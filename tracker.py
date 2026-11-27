#!/usr/bin/env python3
"""PS5 stock & deal tracker.

Checks every hunt/watch in watchlist.json, compares with the last run's state,
and alerts only on changes: something became buyable, its price dropped, a new
matching RedFlagDeals thread appeared, or a store started/stopped blocking us.

    python3 tracker.py               # one check, sends alerts
    python3 tracker.py --dry-run     # one check, prints alerts instead
    python3 tracker.py --loop 60     # check every 60s (run on your Mac)
    python3 tracker.py --test-alert  # send a test push/email
"""
import argparse
import json
import os
import re
import sys
import time
import traceback
import unicodedata
from datetime import datetime, timezone
from typing import Dict, List, Tuple

import notify
import sources

HERE = os.path.dirname(os.path.abspath(__file__))
PRICE_DROP_PCT = 5  # re-alert on an already-live listing if it gets this much cheaper
RANK = {"low": 0, "default": 1, "high": 2, "urgent": 3}


# --- Matching ----------------------------------------------------------------

def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9.$ ]+", " ", s)
    s = re.sub(r"(?<=[a-z])(?=\d)", " ", s)  # "playstation5" -> "playstation 5"
    return re.sub(r"\s+", " ", s).strip()


def has_term(text: str, term: str) -> bool:
    """Whole-word match, tolerating plurals: 'cover' matches 'covers', 'pro' not 'protection'."""
    return re.search(r"\b" + re.escape(norm(term)) + r"(s|es)?\b", text) is not None


def matches_name(name: str, include: List[str], exclude: List[str], conditions: List[str]) -> bool:
    n = norm(name)
    if any(re.search(r"\b" + re.escape(norm(c)), n) for c in conditions):
        return False
    return all(has_term(n, t) for t in include) and not any(has_term(n, t) for t in exclude)


def buyable(l: sources.Listing, max_price, min_price=None) -> bool:
    if not (l.in_stock and l.first_party):
        return False
    if min_price is not None and (l.price is None or l.price < min_price):
        return False  # accessories and games that happen to say "PlayStation 5"
    return max_price is None or (l.price is not None and l.price <= max_price)


def title_matches(title: str, any_of: List[str], exclude: List[str]) -> bool:
    t = norm(title)
    return any(has_term(t, w) for w in any_of) and not any(has_term(t, w) for w in exclude)


# --- State -------------------------------------------------------------------

def load_json(path: str, default):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


# --- One check ---------------------------------------------------------------

SEARCHERS = {"bestbuy": sources.bestbuy, "walmart": sources.walmart}


