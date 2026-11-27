# PS5 tracker (Canada)

Watches Canadian stores for a PS5 at a real price and pings your phone the moment one is buyable.
**It alerts; you buy.** No auto-checkout: retailers cancel bot orders and ban the accounts, and the
slow part of a drop is noticing, not clicking.

## What it watches

| Source | How | Reliability |
|---|---|---|
| **Best Buy CA** | Public JSON search + stock API | Solid |
| **Walmart CA** | Search page product data | Good; may hit bot checks from cloud IPs |
| **Amazon.ca** | Specific product pages (`watches`) | Best effort; may hit bot checks |
| **RedFlagDeals Hot Deals** | Feed of new deal threads | Solid. Covers stores we can't check directly (EB Games, Costco, PlayStation Direct, The Source) because Canadians post drops there within minutes |
| EB Games, Costco, PlayStation Direct | Add a `watches` entry with the product URL | These block automated requests today; you'll get a "tracker is blind" alert if so |

**The scalper filter.** Most "in stock" PS5s online are marketplace resellers at 1.5–2.5× retail
(PS5 Pro for $2,500+). A listing only alerts when it's **sold by the store itself**, **in stock**, and
priced between the hunt's `min_price` and `max_price`. Refurbished/open-box/used are skipped.

## Alerts

- **Phone push (ntfy):** urgent priority, so it breaks through Do Not Disturb if you allow it.
  Tap it and the store page opens.
- **Email (Gmail):** a copy of every high or urgent alert.
- Alerts fire only on **changes**: newly buyable, price dropped 5%+, a new matching RFD thread,
  or a store started/stopped blocking the tracker. A sold-out → back-in-stock restock alerts again.

## Setup (about 10 minutes)

1. **ntfy:** install the ntfy app (iOS/Android). Subscribe to a topic name nobody could guess,
   e.g. `ps5-` + 16 random letters. Anyone who knows the name can read it, so treat it like a password.
2. **Gmail (optional):** turn on 2-Step Verification, then create an App Password
   (Google Account → Security → App passwords).
3. **GitHub repo secrets** (Settings → Secrets and variables → Actions):
   `NTFY_TOPIC`, and optionally `GMAIL_USER`, `GMAIL_APP_PASSWORD`, `ALERT_EMAIL_TO`.
4. **Test:** Actions → *track* → Run workflow → tick "Send a test push/email".
5. Done. It checks every ~5 minutes. The repo should be **public**: scheduled jobs on a private repo
   exhaust the free 2,000 Actions minutes in about a week. Nothing secret lives in the code; secrets
   stay in GitHub secrets.

## Tuning the watchlist

Edit `watchlist.json` and push. Each **hunt** searches Best Buy/Walmart:

- `include`: every word must be in the product name. `exclude`: none may be.
- `min_price` keeps out accessories and games that say "PlayStation 5". `max_price` is your ceiling.
- `priority`: `urgent` (loud), `high`, `default`, `low`.

Each **watch** is one product page (`retailer`, `url`, `min_price`, `max_price`).

## Run it on your Mac too

GitHub's schedule can run late during busy periods. On a big launch day, run it locally every 45s as well:

```sh
export NTFY_TOPIC=your-topic
python3 tracker.py --dry-run     # see what it sees, no alerts, no state saved
python3 tracker.py --loop 45     # check every 45 seconds
```

Local and cloud runs keep separate state, so both may alert for the same drop. That's harmless.

## Be ready to buy in under a minute

The tracker buys you minutes; these buy you seconds:
- Be **signed in** at Best Buy, Walmart and Amazon on your phone, ideally in their apps.
- **Saved card and address** at each store. Use Apple Pay / PayPal where offered.
- Turn on Best Buy's own "notify me" and the PlayStation Direct queue emails as backups.
- Don't refresh a queue page; you lose your place.

## Tests

```sh
python3 -m unittest discover -s tests
```
