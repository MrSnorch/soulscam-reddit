#!/usr/bin/env python3
"""
Мониторинг новых постов НЕСКОЛЬКИХ Reddit-пользователей через Arctic Shift
(arctic-shift.photon-reddit.com) — открытый архив Reddit-данных,
не требует авторизации и не блочит GitHub Actions IP.

Уведомления шлются в Telegram.

Список юзернеймов задаётся через переменную окружения REDDIT_USERNAMES,
через запятую, например: "SoulboundMMO,futuraszn"
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error

USERNAMES = [
    u.strip()
    for u in os.environ.get("REDDIT_USERNAMES", "SoulboundMMO,futuraszn").split(",")
    if u.strip()
]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # миграция со старого формата {"seen_ids": [...]}  -> per-user
            if "seen_ids" in data and "by_user" not in data:
                data = {"by_user": {}}
            data.setdefault("by_user", {})
            return data
    return {"by_user": {}}


def save_state(state):
    for username, ids in state["by_user"].items():
        state["by_user"][username] = ids[-500:]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def fetch_posts(username):
    api_url = (
        f"https://arctic-shift.photon-reddit.com/api/posts/search"
        f"?limit=100&sort=desc&author={username}"
    )
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    req = urllib.request.Request(api_url, headers=headers)

    last_error = None
    payload = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            last_error = None
            break
        except urllib.error.HTTPError as e:
            last_error = f"HTTP {e.code} {e.reason}"
            print(f"[{username}] Attempt {attempt + 1}: {last_error}", file=sys.stderr)
        except Exception as e:
            last_error = str(e)
            print(f"[{username}] Attempt {attempt + 1}: {last_error}", file=sys.stderr)
        time.sleep(5)

    if last_error:
        print(f"[{username}] Error fetching after retries: {last_error}", file=sys.stderr)
        return []

    raw_posts = payload.get("data", [])
    posts = []
    for p in raw_posts:
        posts.append(
            {
                "id": p.get("id"),
                "title": p.get("title") or "(без заголовка)",
                "subreddit": p.get("subreddit_name_prefixed", ""),
                "url": "https://www.reddit.com" + p.get("permalink", ""),
                "created_utc": p.get("created_utc", 0),
                "selftext": (p.get("selftext") or "")[:300],
            }
        )
    return posts


def escape_html(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_message(username, post):
    text_block = f"\n\n{escape_html(post['selftext'])}" if post["selftext"] else ""
    return (
        f"🆕 Новый пост у <b>u/{username}</b>\n"
        f"{post['subreddit']}\n\n"
        f"<b>{escape_html(post['title'])}</b>"
        f"{text_block}\n\n"
        f"{post['url']}"
    )


def send_telegram(text):
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = json.dumps(
        {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        api_url, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"Telegram error: {e.code} {body}", file=sys.stderr)


def main():
    state = load_state()
    total_new = 0

    for username in USERNAMES:
        seen = set(state["by_user"].get(username, []))
        posts = fetch_posts(username)
        posts.sort(key=lambda p: p["created_utc"])  # старые -> новые

        new_posts = [p for p in posts if p["id"] and p["id"] not in seen]

        if not new_posts:
            print(f"[{username}] Новых постов нет.")
            continue

        print(f"[{username}] Найдено новых постов: {len(new_posts)}")
        total_new += len(new_posts)

        seen_list = state["by_user"].setdefault(username, [])
        for post in new_posts:
            send_telegram(format_message(username, post))
            seen_list.append(post["id"])
            time.sleep(1)

    if total_new == 0:
        print("Итого новых постов по всем аккаунтам: 0")

    save_state(state)


if __name__ == "__main__":
    main()