def run_check(cfg: dict, state: dict, fetchers=None, only=None) -> Tuple[List[dict], dict, List[str]]:
    """Returns (alerts, new_state, log_lines). Pure apart from `fetchers` (injectable for tests)."""
    fetchers = fetchers or {}
    use = (lambda src: True) if not only else (lambda src: src in only)  # e.g. {"bestbuy", "rfd"}
    search = fetchers.get("search") or (lambda r, q: SEARCHERS[r](q))
    page = fetchers.get("page") or sources.product_page
    feed = fetchers.get("rfd") or sources.rfd

    conditions = cfg.get("excluded_conditions", [])
    prev_live: Dict[str, dict] = state.get("live", {})
    failures: Dict[str, int] = dict(state.get("failures", {}))
    blind: Dict[str, bool] = dict(state.get("blind", {}))
    blind_after = cfg.get("blind_after_failures", 3)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    live: Dict[str, dict] = {}
    ok_sources, failed_sources, log = set(), {}, []
    cache: Dict[Tuple[str, str], List[sources.Listing]] = {}

    def fetch_search(retailer: str, query: str):
        k = (retailer, query)
        if k not in cache:
            cache[k] = search(retailer, query)
        return cache[k]

    candidates = []  # (hunt-like dict, listing)
    for h in cfg.get("hunts", []):
        for r in h.get("retailers", []):
            if not use(r):
                continue
            try:
                found = fetch_search(r, h.get("query") or h["name"])
                ok_sources.add(r)
            except Exception as e:  # noqa: BLE001 - one store failing must not stop the others
                failed_sources[r] = f"{type(e).__name__}: {e}"
                continue
            for l in found:
                if matches_name(l.name, h.get("include", []), h.get("exclude", []), conditions):
                    candidates.append((h, l))
    for w in cfg.get("watches", []):
        src = w["retailer"]
        if not use(src):
            continue
        try:
            l = page(src, w["url"], w["name"])
            ok_sources.add(src)
            candidates.append((w, l))
        except Exception as e:  # noqa: BLE001
            failed_sources[src] = f"{type(e).__name__}: {e}"

    per_listing: Dict[str, dict] = {}
    for h, l in candidates:
        good = buyable(l, h.get("max_price"), h.get("min_price"))
        log.append(f"{'BUYABLE' if good else '   -   '} {l.retailer:9} "
                   f"{('$%.2f' % l.price) if l.price else '   ?   ':>10} "
                   f"{'stock' if l.in_stock else 'sold-out':8} "
                   f"{'' if l.first_party else '[reseller: ' + l.seller + '] '}{l.name[:70]}  <{h['name']}>")
        if not good:
            continue
        entry = per_listing.setdefault(l.key, {"listing": l, "hunts": [], "priority": "low"})
        entry["hunts"].append(h["name"])
        p = h.get("priority", "default")
        if RANK[p] > RANK[entry["priority"]]:
            entry["priority"] = p

    alerts = []
    for key, e in per_listing.items():
        l = e["listing"]
        before = prev_live.get(key)
        live[key] = {"price": l.price, "since": before["since"] if before else now, "name": l.name}
        if before is None:
            alerts.append(_stock_alert(l, e, "IN STOCK"))
        elif before.get("price") and l.price and l.price <= before["price"] * (1 - PRICE_DROP_PCT / 100):
            alerts.append(_stock_alert(l, e, f"PRICE DROP (was ${before['price']:.2f})"))

    # Keep listings from stores that failed this run, so a hiccup doesn't cause a re-alert.
    for key, v in prev_live.items():
        store = key.split(":", 1)[0]
        if key not in live and store in failed_sources:
            live[key] = v

    # RedFlagDeals feed.
    rfd_cfg = cfg.get("deal_feed", {})
    seen = list(state.get("rfd_seen", []))
    if rfd_cfg.get("enabled") and use("rfd"):
        try:
            threads = feed()
            ok_sources.add("rfd")
            first_run = "rfd_seen" not in state
            for t in threads:
                if t["id"] in seen:
                    continue
                seen.append(t["id"])
                if not first_run and title_matches(t["title"], rfd_cfg.get("any_of", []), rfd_cfg.get("exclude", [])):
                    alerts.append({"title": "RFD deal: " + t["title"][:80], "message": t["title"],
                                   "url": t["url"], "priority": rfd_cfg.get("priority", "high"),
                                   "tags": ["fire"]})
            seen = seen[-1000:]
        except Exception as e:  # noqa: BLE001
            failed_sources["rfd"] = f"{type(e).__name__}: {e}"

    # Source health: one alert when a store goes blind, one when it recovers.
    for s in ok_sources - set(failed_sources):
        failures[s] = 0
        if blind.pop(s, False):
            alerts.append({"title": f"Tracker can see {s} again", "message": f"{s} checks are working again.",
                           "priority": "low", "tags": ["white_check_mark"]})
    for s, err in failed_sources.items():
        failures[s] = failures.get(s, 0) + 1
        log.append(f"FAILED   {s}: {err}")
        if failures[s] >= blind_after and not blind.get(s):
            blind[s] = True
            alerts.append({"title": f"Tracker is blind on {s}",
                           "message": f"{s} has failed {failures[s]} checks in a row ({err[:150]}). "
                                      "Restocks there will be missed until it recovers.",
                           "priority": "default", "tags": ["warning"]})

    new_state = {"live": live, "failures": failures, "blind": blind, "rfd_seen": seen, "last_run": now}
    return alerts, new_state, log


