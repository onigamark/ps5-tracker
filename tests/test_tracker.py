import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sources  # noqa: E402
import tracker  # noqa: E402
from sources import Listing  # noqa: E402


def L(key="bestbuy:1", name="PlayStation 5 Pro Console", price=1099.99, in_stock=True, first_party=True):
    return Listing(key=key, retailer=key.split(":")[0], name=name, url="https://x/" + key,
                   price=price, in_stock=in_stock, first_party=first_party, seller="s")


CFG = {
    "hunts": [{"name": "PS5 Pro", "query": "q", "include": ["playstation 5 pro"],
               "exclude": ["controller"], "min_price": 350, "max_price": 1150,
               "retailers": ["bestbuy"], "priority": "urgent"}],
    "deal_feed": {"enabled": True, "any_of": ["ps5", "playstation 5"], "exclude": ["headset"]},
    "excluded_conditions": ["refurb", "open box", "used"],
    "blind_after_failures": 3,
}


SOLD_OUT = [L(key="bestbuy:0", in_stock=False)]
RFD_DEFAULT = [{"id": "0", "title": "Xbox deal", "url": "u0"}]
NOON = datetime(2026, 11, 27, 17, 0, tzinfo=timezone.utc)  # 12:00 Toronto


def run(state, listings=None, threads=None, search_error=None, at=NOON):
    def search(r, q):
        if search_error:
            raise search_error
        return SOLD_OUT if listings is None else listings
    return tracker.run_check(CFG, state, {"search": search, "rfd": lambda: threads or RFD_DEFAULT},
                             clock=lambda: at, name="cloud")


class Matching(unittest.TestCase):
    def test_norm_handles_trademarks_accents_and_glued_digits(self):
        self.assertEqual(tracker.norm("PlayStation®5 Pro"), "playstation 5 pro")
        self.assertEqual(tracker.norm("Ghost of Yōtei"), "ghost of yotei")

    def test_whole_words_and_plurals(self):
        self.assertTrue(tracker.has_term("ps5 covers", "cover"))
        self.assertFalse(tracker.has_term("screen protection", "pro"))

    def test_conditions_excluded(self):
        for n in ["Refurbished PlayStation 5 Pro", "Open Box - PlayStation 5 Pro", "Used PlayStation 5 Pro"]:
            self.assertFalse(tracker.matches_name(n, ["playstation 5 pro"], [], CFG["excluded_conditions"]), n)
        self.assertTrue(tracker.matches_name("Focused PlayStation 5 Pro", ["playstation 5 pro"], [], ["used"]))

    def test_buyable_rules(self):
        self.assertTrue(tracker.buyable(L(), 1150, 350))
        self.assertFalse(tracker.buyable(L(first_party=False), 1150, 350), "reseller")
        self.assertFalse(tracker.buyable(L(in_stock=False), 1150, 350))
        self.assertFalse(tracker.buyable(L(price=2599), 1150, 350), "scalper price")
        self.assertFalse(tracker.buyable(L(price=29.99), 1150, 350), "accessory")
        self.assertFalse(tracker.buyable(L(price=None), 1150, 350), "unknown price")


class StateTransitions(unittest.TestCase):
    def test_alert_once_then_quiet(self):
        alerts, st, _ = run({}, [L()])
        self.assertEqual(len(alerts), 1)
        self.assertIn("IN STOCK", alerts[0]["title"])
        self.assertEqual(alerts[0]["priority"], "urgent")
        alerts, st, _ = run(st, [L()])
        self.assertEqual(alerts, [])

    def test_restock_after_sellout_alerts_again(self):
        _, st, _ = run({}, [L()])
        _, st, _ = run(st, [L(in_stock=False)])
        alerts, _, _ = run(st, [L()])
        self.assertEqual(len(alerts), 1)

    def test_price_drop_alerts(self):
        _, st, _ = run({}, [L(price=1099.99)])
        self.assertEqual(run(st, [L(price=1079.99)])[0], [], "small drop is quiet")
        alerts, _, _ = run(st, [L(price=999.99)])
        self.assertIn("PRICE DROP", alerts[0]["title"])

    def test_reseller_and_soldout_never_alert(self):
        alerts, _, _ = run({}, [L(first_party=False), L(key="bestbuy:2", in_stock=False)])
        self.assertEqual(alerts, [])

    def test_store_hiccup_does_not_cause_realert(self):
        _, st, _ = run({}, [L()])
        alerts, st, _ = run(st, search_error=sources.Blocked("captcha"))
        self.assertEqual(alerts, [])
        self.assertIn("bestbuy:1", st["live"])
        alerts, _, _ = run(st, [L()])
        self.assertEqual(alerts, [])

    def test_blind_alert_once_and_recovery(self):
        st = {}
        titles = []
        for _ in range(5):
            a, st, _ = run(st, search_error=sources.Blocked("captcha"))
            titles += [x["title"] for x in a]
        self.assertEqual(titles, ["Tracker is blind on bestbuy"])
        a, st, _ = run(st)
        self.assertEqual([x["title"] for x in a], ["Tracker can see bestbuy again"])

    def test_empty_search_counts_as_failure(self):
        _, st, log = run({}, [])
        self.assertEqual(st["failures"]["bestbuy"], 1)
        self.assertTrue(any("no results" in line for line in log))

    def test_empty_feed_counts_as_failure(self):
        _, st, _ = tracker.run_check(CFG, {}, {"search": lambda r, q: SOLD_OUT, "rfd": lambda: []}, clock=lambda: NOON)
        self.assertEqual(st["failures"]["rfd"], 1)


