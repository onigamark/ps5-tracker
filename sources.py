"""Retailer adapters. Each returns a list of Listing, or raises Blocked/other errors.

A Listing is one product offer at one retailer, normalized so the matcher in
tracker.py does not care where it came from.
"""
import gzip
import html
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import List, Optional

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


class Blocked(Exception):
    """The retailer answered, but with a bot wall / captcha instead of data."""


@dataclass
class Listing:
    key: str               # stable id, e.g. "bestbuy:19492009"
    retailer: str
    name: str
    url: str
    price: Optional[float]
    in_stock: bool
    first_party: bool      # sold by the retailer itself, not a marketplace reseller
    seller: str = ""
    notes: List[str] = field(default_factory=list)


def fetch(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-CA,en;q=0.9",
        "Accept-Encoding": "gzip",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
    return body.decode("utf-8", "replace").lstrip("﻿")


def _money(v) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"[\d,]+(?:\.\d+)?", str(v))
    return float(m.group(0).replace(",", "")) if m else None


# --- Best Buy Canada ---------------------------------------------------------

BB_SEARCH = "https://www.bestbuy.ca/api/v2/json/search?lang=en-CA&pageSize=40&query={q}"
BB_AVAIL = ("https://www.bestbuy.ca/ecomm-api/availability/products?"
            "accept=application%2Fvnd.bestbuy.standardproduct.v1%2Bjson"
            "&accept-language=en-CA&skus={skus}")


def parse_bestbuy_search(text: str) -> List[dict]:
    return json.loads(text.lstrip("\ufeff")).get("products", [])


def parse_bestbuy_availability(text: str) -> dict:
    """sku -> (purchasable_online, status string)."""
    out = {}
    for a in json.loads(text.lstrip("\ufeff")).get("availabilities", []):
        ship = a.get("shipping", {})
        out[str(a["sku"])] = (bool(ship.get("purchasable")), ship.get("status", ""))
    return out


def bestbuy(query: str) -> List[Listing]:
    products = parse_bestbuy_search(fetch(BB_SEARCH.format(q=urllib.parse.quote(query))))
    if not products:
        return []
    skus = "|".join(str(p["sku"]) for p in products)
    avail = parse_bestbuy_availability(fetch(BB_AVAIL.format(skus=urllib.parse.quote(skus))))
    listings = []
    for p in products:
        sku = str(p["sku"])
        buyable, status = avail.get(sku, (False, "Unknown"))
        seller = (p.get("seller") or {}).get("name") or "Best Buy"
        listings.append(Listing(
            key=f"bestbuy:{sku}", retailer="Best Buy", name=p.get("name", ""),
            url="https://www.bestbuy.ca" + p.get("productUrl", f"/en-ca/product/{sku}"),
            price=_money(p.get("salePrice") or p.get("regularPrice")),
            in_stock=buyable, first_party=not p.get("isMarketplace"),
            seller=seller, notes=[status],
        ))
    return listings


# --- Walmart Canada ----------------------------------------------------------

WM_SEARCH = "https://www.walmart.ca/en/search?q={q}"


def _walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)


def parse_walmart_search(page: str) -> List[Listing]:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if not m:
        raise Blocked("walmart: no product data in page (bot check?)")
    data = json.loads(m.group(1))
    seen, out = set(), []
    for o in _walk(data):
        item_id = o.get("usItemId")
        if not item_id or "name" not in o or "priceInfo" not in o or item_id in seen:
            continue
        seen.add(item_id)
        status = (o.get("availabilityStatusV2") or {}).get("value") or ""
        seller = o.get("sellerName") or ""
        first_party = o.get("sellerType") != "EXTERNAL" and seller.lower() in ("", "walmart", "walmart.ca")
        price = _money((o.get("priceInfo") or {}).get("linePrice") or o.get("price"))
        url = o.get("canonicalUrl") or f"/en/ip/{item_id}"
        out.append(Listing(
            key=f"walmart:{item_id}", retailer="Walmart", name=o["name"],
            url=urllib.parse.urljoin("https://www.walmart.ca", url.split("?")[0]),
            price=price, in_stock=(status == "IN_STOCK" and not o.get("isOutOfStock")),
            first_party=first_party, seller=seller or "Walmart", notes=[status],
        ))
    return out


def walmart(query: str) -> List[Listing]:
    return parse_walmart_search(fetch(WM_SEARCH.format(q=urllib.parse.quote(query))))


