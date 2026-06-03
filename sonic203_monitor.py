import json
import os
import re
import time
import requests
from datetime import datetime

# ── Config ─────────────────────────────────────────────────────────────────────
SHOPS = [
    {"label": "Sonic203",      "url": "https://sonic203.com/products.json?limit=250",      "site": "sonic203.com"},
    {"label": "Sonic203 Parts","url": "https://sonic203parts.com/products.json?limit=250",  "site": "sonic203parts.com"},
    {"label": "Fast Parts US", "url": "https://www.fastpartsus.com/products.json?limit=250","site": "www.fastpartsus.com"},
]

IG_ACCOUNTS = [
    {
        "user":     "sonic203",
        "label":    "Sonic203",
        "live_page":"https://www.instagram.com/sonic203ct/",
        "list_url": "https://s203list.com",
        "state_bio_key":   "ig_last_bio_url_sonic203",
        "state_seen_key":  "ig_seen_post_ids_sonic203",
    },
    {
        "user":     "type_r_jose",
        "label":    "Type R Jose",
        "live_page":"https://www.instagram.com/type_r_jose/",
        "list_url": "https://airtable.com",
        "state_bio_key":   "ig_last_bio_url_jose",
        "state_seen_key":  "ig_seen_post_ids_jose",
    },
]

STATE_FILE       = "monitor_state.json"
LOW_STOCK_THRESH = 20
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]WINNERS_TRIGGERS   = ["results", "the winners", "1st place", "winner is", "we have a winner"]
# ───────────────────────────────────────────────────────────────────────────────


# ── Telegram ───────────────────────────────────────────────────────────────────
def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    resp = requests.post(url, json=payload, timeout=10)
    resp.raise_for_status()
    print(f"  [Telegram] Sent: {message[:60]}...")


# ── State ──────────────────────────────────────────────────────────────────────
def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        "products": {},
        "low_stock_alerted": [],
        "sold_out_alerted": [],
        "ig_seen_post_ids_sonic203": [],
        "ig_seen_post_ids_jose": [],
        "ig_last_bio_url_sonic203": "",
        "ig_last_bio_url_jose": "",
    }

def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ── Shopify ────────────────────────────────────────────────────────────────────
def fetch_products(shop_url: str) -> list[dict]:
    products = []
    page = 1
    while True:
        url = f"{shop_url}&page={page}"
        for attempt in range(3):
            try:
                resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
                resp.raise_for_status()
                break
            except requests.exceptions.HTTPError as e:
                if attempt < 2:
                    wait = 15 * (attempt + 1)
                    print(f"  [Retry] {e} — waiting {wait}s before retry {attempt + 2}/3...")
                    time.sleep(wait)
                else:
                    print(f"  [Skip] {shop_url} unreachable after 3 attempts — skipping.")
                    return products
        data = resp.json().get("products", [])
        if not data:
            break
        products.extend(data)
        if len(data) < 250:
            break
        page += 1
    return products

def check_products(state: dict):
    known        = state["products"]
    low_alerted  = state["low_stock_alerted"]
    sold_alerted = state["sold_out_alerted"]

    for shop in SHOPS:
        label     = shop["label"]
        shop_url  = shop["url"]
        site      = shop["site"]

        products = fetch_products(shop_url)
        print(f"  Found {len(products)} products on {label}.")

        for p in products:
            handle     = f"{site}::{p.get('handle', '')}"  # namespaced to avoid cross-site collisions
            raw_handle = p.get("handle", "")
            title      = p.get("title", "Unknown")
            variant    = p.get("variants", [{}])[0]
            price      = variant.get("price", "?")
            variant_id = variant.get("id", "")
            inventory  = variant.get("inventory_quantity", None)
            available  = variant.get("available", True)
            prod_url   = f"https://{site}/products/{raw_handle}"
            cart_url   = f"https://{site}/cart/{variant_id}:10" if variant_id else prod_url

            # ── New product ──────────────────────────────────────────────────
            if handle and handle not in known:
                known[handle] = {
                    "title": title,
                    "first_seen": datetime.utcnow().isoformat()
                }
                msg = (
                    f"🆕 <b>New Product — {label}!</b>\n\n"
                    f"📦 <b>{title}</b>\n"
                    f"💵 Price: <b>${price}</b>\n\n"
                    f"🔗 <a href='{prod_url}'>View Product</a>\n"
                    f"🛒 <a href='{cart_url}'>Add 10 to Cart &amp; Checkout</a>"
                )
                send_telegram(msg)
                print(f"  [NEW] [{label}] {title}")

            # ── Low stock ────────────────────────────────────────────────────
            if (
                inventory is not None
                and 0 < inventory <= LOW_STOCK_THRESH
                and handle not in low_alerted
            ):
                low_alerted.append(handle)
                msg = (
                    f"⚠️ <b>Low Stock — {label}!</b>\n\n"
                    f"📦 <b>{title}</b>\n"
                    f"💵 Price: <b>${price}</b>\n"
                    f"🔢 Only <b>{inventory} entries left!</b>\n\n"
                    f"🔗 <a href='{prod_url}'>View Product</a>\n"
                    f"🛒 <a href='{cart_url}'>Add 10 to Cart &amp; Checkout</a>"
                )
                send_telegram(msg)
                print(f"  [LOW STOCK] [{label}] {title} — {inventory} left")

            # ── Sold out ─────────────────────────────────────────────────────
            if (
                not available
                and handle not in sold_alerted
                and handle in known
            ):
                sold_alerted.append(handle)
                if handle in low_alerted:
                    low_alerted.remove(handle)
                msg = (
                    f"🔴 <b>SOLD OUT — {label}!</b>\n\n"
                    f"📦 <b>{title}</b>\n\n"
                    f"Entries are closed. Watch for the list and live draw! 🎰\n\n"
                    f"🔗 <a href='{prod_url}'>Product Page</a>"
                )
                send_telegram(msg)
                print(f"  [SOLD OUT] [{label}] {title}")

            # ── Restock reset ────────────────────────────────────────────────
            if available and handle in sold_alerted:
                sold_alerted.remove(handle)
                if handle in low_alerted:
                    low_alerted.remove(handle)
                print(f"  [RESTOCKED] [{label}] {title} — alerts reset")

    state["products"]          = known
    state["low_stock_alerted"] = low_alerted
    state["sold_out_alerted"]  = sold_alerted


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    print(f"[{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}] Running Monitor...")

    state = load_state()

    print("\nChecking products...")
    check_products(state)

    save_state(state)
    print("\nDone. State saved.")


if __name__ == "__main__":
    main()
