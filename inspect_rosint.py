"""
Разведка: смотрим, какие сетевые запросы делает rosint.dev при загрузке
профиля, чтобы понять его внутренний API (или отсутствие такового).

Запуск:
    pip install playwright
    playwright install chromium
    python inspect_rosint.py
"""

from playwright.sync_api import sync_playwright

USERNAME = "SoulboundMMO"
URL = f"https://rosint.dev/?u={USERNAME}"


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        requests_log = []

        def on_request(req):
            requests_log.append(f"REQUEST  {req.method} {req.url}")

        def on_response(res):
            ct = res.headers.get("content-type", "")
            if "json" in ct or "xhr" in res.request.resource_type:
                requests_log.append(
                    f"RESPONSE {res.status} {res.url}  [{ct}]"
                )

        page.on("request", on_request)
        page.on("response", on_response)

        print(f"Открываю {URL} ...")
        page.goto(URL, wait_until="networkidle", timeout=30000)

        # даём странице время подгрузить данные через JS после networkidle
        page.wait_for_timeout(3000)

        print("\n=== ВСЕ ЗАПРОСЫ / JSON-ОТВЕТЫ ===\n")
        for line in requests_log:
            print(line)

        print("\n=== HTML после рендера (первые 3000 символов) ===\n")
        html = page.content()
        print(html[:3000])

        # Пробуем найти видимый текст постов на странице
        print("\n=== ВИДИМЫЙ ТЕКСТ СТРАНИЦЫ (может помочь понять структуру) ===\n")
        body_text = page.inner_text("body")
        print(body_text[:3000])

        browser.close()


if __name__ == "__main__":
    main()
