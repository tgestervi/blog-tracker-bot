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

FORMAT_PLATFORMS = {
    "Короткий контент": ["Instagram", "VK", "YouTube", "Tik-tok", "Pinterest", "Дзен"],
    "Длинный контент": ["YouTube", "VK"],
}


def format_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📱 Короткий контент", callback_data="Короткий контент")],
        [InlineKeyboardButton("🎥 Длинный контент", callback_data="Длинный контент")],
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


def push_to_notion(title, platforms):
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
    fmt = query.data
    platforms = FORMAT_PLATFORMS.get(fmt, [])
    raw = context.user_data.get("raw_idea", "")
    await query.edit_message_text("Сохраняю в Notion...")
    try:
        loop = asyncio.get_event_loop()
        url = await loop.run_in_executor(None, push_to_notion, raw, platforms)
        platforms_str = ", ".join(platforms)
        reply = f"Сохранено!\nФормат: {fmt}\nПлощадки: {platforms_str}"
        if url:
            reply += "\n\n" + url
        await query.edit_message_text(reply)
    except Exception as e:
        log.exception("notion error")
        await query.edit_message_text("Ошибка Notion: " + str(e))


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.VOICE, on_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(CallbackQueryHandler(on_format))
    log.info("Бот запущен...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
