#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UGC Ideas Bot - голос/текст -> Notion Blog Tracker
"""

import os
import logging
import tempfile
import asyncio
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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

logging.basicConfig(
    format="%(asctime)s  %(levelname)s  %(message)s",
    level=logging.INFO
)
log = logging.getLogger(__name__)

# Форматы -> площадки (без Хайлайтс и Подкаст — они через доп. вопрос)
FORMAT_PLATFORMS = {
    "Короткий ролик 9:16":   ["Instagram", "Tik-tok", "YouTube", "VK", "Дзен"],
    "Длинное видео 16:9":    ["YouTube", "VK", "Дзен"],
    "Текстовый пост + фото": ["Instagram", "VK", "Telegram", "Дзен", "YouTube"],
    "Инфографика=карусель":  ["Instagram", "VK", "Pinterest", "Tik-tok", "Telegram"],
    "Сторис":                ["Instagram", "VK", "Telegram", "Tik-tok"],
}

# Форматы, после которых задаём уточняющий вопрос
EXTRA_QUESTION = {
    "Длинное видео 16:9": "В формате подкаста?",
    "Сторис":             "Закрепить как хайлайтс?",
}


def format_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 Короткий ролик 9:16",   callback_data="fmt:Короткий ролик 9:16")],
        [InlineKeyboardButton("🎥 Длинное видео 16:9",    callback_data="fmt:Длинное видео 16:9")],
        [InlineKeyboardButton("📝 Текстовый пост + фото", callback_data="fmt:Текстовый пост + фото")],
        [InlineKeyboardButton("📊 Инфографика=карусель",  callback_data="fmt:Инфографика=карусель")],
        [InlineKeyboardButton("📱 Сторис",                callback_data="fmt:Сторис")],
    ])


def yes_no_keyboard(fmt):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Да", callback_data=f"extra:yes:{fmt}"),
            InlineKeyboardButton("❌ Нет", callback_data=f"extra:no:{fmt}"),
        ]
    ])


def transcribe(path):
    with open(path, "rb") as f:
        result = groq_client.audio.transcriptions.create(
            file=(os.path.basename(path), f.read()),
            model="whisper-large-v3-turbo",
            language="ru",
            response_format="text"
        )
    return result


def push_to_notion(title, formats, platforms):
    headers = {
        "Authorization": "Bearer " + NOTION_TOKEN,
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }
    payload = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            "Video Title": {
                "title": [{"text": {"content": title[:2000]}}]
            },
            "Формат": {
                "multi_select": [{"name": f} for f in formats]
            },
            "Platform": {
                "multi_select": [{"name": p} for p in platforms]
            },
            "Status": {
                "select": {"name": "Idea"}
            },
        },
        "children": [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": title[:2000]}}]
                }
            }
        ]
    }
    resp = requests.post(
        "https://api.notion.com/v1/pages",
        headers=headers,
        json=payload
    )
    data = resp.json()
    log.info("Notion %s: %s", resp.status_code, data.get("object", data.get("message", "")))
    if resp.status_code != 200:
        raise Exception("Notion: " + data.get("message", str(data)))
    return data.get("url", "")


async def save_and_reply(query, raw, formats, platforms):
    """Сохраняет в Notion и редактирует сообщение с результатом."""
    loop = asyncio.get_event_loop()
    url = await loop.run_in_executor(None, push_to_notion, raw, formats, platforms)
    fmt_str = ", ".join(formats)
    plat_str = ", ".join(platforms)
    reply = f"Сохранено!\nФормат: {fmt_str}\nПлощадки: {plat_str}"
    if url:
        reply += "\n\n" + url
    await query.edit_message_text(reply)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Отправь идею для ролика:\n"
        "— голосовым сообщением\n"
        "— или текстом\n\n"
        "Я сохраню её в Notion со статусом «Идея»."
    )


async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("Транскрибирую...")
    try:
        voice_file = await context.bot.get_file(update.message.voice.file_id)
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            await voice_file.download_to_drive(tmp.name)
            path = tmp.name
        loop = asyncio.get_event_loop()
        text = await loop.run_in_executor(None, transcribe, path)
        os.unlink(path)
        context.user_data["raw_idea"] = text
        await msg.edit_text(
            "Транскрипция:\n\n" + text + "\n\nВыбери формат контента:",
            reply_markup=format_keyboard()
        )
    except Exception as e:
        log.exception("voice error")
        await msg.edit_text("Ошибка: " + str(e))


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["raw_idea"] = update.message.text
    await update.message.reply_text(
        "Выбери формат контента:",
        reply_markup=format_keyboard()
    )


async def on_format(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Парсим callback_data: "fmt:Короткий ролик 9:16"
    fmt = query.data[len("fmt:"):]
    context.user_data["fmt"] = fmt

    if fmt in EXTRA_QUESTION:
        question = EXTRA_QUESTION[fmt]
        await query.edit_message_text(
            f"Формат: {fmt}\n\n{question}",
            reply_markup=yes_no_keyboard(fmt)
        )
    else:
        await query.edit_message_text("Сохраняю в Notion...")
        raw = context.user_data.get("raw_idea", "")
        platforms = FORMAT_PLATFORMS.get(fmt, [])
        try:
            await save_and_reply(query, raw, [fmt], platforms)
        except Exception as e:
            log.exception("notion error")
            await query.edit_message_text("Ошибка Notion: " + str(e))


async def on_extra(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Парсим callback_data: "extra:yes:Длинное видео 16:9"
    parts = query.data.split(":", 2)
    answer = parts[1]   # "yes" или "no"
    fmt = parts[2]      # основной формат

    raw = context.user_data.get("raw_idea", "")
    platforms = list(FORMAT_PLATFORMS.get(fmt, []))
    formats = [fmt]

    if answer == "yes":
        if fmt == "Длинное видео 16:9":
            formats.append("Подкаст")
            if "Mave" not in platforms:
                platforms.append("Mave")
        elif fmt == "Сторис":
            formats.append("Хайлайтс")

    await query.edit_message_text("Сохраняю в Notion...")
    try:
        await save_and_reply(query, raw, formats, platforms)
    except Exception as e:
        log.exception("notion error")
        await query.edit_message_text("Ошибка Notion: " + str(e))


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.VOICE, on_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(CallbackQueryHandler(on_format, pattern=r"^fmt:"))
    app.add_handler(CallbackQueryHandler(on_extra, pattern=r"^extra:"))
    log.info("Бот запущен...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
