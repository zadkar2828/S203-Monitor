import json
import os
import re
import requests
from datetime import datetime

# ── Config ─────────────────────────────────────────────────────────────────────
SHOP_URL          = "https://sonic203.com/products.json?limit=250"
INSTAGRAM_USER    = "sonic203"
STATE_FILE        = "monitor_state.json"
LOW_STOCK_THRESH  = 20
TELEGRAM_TOKEN    = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID  = os.environ["TELEGRAM_CHAT_ID"]
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
        "ig_seen_post_ids": []
    }

def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ── Shopify ────────────────────────────────────────────────────────────────────
def fetch_products() -> list[dict]:
    products = []
    page = 1
    while True:
        url = f"{SHOP_URL}&page={page}"
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        data = resp.json().get("products", [])
        if not data:
            break
        products.extend(data)
        if len(data) < 250:
            break
        page += 1
    return products

def check_products(state: dict):
    products = fetch_products()
    print(f"  Found {len(products)} products on site.")

    known          = state["products"]
    low_alerted    = state["low_stock_alerted"]
    sold_alerted   = state["sold_out_alerted"]

    for p in products:
        handle     = p.get("handle", "")
        title      = p.get("title", "Unknown")
        variant    = p.get("variants", [{}])[0]
        price      = variant.get("price", "?")
        variant_id = variant.get("id", "")
        inventory  = variant.get("inventory_quantity", None)
        available  = variant.get("available", True)
        prod_url   = f"https://sonic203.com/products/{handle}"
        cart_url   = f"https://sonic203.com/cart/{variant_id}:10" if variant_id else prod_url

        # ── New product ──────────────────────────────────────────────────────
        if handle and handle not in known:
            known[handle] = {
                "title": title,
                "first_seen": datetime.utcnow().isoformat()
            }
            msg = (
                f"🆕 <b>New Product on Sonic203!</b>\n\n"
                f"📦 <b>{title}</b>\n"
                f"💵 Price: <b>${price}</b>\n\n"
                f"🔗 <a href='{prod_url}'>View Product</a>\n"
                f"🛒 <a href='{cart_url}'>Add 10 to Cart &amp; Checkout</a>"
            )
            send_telegram(msg)
            print(f"  [NEW] {title}")

        # ── Low stock ────────────────────────────────────────────────────────
        if (
            inventory is not None
            and 0 < inventory <= LOW_STOCK_THRESH
            and handle not in low_alerted
        ):
            low_alerted.append(handle)
            msg = (
                f"⚠️ <b>Low Stock Alert — Sonic203!</b>\n\n"
                f"📦 <b>{title}</b>\n"
                f"💵 Price: <b>${price}</b>\n"
                f"🔢 Only <b>{inventory} entries left!</b>\n\n"
                f"🔗 <a href='{prod_url}'>View Product</a>\n"
                f"🛒 <a href='{cart_url}'>Add 10 to Cart &amp; Checkout</a>"
            )
            send_telegram(msg)
            print(f"  [LOW STOCK] {title} — {inventory} left")

        # ── Sold out ─────────────────────────────────────────────────────────
        if (
            not available
            and handle not in sold_alerted
            and handle in known
        ):
            sold_alerted.append(handle)
            if handle in low_alerted:
                low_alerted.remove(handle)
            msg = (
                f"🔴 <b>SOLD OUT — Sonic203!</b>\n\n"
                f"📦 <b>{title}</b>\n\n"
                f"Entries are closed. Watch for the Airtable link and live draw! 🎰\n\n"
                f"🔗 <a href='{prod_url}'>Product Page</a>"
            )
            send_telegram(msg)
            print(f"  [SOLD OUT] {title}")

        # ── Restock reset ────────────────────────────────────────────────────
        if available and handle in sold_alerted:
            sold_alerted.remove(handle)
            if handle in low_alerted:
                low_alerted.remove(handle)
            print(f"  [RESTOCKED] {title} — alerts reset")

    state["products"]          = known
    state["low_stock_alerted"] = low_alerted
    state["sold_out_alerted"]  = sold_alerted


# ── Instagram ──────────────────────────────────────────────────────────────────
def check_instagram(state: dict):
    print(f"  Checking Instagram @{INSTAGRAM_USER} for Airtable links...")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }

    seen_ids = state.get("ig_seen_post_ids", [])

    try:
        url  = f"https://www.instagram.com/{INSTAGRAM_USER}/"
        resp = requests.get(url, headers=headers, timeout=15)

        # Search raw page source for any airtable.com links
        airtable_links = re.findall(
            r'https?://(?:www\.)?airtable\.com/[^\s\"\'\\\u003e]+',
            resp.text
        )

        if airtable_links:
            for link in set(airtable_links):
                link_id = link[:100]
                if link_id not in seen_ids:
                    seen_ids.append(link_id)
                    msg = (
                        f"🎰 <b>Airtable Entry List Posted!</b>\n\n"
                        f"Sonic203 just posted the entry list. "
                        f"Go check your entries are in there!\n\n"
                        f"📋 <a href='{link}'>Open Airtable List</a>\n\n"
                        f"🏁 Draw is happening soon — watch the live!"
                    )
                    send_telegram(msg)
                    print(f"  [AIRTABLE] Link found: {link}")
        else:
            print("  No Airtable links found on Instagram page.")

    except Exception as e:
        print(f"  [Instagram] Error: {e} — skipping this check.")

    state["ig_seen_post_ids"] = seen_ids[-50:]


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    print(f"[{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}] Running Sonic203 Monitor...")

    state = load_state()

    print("\n[1/2] Checking products...")
    check_products(state)

    print("\n[2/2] Checking Instagram...")
    check_instagram(state)

    save_state(state)
    print("\nDone. State saved.")


if __name__ == "__main__":
    main()
