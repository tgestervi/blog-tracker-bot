#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UGC Ideas Bot - голос/текст -> Notion Blog Tracker
"""

import os
import logging
import tempfile
import asyncio
from datetime import date, timedelta
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)
from groq import Groq
import requests

load_dotenv()

TELEGRAM_TOKEN     = os.environ["TELEGRAM_BOT_TOKEN"]
GROQ_API_KEY       = os.environ["GROQ_API_KEY"]
NOTION_TOKEN       = os.environ["NOTION_TOKEN"]
NOTION_DATABASE_ID = os.environ["NOTION_DATABASE_ID"]

groq_client = Groq(api_key=GROQ_API_KEY)

logging.basicConfig(format="%(asctime)s  %(levelname)s  %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

TASKS_BTN = "📋 Задачи на сегодня"

FORMAT_PLATFORMS = {
    "Короткий ролик 9:16":   ["Instagram", "Tik-tok", "YouTube", "VK", "Дзен"],
    "Длинное видео 16:9":    ["YouTube", "VK", "Дзен"],
    "Текстовый пост + фото": ["Instagram", "VK", "Telegram", "Дзен", "YouTube"],
    "Инфографика=карусель":  ["Instagram", "VK", "Pinterest", "Tik-tok", "Telegram"],
    "Сторис":                ["Instagram", "VK", "Telegram", "Tik-tok"],
}

EXTRA_QUESTION = {
    "Длинное видео 16:9":    "В формате подкаста?",
    "Короткий ролик 9:16":   "Закрепить как хайлайтс?",
    "Текстовый пост + фото": "Закрепить как хайлайтс?",
    "Инфографика=карусель":  "Закрепить как хайлайтс?",
    "Сторис":                "Закрепить как хайлайтс?",
}

THEMES = ["Экспертный", "Личный", "Развлекательный"]


def main_keyboard():
    return ReplyKeyboardMarkup([[KeyboardButton(TASKS_BTN)]], resize_keyboard=True)


def format_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 Короткий ролик 9:16",   callback_data="fmt:Короткий ролик 9:16")],
        [InlineKeyboardButton("🎥 Длинное видео 16:9",    callback_data="fmt:Длинное видео 16:9")],
        [InlineKeyboardButton("📝 Текстовый пост + фото", callback_data="fmt:Текстовый пост + фото")],
        [InlineKeyboardButton("📊 Инфографика=карусель",  callback_data="fmt:Инфографика=карусель")],
        [InlineKeyboardButton("📱 Сторис",                callback_data="fmt:Сторис")],
    ])


def yes_no_keyboard(fmt):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Да", callback_data=f"extra:yes:{fmt}"),
        InlineKeyboardButton("❌ Нет", callback_data=f"extra:no:{fmt}"),
    ]])


def theme_keyboard(selected: set):
    rows = []
    for t in THEMES:
        label = ("✅ " if t in selected else "") + t
        rows.append([InlineKeyboardButton(label, callback_data=f"theme:{t}")])
    rows.append([InlineKeyboardButton("Далее →", callback_data="theme_done")])
    return InlineKeyboardMarkup(rows)


def date_keyboard():
    today = date.today()
    rows = [
        [
            InlineKeyboardButton("Сегодня", callback_data=f"date:{today.isoformat()}"),
            InlineKeyboardButton("Завтра",  callback_data=f"date:{(today + timedelta(1)).isoformat()}"),
        ],
        [
            InlineKeyboardButton("+2 дня",  callback_data=f"date:{(today + timedelta(2)).isoformat()}"),
            InlineKeyboardButton("+3 дня",  callback_data=f"date:{(today + timedelta(3)).isoformat()}"),
            InlineKeyboardButton("+7 дней", callback_data=f"date:{(today + timedelta(7)).isoformat()}"),
        ],
        [InlineKeyboardButton("Пропустить", callback_data="date:skip")],
    ]
    return InlineKeyboardMarkup(rows)


def skip_ref_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("Пропустить", callback_data="ref:skip")]])


def transcribe(path):
    with open(path, "rb") as f:
        result = groq_client.audio.transcriptions.create(
            file=(os.path.basename(path), f.read()),
            model="whisper-large-v3-turbo", language="ru", response_format="text"
        )
    return result


def generate_title(idea_text: str) -> str:
    resp = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": "Ты помощник контент-мейкера. Придумай короткое цепляющее название видео до 60 символов на основе идеи. Отвечай ТОЛЬКО названием, без кавычек и объяснений."},
            {"role": "user", "content": idea_text},
        ],
        max_tokens=100,
    )
    return resp.choices[0].message.content.strip()


def push_to_notion(title, body, formats, platforms, themes=None, pub_date=None, reference=None):
    headers = {"Authorization": "Bearer " + NOTION_TOKEN,
               "Content-Type": "application/json", "Notion-Version": "2022-06-28"}
    properties = {
        "Video Title": {"title": [{"text": {"content": title[:2000]}}]},
        "Формат":      {"multi_select": [{"name": f} for f in formats]},
        "Platform":    {"multi_select": [{"name": p} for p in platforms]},
        "Status":      {"select": {"name": "Idea"}},
    }
    if themes:
        properties["Themes"] = {"multi_select": [{"name": t} for t in themes]}
    if pub_date:
        properties["Live Date"] = {"date": {"start": pub_date}}
    if reference:
        properties["Референс"] = {"url": reference}

    payload = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": properties,
        "children": [{"object": "block", "type": "paragraph",
                      "paragraph": {"rich_text": [{"type": "text", "text": {"content": body[:2000]}}]}}],
    }
    resp = requests.post("https://api.notion.com/v1/pages", headers=headers, json=payload)
    data = resp.json()
    if resp.status_code != 200:
        raise Exception("Notion: " + data.get("message", str(data)))
    return data.get("url", "")


def get_tasks_today():
    today = date.today().isoformat()
    headers = {"Authorization": "Bearer " + NOTION_TOKEN,
               "Content-Type": "application/json", "Notion-Version": "2022-06-28"}
    payload = {
        "filter": {"property": "Live Date", "date": {"equals": today}},
        "sorts":  [{"property": "Live Date", "direction": "ascending"}],
    }
    resp = requests.post(
        f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
        headers=headers, json=payload)
    data = resp.json()
    if resp.status_code != 200:
        raise Exception("Notion: " + data.get("message", str(data)))
    return data.get("results", [])


async def _do_save(edit_fn, context):
    """Pull all accumulated data from user_data and save to Notion."""
    title     = context.user_data.get("generated_title", "")
    body      = context.user_data.get("raw_idea", "")
    formats   = context.user_data.get("formats", [])
    platforms = context.user_data.get("platforms", [])
    themes    = list(context.user_data.get("selected_themes", set()))
    pub_date  = context.user_data.get("pub_date")
    reference = context.user_data.get("reference")

    await edit_fn("Сохраняю в Notion...")
    loop = asyncio.get_event_loop()
    try:
        url = await loop.run_in_executor(
            None, push_to_notion, title, body, formats, platforms, themes, pub_date, reference
        )
        fmt_str = ", ".join(formats)
        reply = f"✅ Сохранено!\n\n📌 {title}\nФормат: {fmt_str}"
        if themes:
            reply += f"\nТема: {', '.join(themes)}"
        if pub_date:
            reply += f"\nДата: {pub_date}"
        if url:
            reply += "\n\n" + url
        await edit_fn(reply)
    except Exception as e:
        await edit_fn("Ошибка Notion: " + str(e))

    context.user_data["state"] = None
    context.user_data["selected_themes"] = set()


async def _init_idea(context, raw_idea: str) -> str:
    """Store idea, generate AI title, reset per-idea state. Returns display text."""
    context.user_data["raw_idea"] = raw_idea
    context.user_data["selected_themes"] = set()
    context.user_data["state"] = None
    loop = asyncio.get_event_loop()
    try:
        gen_title = await loop.run_in_executor(None, generate_title, raw_idea)
    except Exception:
        gen_title = raw_idea[:60]
    context.user_data["generated_title"] = gen_title
    return gen_title


# ── handlers ──────────────────────────────────────────────────────────────────

async def cmd_start(update, context):
    await update.message.reply_text(
        "Привет! Отправь идею для ролика:\n— голосовым сообщением\n— или текстом\n\nЯ сохраню её в Notion со статусом «Идея».",
        reply_markup=main_keyboard()
    )


async def on_tasks_today(update, context):
    await update.message.reply_text("Загружаю задачи...")
    try:
        loop = asyncio.get_event_loop()
        pages = await loop.run_in_executor(None, get_tasks_today)
        if not pages:
            await update.message.reply_text("На сегодня задач нет 🎉")
            return
        lines = [f"📋 Задачи на сегодня ({date.today().strftime('%d.%m')}):\n"]
        for page in pages:
            props = page.get("properties", {})
            title_parts = props.get("Video Title", {}).get("title", [])
            title = "".join(t.get("plain_text", "") for t in title_parts) or "Без названия"
            status_obj = props.get("Status", {}).get("select") or {}
            status = status_obj.get("name", "")
            url = page.get("url", "")
            line = f"• {title}"
            if status: line += f" [{status}]"
            if url:    line += f"\n  {url}"
            lines.append(line)
        await update.message.reply_text("\n\n".join(lines))
    except Exception as e:
        await update.message.reply_text("Ошибка: " + str(e))


async def on_voice(update, context):
    msg = await update.message.reply_text("Транскрибирую...")
    try:
        voice_file = await context.bot.get_file(update.message.voice.file_id)
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            await voice_file.download_to_drive(tmp.name)
            path = tmp.name
        loop = asyncio.get_event_loop()
        text = await loop.run_in_executor(None, transcribe, path)
        os.unlink(path)
        gen_title = await _init_idea(context, text)
        display = f"Транскрипция:\n\n{text}\n\n🤖 Название: {gen_title}\n\nВыбери формат контента:"
        await msg.edit_text(display, reply_markup=format_keyboard())
    except Exception as e:
        await msg.edit_text("Ошибка: " + str(e))


async def on_text(update, context):
    # Reference link arriving as plain text
    if context.user_data.get("state") == "waiting_reference":
        context.user_data["reference"] = update.message.text
        context.user_data["state"] = None
        msg = await update.message.reply_text("...")
        await _do_save(msg.edit_text, context)
        return

    # New idea
    raw = update.message.text
    gen_title = await _init_idea(context, raw)
    display = f"💡 Идея:\n{raw}\n\n🤖 Название: {gen_title}\n\nВыбери формат контента:"
    await update.message.reply_text(display, reply_markup=format_keyboard())


async def on_format(update, context):
    query = update.callback_query
    await query.answer()
    fmt = query.data[len("fmt:"):]
    context.user_data["fmt"] = fmt
    if fmt in EXTRA_QUESTION:
        await query.edit_message_text(
            f"Формат: {fmt}\n\n{EXTRA_QUESTION[fmt]}",
            reply_markup=yes_no_keyboard(fmt)
        )
    else:
        context.user_data["formats"]   = [fmt]
        context.user_data["platforms"] = list(FORMAT_PLATFORMS.get(fmt, []))
        selected = context.user_data.get("selected_themes", set())
        await query.edit_message_text("Выбери тему (можно несколько):", reply_markup=theme_keyboard(selected))


async def on_extra(update, context):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":", 2)
    answer, fmt = parts[1], parts[2]
    platforms = list(FORMAT_PLATFORMS.get(fmt, []))
    formats   = [fmt]
    if answer == "yes":
        if fmt == "Длинное видео 16:9":
            formats.append("Подкаст")
            if "Mave" not in platforms:
                platforms.append("Mave")
        else:
            formats.append("Хайлайтс")
    context.user_data["formats"]   = formats
    context.user_data["platforms"] = platforms
    selected = context.user_data.get("selected_themes", set())
    await query.edit_message_text("Выбери тему (можно несколько):", reply_markup=theme_keyboard(selected))


async def on_theme(update, context):
    query = update.callback_query
    await query.answer()

    if query.data == "theme_done":
        await query.edit_message_text("Выбери дату публикации:", reply_markup=date_keyboard())
        return

    theme    = query.data[len("theme:"):]
    selected = context.user_data.get("selected_themes", set())
    if theme in selected:
        selected.discard(theme)
    else:
        selected.add(theme)
    context.user_data["selected_themes"] = selected
    await query.edit_message_reply_markup(reply_markup=theme_keyboard(selected))


async def on_date(update, context):
    query = update.callback_query
    await query.answer()
    val = query.data[len("date:"):]
    context.user_data["pub_date"] = None if val == "skip" else val
    context.user_data["state"]    = "waiting_reference"
    await query.edit_message_text(
        "Отправь ссылку на референс или пропусти:",
        reply_markup=skip_ref_keyboard()
    )


async def on_ref_skip(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data["reference"] = None
    context.user_data["state"]     = None
    await _do_save(query.edit_message_text, context)


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.Text([TASKS_BTN]), on_tasks_today))
    app.add_handler(MessageHandler(filters.VOICE, on_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(CallbackQueryHandler(on_format,   pattern=r"^fmt:"))
    app.add_handler(CallbackQueryHandler(on_extra,    pattern=r"^extra:"))
    app.add_handler(CallbackQueryHandler(on_theme,    pattern=r"^theme"))
    app.add_handler(CallbackQueryHandler(on_date,     pattern=r"^date:"))
    app.add_handler(CallbackQueryHandler(on_ref_skip, pattern=r"^ref:skip$"))
    log.info("Бот запущен...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
