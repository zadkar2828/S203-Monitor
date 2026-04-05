import json
import os
import re
import requests
import instaloader
from datetime import datetime

# ── Config ─────────────────────────────────────────────────────────────────────
SHOP_URL          = "https://sonic203.com/products.json?limit=250"
INSTAGRAM_USER    = "sonic203"
STATE_FILE        = "monitor_state.json"
LOW_STOCK_THRESH  = 20
TELEGRAM_TOKEN    = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID  = os.environ["TELEGRAM_CHAT_ID"]
IG_USERNAME       = os.environ["IG_USERNAME"]
IG_PASSWORD       = os.environ["IG_PASSWORD"]
LIST_KEYWORDS     = ["s203list.com", "airtable.com"]
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
        "ig_seen_post_ids": [],
        "ig_last_bio_url": ""
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

    known        = state["products"]
    low_alerted  = state["low_stock_alerted"]
    sold_alerted = state["sold_out_alerted"]

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
                f"Entries are closed. Watch for the list and live draw! 🎰\n\n"
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
def find_list_link(text: str):
    """Return the list URL if text contains a known giveaway list domain."""
    if not text:
        return None
    for kw in LIST_KEYWORDS:
        if kw.lower() in text.lower():
            match = re.search(r'https?://\S*' + re.escape(kw.split(".")[0]) + r'\S*', text, re.IGNORECASE)
            if match:
                return match.group(0).rstrip(".,;:)'\"")
            return f"https://{kw}"
    return None

def send_list_alert(link: str, source: str):
    msg = (
        f"🎰 <b>Entry List is LIVE — Sonic203!</b>\n\n"
        f"Go check your entries are in there!\n\n"
        f"📋 <a href='{link}'>Open The List</a>\n\n"
        f"🏁 Draw happening soon — watch live on @sonic203ct!\n"
        f"📍 Found in: {source}"
    )
    send_telegram(msg)
    print(f"  [LIST ALERT] {source} → {link}")

def check_instagram(state: dict):
    print(f"  Checking Instagram @{INSTAGRAM_USER}...")

    seen_ids     = state.get("ig_seen_post_ids", [])
    last_bio_url = state.get("ig_last_bio_url", "")

    try:
        L = instaloader.Instaloader()
        L.login(IG_USERNAME, IG_PASSWORD)

        profile = instaloader.Profile.from_username(L.context, INSTAGRAM_USER)

        # ── 1. Bio link ──────────────────────────────────────────────────────
        bio_url = profile.external_url or ""
        print(f"  Bio link: {bio_url}")

        if bio_url and bio_url != last_bio_url:
            state["ig_last_bio_url"] = bio_url
            found = find_list_link(bio_url)
            if found:
                send_list_alert(found, "Bio link")

        # ── 2. Stories ───────────────────────────────────────────────────────
        try:
            for story in L.get_stories(userids=[profile.userid]):
                for item in story.get_items():
                    story_id = str(item.mediaid)
                    if story_id in seen_ids:
                        continue
                    seen_ids.append(story_id)
                    caption = item.caption or ""
                    found   = find_list_link(caption)
                    if found:
                        send_list_alert(found, "Instagram Story")
            print("  Stories checked.")
        except Exception as e:
            print(f"  [Stories] Could not fetch: {e}")

        # ── 3. Recent posts ──────────────────────────────────────────────────
        for i, post in enumerate(profile.get_posts()):
            if i >= 10:
                break
            post_id = post.shortcode
            if post_id in seen_ids:
                continue
            seen_ids.append(post_id)
            caption = post.caption or ""
            found   = find_list_link(caption)
            if found:
                send_list_alert(found, f"Post instagram.com/p/{post_id}")

        print("  Instagram check complete.")

    except Exception as e:
        print(f"  [Instagram] Error: {e} — skipping this check.")

    state["ig_seen_post_ids"] = seen_ids[-100:]


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
