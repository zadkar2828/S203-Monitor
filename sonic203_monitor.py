import json
import os
import time
import requests
from datetime import datetime

# Config
SHOPS = [
    {"label": "Sonic203",       "url": "https://sonic203.com/products.json?limit=250",     "site": "sonic203.com"},
    {"label": "Sonic203 Parts", "url": "https://sonic203parts.com/products.json?limit=250", "site": "sonic203parts.com"},
]

STATE_FILE       = "monitor_state.json"
LOW_STOCK_THRESH = 20
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]


def send_telegram(message):
    url = "https://api.telegram.org/bot" + TELEGRAM_TOKEN + "/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    resp = requests.post(url, json=payload, timeout=10)
    resp.raise_for_status()
    print("  [Telegram] Sent: " + message[:60] + "...")


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        "products": {},
        "low_stock_alerted": [],
        "sold_out_alerted": [],
    }


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def fetch_products(shop_url):
    products = []
    page = 1
    while True:
        url = shop_url + "&page=" + str(page)
        for attempt in range(3):
            try:
                resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
                resp.raise_for_status()
                break
            except requests.exceptions.HTTPError as e:
                if attempt < 2:
                    wait = 15 * (attempt + 1)
                    print("  [Retry] " + str(e) + " -- waiting " + str(wait) + "s...")
                    time.sleep(wait)
                else:
                    print("  [Skip] " + shop_url + " unreachable after 3 attempts.")
                    return products
        data = resp.json().get("products", [])
        if not data:
            break
        products.extend(data)
        if len(data) < 250:
            break
        page += 1
    return products


def check_products(state):
    known        = state["products"]
    low_alerted  = state["low_stock_alerted"]
    sold_alerted = state["sold_out_alerted"]

    for shop in SHOPS:
        label    = shop["label"]
        shop_url = shop["url"]
        site     = shop["site"]

        products = fetch_products(shop_url)
        print("  Found " + str(len(products)) + " products on " + label + ".")

        for p in products:
            raw_handle = p.get("handle", "")
            handle     = site + "::" + raw_handle
            title      = p.get("title", "Unknown")
            variant    = p.get("variants", [{}])[0]
            price      = variant.get("price", "?")
            variant_id = variant.get("id", "")
            inventory  = variant.get("inventory_quantity", None)
            available  = variant.get("available", True)
            prod_url   = "https://" + site + "/products/" + raw_handle
            cart_url   = "https://" + site + "/cart/" + str(variant_id) + ":10" if variant_id else prod_url

            # New product
            if handle and handle not in known:
                known[handle] = {
                    "title": title,
                    "first_seen": datetime.utcnow().isoformat()
                }
                msg = (
                    "\U0001f195 <b>New Product -- " + label + "!</b>\n\n"
                    "\U0001f4e6 <b>" + title + "</b>\n"
                    "\U0001f4b5 Price: <b>$" + str(price) + "</b>\n\n"
                    "\U0001f517 <a href='" + prod_url + "'>View Product</a>\n"
                    "\U0001f6d2 <a href='" + cart_url + "'>Add 10 to Cart &amp; Checkout</a>"
                )
                send_telegram(msg)
                print("  [NEW] [" + label + "] " + title)

            # Low stock
            if (
                inventory is not None
                and 0 < inventory <= LOW_STOCK_THRESH
                and handle not in low_alerted
            ):
                low_alerted.append(handle)
                msg = (
                    "\u26a0\ufe0f <b>Low Stock -- " + label + "!</b>\n\n"
                    "\U0001f4e6 <b>" + title + "</b>\n"
                    "\U0001f4b5 Price: <b>$" + str(price) + "</b>\n"
                    "\U0001f522 Only <b>" + str(inventory) + " entries left!</b>\n\n"
                    "\U0001f517 <a href='" + prod_url + "'>View Product</a>\n"
                    "\U0001f6d2 <a href='" + cart_url + "'>Add 10 to Cart &amp; Checkout</a>"
                )
                send_telegram(msg)
                print("  [LOW STOCK] [" + label + "] " + title)

            # Sold out
            if (
                not available
                and handle not in sold_alerted
                and handle in known
            ):
                sold_alerted.append(handle)
                if handle in low_alerted:
                    low_alerted.remove(handle)
                msg = (
                    "\U0001f534 <b>SOLD OUT -- " + label + "!</b>\n\n"
                    "\U0001f4e6 <b>" + title + "</b>\n\n"
                    "Entries are closed. Watch for the list and live draw!\n\n"
                    "\U0001f517 <a href='" + prod_url + "'>Product Page</a>"
                )
                send_telegram(msg)
                print("  [SOLD OUT] [" + label + "] " + title)

            # Restock
            if available and handle in sold_alerted:
                sold_alerted.remove(handle)
                if handle in low_alerted:
                    low_alerted.remove(handle)
                msg = (
                    "\U0001f7e2 <b>BACK IN STOCK -- " + label + "!</b>\n\n"
                    "\U0001f4e6 <b>" + title + "</b>\n"
                    "\U0001f4b5 Price: <b>$" + str(price) + "</b>\n\n"
                    "New giveaway is LIVE -- get your entries in!\n\n"
                    "\U0001f517 <a href='" + prod_url + "'>View Product</a>\n"
                    "\U0001f6d2 <a href='" + cart_url + "'>Add 10 to Cart &amp; Checkout</a>"
                )
                send_telegram(msg)
                print("  [RESTOCKED] [" + label + "] " + title)

    state["products"]          = known
    state["low_stock_alerted"] = low_alerted
    state["sold_out_alerted"]  = sold_alerted


def main():
    print("[" + datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC") + "] Running Monitor...")
    state = load_state()
    print("\nChecking products...")
    check_products(state)
    save_state(state)
    print("\nDone. State saved.")


if __name__ == "__main__":
    main()