class Heartbeat(unittest.TestCase):
    def hb(self, alerts):
        return [a for a in alerts if a["priority"] == "min"]

    def test_daily_after_9am_local_once(self):
        a, st, _ = run({})                       # day 1 noon: first run, no heartbeat
        self.assertEqual(self.hb(a), [])
        for i in range(5):
            a, st, _ = run(st, at=NOON + timedelta(minutes=5 * i))
            self.assertEqual(self.hb(a), [])
        early = datetime(2026, 11, 28, 13, 0, tzinfo=timezone.utc)   # day 2, 8:00 Toronto
        a, st, _ = run(st, at=early)
        self.assertEqual(self.hb(a), [])
        a, st, _ = run(st, at=early + timedelta(hours=1, minutes=5))  # 9:05 Toronto
        [h] = self.hb(a)
        self.assertIn("cloud: still watching (8 checks, all stores OK)", h["title"])
        a, st, _ = run(st, at=early + timedelta(hours=2))
        self.assertEqual(self.hb(a), [], "only once a day")

    def test_reports_failures(self):
        _, st, _ = run({})
        _, st, _ = run(st, search_error=sources.Blocked("x"))
        a, _, _ = run(st, at=NOON + timedelta(days=1))
        [h] = self.hb(a)
        self.assertIn("some checks failed", h["title"])
        self.assertIn("bestbuy ⚠ 2/3", h["message"])

    def test_heartbeat_is_push_only(self):
        self.assertLess(tracker.RANK["min"], tracker.RANK["high"])


class SourceFilter(unittest.TestCase):
    def test_only_skips_other_sources_without_counting_failures(self):
        calls = []
        def search(r, q):
            calls.append(r)
            raise sources.Blocked("x")
        _, st, _ = tracker.run_check(CFG, {}, {"search": search, "rfd": lambda: []}, only={"rfd"})
        self.assertEqual(calls, [])
        self.assertNotIn("bestbuy", st["failures"])


class PushPlan(unittest.TestCase):
    def test_many_alerts_fold_into_summary(self):
        alerts = [{"title": f"a{i}", "message": "", "priority": "high"} for i in range(16)]
        alerts.append({"title": "PRO", "message": "", "priority": "urgent"})
        plan = tracker.push_plan(alerts)
        self.assertEqual(len(plan), 4)
        self.assertEqual(plan[0]["title"], "PRO")
        self.assertEqual(plan[-1]["title"], "+14 more PS5 alerts")

    def test_few_alerts_sent_individually(self):
        alerts = [{"title": f"a{i}", "message": "", "priority": "high"} for i in range(4)]
        self.assertEqual(len(tracker.push_plan(alerts)), 4)


class DealFeed(unittest.TestCase):
    T = [{"id": "1", "title": "[Best Buy] PS5 Slim $499", "url": "u1"}]

    def test_first_run_seeds_silently_then_alerts_new(self):
        alerts, st, _ = run({}, threads=self.T)
        self.assertEqual(alerts, [])
        new = self.T + [{"id": "2", "title": "[Walmart] PlayStation 5 Pro bundle $999", "url": "u2"},
                        {"id": "3", "title": "PS5 Pulse headset $99", "url": "u3"},
                        {"id": "4", "title": "Xbox Series X $399", "url": "u4"}]
        alerts, st, _ = run(st, threads=new)
        self.assertEqual([a["url"] for a in alerts], ["u2"])
        self.assertEqual(run(st, threads=new)[0], [])
        self.assertNotIn("bestbuy", [k for k, v in st["failures"].items() if v])