# --- Single product pages (Amazon, PS Direct, EB Games, Costco ...) ----------

BOT_MARKERS = ("captcha", "are you a human", "access denied", "robot check", "px-captcha")


def parse_product_page(page: str, retailer: str, url: str, name: str) -> Listing:
    low = page.lower()
    price, in_stock, seller = None, None, ""

    # 1. schema.org JSON-LD offers: the most portable signal.
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.S):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        for o in _walk(data):
            if "availability" in o and ("price" in o or "lowPrice" in o):
                in_stock = "instock" in str(o["availability"]).lower().replace(" ", "")
                price = _money(o.get("price") or o.get("lowPrice"))
                seller = ((o.get("seller") or {}).get("name") if isinstance(o.get("seller"), dict) else "") or ""
                break
        if in_stock is not None:
            break

    # 2. Amazon has no JSON-LD; read its buy box.
    if in_stock is None and "amazon." in url:
        return _parse_amazon(page, retailer, url, name)

    if in_stock is None:
        if any(b in low for b in BOT_MARKERS):
            raise Blocked(f"{retailer}: bot check page")
        raise Blocked(f"{retailer}: could not find stock info on page")

    # Unknown seller counts as the store itself; otherwise the store's name must appear.
    first_party = not seller or retailer.lower() in seller.lower()
    return Listing(key=f"{retailer}:{url}", retailer=retailer.title(), name=name, url=url,
                   price=price, in_stock=in_stock, first_party=first_party, seller=seller)


def _parse_amazon(page: str, retailer: str, url: str, name: str) -> Listing:
    # Note: the text "add-to-cart-button" appears on every page (keyboard-shortcut help),
    # so only a real <input id="add-to-cart-button"> counts as a buy box.
    has_buy_box = re.search(r'<input[^>]{0,300}id="(add-to-cart-button|buy-now-button)"', page) is not None
    i = page.find('id="availability"')
    avail = re.sub(r"<[^>]+>", " ", page[i:i + 4000]).lower() if i >= 0 else ""
    if not has_buy_box and i < 0:
        if any(b in page.lower() for b in BOT_MARKERS):
            raise Blocked(f"{retailer}: bot check page")
        raise Blocked(f"{retailer}: no buy box or availability on page")
    in_stock = has_buy_box and "currently unavailable" not in avail

    price = None
    j = page.find('data-feature-name="apex_desktop"')
    if j >= 0:
        m = re.search(r'class="a-offscreen">\s*\$([\d,]+\.\d\d)', page[j:j + 30000])
        price = _money(m.group(1)) if m else None

    # Buy box seller: first "offer-display-feature-text-message" is Ships from/Sold by.
    m = re.search(r'offer-display-feature-text-message"[^>]*>\s*([^<]+?)\s*<', page)
    seller = html.unescape(m.group(1)) if m else ""
    # Amazon is full of resellers, so an unknown seller does NOT count as Amazon.
    return Listing(key=f"{retailer}:{url}", retailer="Amazon", name=name, url=url, price=price,
                   in_stock=in_stock, first_party="amazon" in seller.lower(), seller=seller or "unknown")


def product_page(retailer: str, url: str, name: str) -> Listing:
    return parse_product_page(fetch(url), retailer, url, name)


# --- RedFlagDeals Hot Deals feed --------------------------------------------

RFD_FEED = "https://forums.redflagdeals.com/feed/forum/9"
ATOM = "{http://www.w3.org/2005/Atom}"


def parse_rfd(xml_text: str) -> List[dict]:
    """One entry per thread (the feed repeats threads for each new reply)."""
    if "<!doctype" in xml_text[:2000].lower() or "<!entity" in xml_text.lower():
        raise ValueError("rfd: refusing XML with DTD/entities")
    root = ET.fromstring(xml_text)
    threads = {}
    for e in root.findall(f"{ATOM}entry"):
        link = e.find(f"{ATOM}link").get("href", "")
        m = re.search(r"[?&]t=(\d+)", link)
        if not m:
            continue
        tid = m.group(1)
        threads.setdefault(tid, {
            "id": tid,
            "title": html.unescape(e.findtext(f"{ATOM}title") or "").strip(),
            "url": f"https://forums.redflagdeals.com/viewtopic.php?t={tid}",
        })
    return list(threads.values())


def rfd() -> List[dict]:
    return parse_rfd(fetch(RFD_FEED))
