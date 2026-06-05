# Blog Tracker Bot — Инструкция по настройке

## Шаг 1 — Создай Telegram-бота

1. Открой [@BotFather](https://t.me/BotFather) в Telegram
2. Отправь `/newbot`
3. Придумай имя и username (например `blog_tracker_bot`)
4. Скопируй **токен** — он выглядит так: `7123456789:AAH...`

---

## Шаг 2 — Подключи Notion

1. Зайди на [notion.so/my-integrations](https://www.notion.so/my-integrations)
2. Нажми **New integration**
3. Дай название, выбери свой Workspace, нажми **Save**
4. Скопируй **Internal Integration Token** (начинается на `secret_...`)
5. Открой базу **Blog Tracker** в Notion
6. Нажми `...` (три точки) → **Connections** → найди свою интеграцию и подключи

**Database ID** — это часть URL твоей базы:
```
https://notion.so/xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx?v=...
                  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                  вот это и есть Database ID
```

---

## Шаг 3 — Узнай свой Telegram Chat ID

1. Напиши боту [@userinfobot](https://t.me/userinfobot) — он пришлёт твой ID
2. Скопируй число (например `123456789`)

---

## Шаг 4 — Задеплой на Railway

1. Зайди на [railway.app](https://railway.app) и зарегистрируйся (через GitHub)
2. Нажми **New Project → Deploy from GitHub repo**
3. Загрузи папку с файлами (`bot.py`, `requirements.txt`, `Procfile`) в новый GitHub-репозиторий и подключи его
   — ИЛИ —
   Нажми **Empty project → Add Service → GitHub repo** после того как создашь репо

4. В проекте на Railway открой **Variables** и добавь:

| Переменная | Значение |
|---|---|
| `TELEGRAM_BOT_TOKEN` | токен из BotFather |
| `NOTION_TOKEN` | `secret_...` из Notion |
| `NOTION_DATABASE_ID` | ID базы Blog Tracker |
| `TELEGRAM_CHAT_ID` | твой числовой ID |
| `TIMEZONE` | `Europe/Moscow` (или свой часовой пояс) |

5. Railway сам запустит бота после сохранения переменных

---

## Что умеет бот

- `/new` — добавить идею в Notion (шаг за шагом, каждое поле можно пропустить)
- `/today` — показать что запланировано на сегодня
- Каждый день в **9:00** бот сам пришлёт список контента на день
- Статус **Idea** ставится автоматически

---

## Часовые пояса

Если ты не в Москве, замени `Europe/Moscow` на свой:
- Киев: `Europe/Kiev`
- Алматы: `Asia/Almaty`
- Минск: `Europe/Minsk`
