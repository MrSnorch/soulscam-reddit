# Reddit Post Monitor → Telegram

Мониторит новые посты Reddit-пользователя (по умолчанию `SoulboundMMO`) и
шлёт уведомление в Telegram при появлении нового поста. Запускается в
GitHub Actions каждые 6 часов.

## Настройка

### 1. Создать Telegram-бота
1. Написать [@BotFather](https://t.me/BotFather) → `/newbot` → получить `TELEGRAM_BOT_TOKEN`.
2. Узнать свой `chat_id`:
   - написать боту любое сообщение
   - открыть `https://api.telegram.org/bot<TOKEN>/getUpdates`
   - взять `message.chat.id` из ответа

Если хочешь слать в группу/канал — добавь бота туда, `chat_id` будет отрицательным числом.

### 2. Добавить секреты в репозиторий
`Settings → Secrets and variables → Actions → New repository secret`:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

### 3. Первый запуск
При самом первом запуске скрипт увидит все текущие посты как "новые" и
разошлёт их разом (до 25 штук, сколько отдаёт Reddit за раз). Если это не
нужно — перед первым запуском вручную заполни `state.json` id последних
постов или просто прими один спам-заход уведомлений и дальше будет идти
только дельта.

### 4. Ручной тест
`Actions → Reddit Post Monitor → Run workflow` — запустит вне расписания.

## Файлы
- `monitor.py` — логика: тянет `reddit.com/user/<username>/submitted.json`,
  сравнивает с `state.json`, шлёт новые в Telegram.
- `state.json` — id уже виденных постов, коммитится обратно раннером.
- `.github/workflows/monitor.yml` — cron `0 */6 * * *`.

## Локальный запуск
```bash
export TELEGRAM_BOT_TOKEN=xxx
export TELEGRAM_CHAT_ID=xxx
export REDDIT_USERNAME=SoulboundMMO
python monitor.py
```
