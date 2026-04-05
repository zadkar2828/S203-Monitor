import json
import os
import requests
from datetime import datetime

# ── Config ────────────────────────────────────────────────────────────────────
SHOP_URL        = "https://sonic203.com/products.json?limit=250"
STATE_FILE      = "known_products.json"
TELEGRAM_TOKEN  = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
# ──────────────────────────────────────────────────────────────────────────────


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
    print(f"[Telegram] Message sent.")


def fetch_products() -> list[dict]:
    """Fetch all products from Shopify products.json (handles pagination)."""
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


def load_known() -> dict:
    """Load previously seen product handles from state file."""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_known(known: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(known, f, indent=2)


def main():
    print(f"[{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}] Checking sonic203.com for new products...")

    products = fetch_products()
    print(f"  Found {len(products)} total products on site.")

    known = load_known()
    new_products = []

    for p in products:
        handle = p.get("handle")
        if handle and handle not in known:
            new_products.append(p)
            known[handle] = {
                "title": p.get("title"),
                "first_seen": datetime.utcnow().isoformat()
            }

    if new_products:
        for p in new_products:
            title     = p.get("title", "Unknown Product")
            handle    = p.get("handle", "")
            price     = p.get("variants", [{}])[0].get("price", "?")
            prod_url  = f"https://sonic203.com/products/{handle}"

            msg = (
                f"🆕 <b>New Product on Sonic203!</b>\n\n"
                f"📦 <b>{title}</b>\n"
                f"💵 Price: <b>${price}</b>\n"
                f"🔗 {prod_url}"
            )
            send_telegram(msg)
            print(f"  [NEW] {title} — ${price}")
    else:
        print("  No new products found.")

    save_known(known)
    print("  State saved.")


if __name__ == "__main__":
    main()
