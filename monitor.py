#!/usr/bin/env python3
"""
Мониторинг новых постов НЕСКОЛЬКИХ Reddit-пользователей через Arctic Shift
(arctic-shift.photon-reddit.com), с уведомлениями в Telegram.

Работает ЦИКЛОМ внутри одного запуска: проверяет всех юзеров каждые
CHECK_INTERVAL_SECONDS секунд, в течение RUN_DURATION_SECONDS секунд
(по умолчанию 6 часов). После истечения времени скрипт сам триггерит
следующий запуск workflow через GitHub API (repository_dispatch), чтобы
цепочка продолжалась без разрыва — и завершается.

Список юзернеймов: переменная окружения REDDIT_USERNAMES, через запятую.
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

# GitHub self-retrigger — нужны, чтобы после 6 часов сразу запустить следующий джоб
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY")  # "owner/repo", задаётся Actions автоматически

RUN_DURATION_SECONDS = int(os.environ.get("RUN_DURATION_SECONDS", 6 * 60 * 60))  # 6 часов
CHECK_INTERVAL_SECONDS = int(os.environ.get("CHECK_INTERVAL_SECONDS", 5 * 60))  # 5 минут

STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
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


def commit_state():
    """Коммитит state.json в репозиторий прямо во время цикла (не только
    в конце), чтобы падение джобы посреди 6-часового окна не приводило
    к повторной рассылке уже отправленных уведомлений."""
    import subprocess

    try:
        subprocess.run(["git", "add", "state.json"], check=True, cwd=os.path.dirname(__file__) or ".")
        result = subprocess.run(
            ["git", "diff", "--staged", "--quiet"],
            cwd=os.path.dirname(__file__) or ".",
        )
        if result.returncode == 0:
            return  # нечего коммитить
        subprocess.run(
            ["git", "commit", "-m", "chore: update seen posts state [skip ci]"],
            check=True,
            cwd=os.path.dirname(__file__) or ".",
        )
        subprocess.run(["git", "push"], check=True, cwd=os.path.dirname(__file__) or ".")
        print("state.json закоммичен.")
    except subprocess.CalledProcessError as e:
        print(f"Не удалось закоммитить state.json: {e}", file=sys.stderr)


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


def check_once(state):
    """Один проход по всем юзерам. Возвращает число новых постов."""
    total_new = 0
    for username in USERNAMES:
        seen = set(state["by_user"].get(username, []))
        posts = fetch_posts(username)
        posts.sort(key=lambda p: p["created_utc"])

        new_posts = [p for p in posts if p["id"] and p["id"] not in seen]
        if not new_posts:
            continue

        print(f"[{username}] Найдено новых постов: {len(new_posts)}")
        total_new += len(new_posts)

        seen_list = state["by_user"].setdefault(username, [])
        for post in new_posts:
            send_telegram(format_message(username, post))
            seen_list.append(post["id"])
            time.sleep(1)

    save_state(state)
    return total_new


def trigger_next_run():
    """Запускает следующий workflow_dispatch через GitHub API, чтобы
    цепочка 6-часовых окон продолжалась без разрыва."""
    if not GITHUB_TOKEN or not GITHUB_REPOSITORY:
        print("GITHUB_TOKEN/GITHUB_REPOSITORY недоступны — пропускаю self-retrigger "
              "(следующий запуск возьмёт на себя cron).", file=sys.stderr)
        return

    api_url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/workflows/monitor.yml/dispatches"
    payload = json.dumps({"ref": os.environ.get("GITHUB_REF_NAME", "main")}).encode("utf-8")
    req = urllib.request.Request(
        api_url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"Следующий запуск запущен: HTTP {resp.status}")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"Не удалось запустить следующий джоб: {e.code} {body}", file=sys.stderr)


def main():
    state = load_state()
    start = time.monotonic()
    deadline = start + RUN_DURATION_SECONDS

    print(
        f"Старт цикла: {RUN_DURATION_SECONDS // 3600}ч, "
        f"проверка каждые {CHECK_INTERVAL_SECONDS // 60} мин, "
        f"юзеры: {', '.join(USERNAMES)}"
    )

    iteration = 0
    while True:
        iteration += 1
        now = time.monotonic()
        print(f"\n--- Проверка #{iteration} ({int(now - start)}s с начала) ---")
        found = check_once(state)
        if found > 0:
            commit_state()

        now = time.monotonic()
        remaining = deadline - now
        if remaining <= CHECK_INTERVAL_SECONDS:
            # следующей полной проверки уже не влезет — выходим из цикла
            if remaining > 0:
                print(f"До конца окна осталось {int(remaining)}s — завершаю цикл.")
            break

        time.sleep(CHECK_INTERVAL_SECONDS)

    print("\n6-часовое окно почти закончилось. Запускаю следующий джоб...")
    trigger_next_run()


if __name__ == "__main__":
    main()
