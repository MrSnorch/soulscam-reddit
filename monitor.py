#!/usr/bin/env python3
"""
Мониторинг новых постов Reddit-пользователя, уведомления в Telegram.

Источник: официальный Reddit JSON API (публичный, без авторизации нужен только
корректный User-Agent, но лучше использовать OAuth client_id/secret, если он
у тебя уже есть — так стабильнее и не банит по IP GitHub Actions).

Состояние (какие посты уже видели) хранится в state.json и коммитится обратно
в репозиторий Actions-раннером, чтобы между запусками (раз в 6 часов) не
слать одно и то же повторно.
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error

REDDIT_USERNAME = os.environ.get("REDDIT_USERNAME", "SoulboundMMO")
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")
REDDIT_URL = f"https://www.reddit.com/user/{REDDIT_USERNAME}/submitted.json?limit=25"
USER_AGENT = f"python:reddit-post-monitor:v1.0 (by /u/{REDDIT_USERNAME}-watcher)"


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"seen_ids": []}


def save_state(state):
    # держим последние 500 id, чтобы файл не рос бесконечно
    state["seen_ids"] = state["seen_ids"][-500:]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def fetch_posts():
    req = urllib.request.Request(REDDIT_URL, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"HTTP error fetching reddit: {e.code} {e.reason}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error fetching reddit: {e}", file=sys.stderr)
        sys.exit(1)

    children = data.get("data", {}).get("children", [])
    posts = []
    for child in children:
        p = child.get("data", {})
        posts.append(
            {
                "id": p.get("id"),
                "title": p.get("title", "(без заголовка)"),
                "subreddit": p.get("subreddit_name_prefixed", ""),
                "url": "https://www.reddit.com" + p.get("permalink", ""),
                "created_utc": p.get("created_utc", 0),
                "is_self": p.get("is_self", False),
                "link_flair_text": p.get("link_flair_text"),
            }
        )
    return posts


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


def format_message(post):
    flair = f" [{post['link_flair_text']}]" if post.get("link_flair_text") else ""
    return (
        f"🆕 Новый пост у <b>u/{REDDIT_USERNAME}</b>{flair}\n"
        f"{post['subreddit']}\n\n"
        f"<b>{escape_html(post['title'])}</b>\n\n"
        f"{post['url']}"
    )


def escape_html(s):
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def main():
    state = load_state()
    seen = set(state["seen_ids"])

    posts = fetch_posts()
    # старые -> новые, чтобы уведомления в телеге шли в хронологическом порядке
    posts.sort(key=lambda p: p["created_utc"])

    new_posts = [p for p in posts if p["id"] and p["id"] not in seen]

    if not new_posts:
        print("Новых постов нет.")
        return

    print(f"Найдено новых постов: {len(new_posts)}")

    for post in new_posts:
        send_telegram(format_message(post))
        seen.add(post["id"])
        state["seen_ids"].append(post["id"])
        time.sleep(1)  # чтобы не упереться в rate limit телеги

    save_state(state)


if __name__ == "__main__":
    main()