class Parsers(unittest.TestCase):
    def test_bestbuy(self):
        products = sources.parse_bestbuy_search(json.dumps({"products": [
            {"sku": 1, "name": "PlayStation 5 Pro Console", "salePrice": 1099.99, "isMarketplace": False,
             "productUrl": "/en-ca/product/x/1"}]}))
        avail = sources.parse_bestbuy_availability("﻿" .lstrip() + json.dumps({"availabilities": [
            {"sku": "1", "shipping": {"status": "SoldOutOnline", "purchasable": False}, "pickup": {}}]}))
        self.assertEqual(products[0]["sku"], 1)
        self.assertEqual(avail["1"], (False, "SoldOutOnline"))

    def test_walmart_marks_external_sellers(self):
        data = {"props": {"items": [
            {"usItemId": "A", "name": "PlayStation®5 Pro Console", "priceInfo": {"linePrice": "$2,572.89"},
             "availabilityStatusV2": {"value": "IN_STOCK"}, "sellerName": "always innovating",
             "sellerType": "EXTERNAL", "canonicalUrl": "/en/ip/a/A?x=1"},
            {"usItemId": "B", "name": "PlayStation®5 Digital Edition", "priceInfo": {"linePrice": "$549.99"},
             "availabilityStatusV2": {"value": "IN_STOCK"}, "sellerName": "Walmart", "sellerType": "INTERNAL",
             "canonicalUrl": "/en/ip/b/B"}]}}
        page = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'
        a, b = sources.parse_walmart_search(page)
        self.assertEqual((a.first_party, a.price, a.in_stock), (False, 2572.89, True))
        self.assertEqual((b.first_party, b.url), (True, "https://www.walmart.ca/en/ip/b/B"))

    def test_walmart_bot_wall(self):
        with self.assertRaises(sources.Blocked):
            sources.parse_walmart_search("<html>captcha</html>")

    NAV = '<a aria-describedby="nav-assist" class="add-to-cart-button">Add to cart</a>'

    def test_amazon_shortcut_hint_is_not_a_buy_box(self):
        page = self.NAV + '<div id="availability"> Currently unavailable. </div>'
        l = sources.parse_product_page(page, "amazon", "https://www.amazon.ca/dp/X", "PS5 Pro")
        self.assertFalse(l.in_stock)

    def test_amazon_in_stock_sold_by_amazon(self):
        page = (self.NAV + '<div data-feature-name="apex_desktop"><span class="a-offscreen">$1,099.99</span></div>'
                '<div id="availability"> In Stock </div><input id="add-to-cart-button" type="submit">'
                '<span class="offer-display-feature-text-message">Amazon.ca</span>')
        l = sources.parse_product_page(page, "amazon", "https://www.amazon.ca/dp/X", "PS5 Pro")
        self.assertEqual((l.in_stock, l.price, l.first_party), (True, 1099.99, True))

    def test_amazon_third_party_seller(self):
        page = ('<div id="availability"> In Stock </div><input id="add-to-cart-button">'
                '<span class="offer-display-feature-text-message">GameFlipper</span>')
        l = sources.parse_product_page(page, "amazon", "https://www.amazon.ca/dp/X", "PS5 Pro")
        self.assertFalse(l.first_party)

    def test_json_ld_product(self):
        ld = {"@type": "Product", "offers": {"price": "1099.99", "availability": "https://schema.org/InStock"}}
        page = f'<script type="application/ld+json">{json.dumps(ld)}</script>'
        l = sources.parse_product_page(page, "ebgames", "https://www.ebgames.ca/x", "PS5 Pro")
        self.assertEqual((l.in_stock, l.price, l.first_party), (True, 1099.99, True))

    def test_bot_page_raises_blocked(self):
        with self.assertRaises(sources.Blocked):
            sources.parse_product_page("<html>Please complete the CAPTCHA</html>", "costco", "https://c/x", "x")

    def test_rfd_dedupes_threads_and_rejects_dtd(self):
        feed = ('<feed xmlns="http://www.w3.org/2005/Atom">'
                '<entry><title>[BB] PS5 $499</title><link href="https://f/viewtopic.php?t=9&amp;p=1"/></entry>'
                '<entry><title>[BB] PS5 $499</title><link href="https://f/viewtopic.php?t=9&amp;p=2"/></entry>'
                '</feed>')
        self.assertEqual(len(sources.parse_rfd(feed)), 1)
        with self.assertRaises(ValueError):
            sources.parse_rfd('<!DOCTYPE x [<!ENTITY a "b">]><feed/>')


if __name__ == "__main__":
    unittest.main()
