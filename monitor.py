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
import datetime
import urllib.request
import urllib.error

USERNAMES = [
    u.strip()
    for u in os.environ.get("REDDIT_USERNAMES", "SoulboundMMO,futuraszn").split(",")
    if u.strip()
]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY")

RUN_DURATION_SECONDS = int(os.environ.get("RUN_DURATION_SECONDS", 6 * 60 * 60))
CHECK_INTERVAL_SECONDS = int(os.environ.get("CHECK_INTERVAL_SECONDS", 5 * 60))

SEED_ONLY = os.environ.get("SEED_ONLY", "false").lower() in ("1", "true", "yes")

STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def log(msg):
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {msg}", flush=True)


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
    import subprocess

    cwd = os.path.dirname(__file__) or "."
    try:
        subprocess.run(["git", "add", "state.json"], check=True, cwd=cwd)
        result = subprocess.run(["git", "diff", "--staged", "--quiet"], cwd=cwd)
        if result.returncode == 0:
            log("state.json без изменений, коммит не нужен.")
            return
        subprocess.run(
            ["git", "commit", "-m", "chore: update seen posts state [skip ci]"],
            check=True,
            cwd=cwd,
        )
        subprocess.run(["git", "push"], check=True, cwd=cwd)
        log("state.json закоммичен и запушен.")
    except subprocess.CalledProcessError as e:
        log(f"⚠ Не удалось закоммитить state.json: {e}")


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
            log(f"[{username}] Попытка {attempt + 1}/3 неудачна: {last_error}")
        except Exception as e:
            last_error = str(e)
            log(f"[{username}] Попытка {attempt + 1}/3 неудачна: {last_error}")
        time.sleep(5)

    if last_error:
        log(f"[{username}] ❌ Не удалось получить посты после 3 попыток: {last_error}")
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
    log(f"[{username}] Получено {len(posts)} постов из Arctic Shift")
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

    for attempt in range(5):
        req = urllib.request.Request(
            api_url, data=payload, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp.read()
            log("✅ Сообщение отправлено в Telegram")
            return
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if e.code == 429:
                try:
                    retry_after = json.loads(body).get("parameters", {}).get("retry_after", 5)
                except Exception:
                    retry_after = 5
                log(f"⏳ Telegram 429, жду {retry_after}s и повторяю... (попытка {attempt + 1}/5)")
                time.sleep(retry_after + 1)
                continue
            log(f"❌ Telegram error: {e.code} {body}")
            return
    log("❌ Telegram: не удалось отправить сообщение после нескольких попыток.")


def check_once(state):
    """Один проход по всем юзерам. Возвращает число новых постов."""
    total_new = 0
    for username in USERNAMES:
        seen = set(state["by_user"].get(username, []))
        log(f"[{username}] Проверяю... (уже известно постов: {len(seen)})")
        posts = fetch_posts(username)

        if SEED_ONLY:
            ids = [p["id"] for p in posts if p.get("id")]
            state["by_user"][username] = ids
            log(f"[{username}] SEED_ONLY: помечено как виденные {len(ids)} постов")
            continue

        posts.sort(key=lambda p: p["created_utc"])

        new_posts = [p for p in posts if p["id"] and p["id"] not in seen]
        if not new_posts:
            log(f"[{username}] Новых постов нет.")
            continue

        log(f"[{username}] 🆕 Найдено новых постов: {len(new_posts)}")
        total_new += len(new_posts)

        seen_list = state["by_user"].setdefault(username, [])
        for post in new_posts:
            log(f"[{username}]   -> отправляю: {post['title'][:80]!r}")
            send_telegram(format_message(username, post))
            seen_list.append(post["id"])
            time.sleep(2)

    save_state(state)
    return total_new


def trigger_next_run():
    if not GITHUB_TOKEN or not GITHUB_REPOSITORY:
        log("⚠ GITHUB_TOKEN/GITHUB_REPOSITORY недоступны — пропускаю self-retrigger "
            "(следующий запуск возьмёт на себя cron).")
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
            log(f"✅ Следующий запуск успешно запущен: HTTP {resp.status}")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        log(f"❌ Не удалось запустить следующий джоб: {e.code} {body}")


def main():
    state = load_state()

    log(f"Запуск скрипта. Юзеры: {', '.join(USERNAMES)}")
    log(f"Режим: {'SEED_ONLY (без рассылки)' if SEED_ONLY else 'обычный мониторинг'}")

    if SEED_ONLY:
        check_once(state)
        commit_state()
        log("✅ SEED_ONLY завершён. Обычный запуск теперь будет слать только новые посты.")
        return

    start = time.monotonic()
    deadline = start + RUN_DURATION_SECONDS
    end_time_str = (
        datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(seconds=RUN_DURATION_SECONDS)
    ).strftime("%Y-%m-%d %H:%M:%S UTC")

    log(
        f"Цикл рассчитан на {RUN_DURATION_SECONDS // 3600}ч "
        f"{(RUN_DURATION_SECONDS % 3600) // 60}м, проверка каждые "
        f"{CHECK_INTERVAL_SECONDS // 60} мин. Ожидаемое завершение окна: ~{end_time_str}"
    )

    iteration = 0
    while True:
        iteration += 1
        now = time.monotonic()
        elapsed = int(now - start)
        log(f"--- Проверка #{iteration} (прошло {elapsed // 60} мин {elapsed % 60} сек) ---")

        found = check_once(state)
        if found > 0:
            log(f"Итого новых постов в этой проверке: {found}")
            commit_state()
        else:
            log("Новых постов не найдено ни у кого.")

        now = time.monotonic()
        remaining = deadline - now
        if remaining <= CHECK_INTERVAL_SECONDS:
            if remaining > 0:
                log(f"До конца окна осталось {int(remaining)}s — завершаю цикл проверок.")
            break

        next_check_str = (
            datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(seconds=CHECK_INTERVAL_SECONDS)
        ).strftime("%H:%M:%S UTC")
        log(f"Сплю {CHECK_INTERVAL_SECONDS // 60} мин. Следующая проверка ~{next_check_str}")
        time.sleep(CHECK_INTERVAL_SECONDS)

    log(f"Цикл завершён. Всего проверок за этот запуск: {iteration}")
    log("Запускаю следующий 6-часовой джоб...")
    trigger_next_run()
    log("Скрипт завершает работу.")


if __name__ == "__main__":
    main()