def _stock_alert(l: sources.Listing, e: dict, what: str) -> dict:
    price = f"${l.price:.2f}" if l.price else "price unknown"
    return {"title": f"{what}: {l.name[:60]}",
            "message": f"{l.retailer} · {price} · matches {', '.join(e['hunts'])}\n{l.url}",
            "url": l.url, "priority": e["priority"], "tags": ["video_game", "rotating_light"]}


# --- Delivery & CLI ----------------------------------------------------------

MAX_PUSHES = 3


def push_plan(alerts: List[dict]) -> List[dict]:
    """Most important alerts individually, the rest folded into one summary push."""
    ranked = sorted(alerts, key=lambda a: -RANK[a["priority"]])
    if len(ranked) <= MAX_PUSHES + 1:
        return ranked
    head, rest = ranked[:MAX_PUSHES], ranked[MAX_PUSHES:]
    return head + [{"title": f"+{len(rest)} more PS5 alerts", "priority": rest[0]["priority"],
                    "message": "\n".join("• " + a["title"] for a in rest[:15]), "url": rest[0].get("url", ""),
                    "tags": ["video_game"]}]


def deliver(alerts: List[dict], dry_run: bool) -> None:
    if not alerts:
        return
    if dry_run or not notify.channels_configured():
        if not dry_run:
            print("WARNING: no alert channel configured (set NTFY_TOPIC and/or GMAIL_*)", file=sys.stderr)
        for a in alerts:
            print(f"ALERT [{a['priority']}] {a['title']}\n      {a['message']}")
        return
    for a in push_plan(alerts):
        try:
            notify.ntfy(a["title"], a["message"], a.get("url", ""), a["priority"], a.get("tags", ()))
        except Exception:  # noqa: BLE001
            traceback.print_exc()
    important = [a for a in alerts if RANK[a["priority"]] >= RANK["high"]]
    if important:
        subject = important[0]["title"] if len(important) == 1 else f"{len(important)} PS5 alerts"
        try:
            notify.email(subject, [f"{a['title']}\n{a['message']}" for a in important])
        except Exception:  # noqa: BLE001
            traceback.print_exc()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(HERE, "watchlist.json"))
    ap.add_argument("--state", default=os.environ.get("STATE_FILE", os.path.join(HERE, "state", "state.json")))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--loop", type=int, metavar="SECONDS")
    ap.add_argument("--test-alert", action="store_true")
    ap.add_argument("--sources", default=os.environ.get("TRACKER_SOURCES", ""),
                    help="comma list to check, e.g. bestbuy,rfd (default: everything)")
    ap.add_argument("--quiet", action="store_true", help="only print alerts and failures")
    args = ap.parse_args()

    if args.test_alert:
        chans = notify.channels_configured()
        if not chans:
            print("No channels configured. Set NTFY_TOPIC and/or GMAIL_USER + GMAIL_APP_PASSWORD.")
            return 1
        notify.ntfy("PS5 tracker test", "If you can read this, alerts work.", "https://www.bestbuy.ca", "high", ["tada"])
        notify.email("PS5 tracker test", ["If you can read this, email alerts work."])
        print("Sent test alert via: " + ", ".join(chans))
        return 0

    while True:
        cfg = load_json(args.config, {})
        state = load_json(args.state, {})
        only = {x.strip() for x in args.sources.split(",") if x.strip()} or None
        alerts, state, log = run_check(cfg, state, only=only)
        if args.quiet:
            log = [line for line in log if line.startswith(("BUYABLE", "FAILED"))]
        if log or alerts or not args.quiet:
            print(f"--- {state['last_run']}  {len(alerts)} alert(s)")
            print("\n".join(log), flush=True)
        deliver(alerts, args.dry_run)
        if not args.dry_run:
            save_json(args.state, state)
        if not args.loop:
            return 0
        time.sleep(args.loop)


if __name__ == "__main__":
    sys.exit(main())
