#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UGC Ideas Bot - golos/tekst -> Notion Blog Tracker
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

TELEGRAM_TOKEN     = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY       = os.environ["GROQ_API_KEY"]
NOTION_TOKEN       = os.environ["NOTION_TOKEN"]
NOTION_DATABASE_ID = os.environ["NOTION_DATABASE_ID"]

groq = Groq(api_key=GROQ_API_KEY)

logging.basicConfig(
    format="%(asctime)s  %(levelname)s  %(message)s",
    level=logging.INFO
)
log = logging.getLogger(__name__)


def platform_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("YouTube",   callback_data="YouTube"),
            InlineKeyboardButton("Instagram", callback_data="Instagram"),
        ],
        [
            InlineKeyboardButton("TikTok",    callback_data="Tik-tok"),
            InlineKeyboardButton("Shorts",    callback_data="YouTube"),
        ],
        [
            InlineKeyboardButton("Telegram",  callback_data="Telegram"),
            InlineKeyboardButton("Threads",   callback_data="Threads"),
        ],
    ])


def transcribe(path):
    with open(path, "rb") as f:
        result = groq.audio.transcriptions.create(
            file=(os.path.basename(path), f.read()),
            model="whisper-large-v3-turbo",
            language="ru",
            response_format="text"
        )
    return result


def push_to_notion(title, platform):
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
                "multi_select": [{"name": platform}]
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
        "Privet! Otpravlyaj ideyu dlya rolika:\n"
        "- Golosovym soobshcheniem\n"
        "- Tekstom\n\n"
        "Ya sokhranyau v Notion so statusom Idea."
    )


async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("Transkribiruyu...")
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
            "Transkripciya:\n\n" + text + "\n\nNa kakuyu platformu?",
            reply_markup=platform_keyboard()
        )
    except Exception as e:
        log.exception("voice error")
        await msg.edit_text("Oshibka: " + str(e))


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["raw_idea"] = update.message.text
    await update.message.reply_text(
        "Na kakuyu platformu?",
        reply_markup=platform_keyboard()
    )


async def on_platform(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    platform = query.data
    raw = context.user_data.get("raw_idea", "")

    await query.edit_message_text("Sokhranyayu v Notion...")

    try:
        loop = asyncio.get_event_loop()
        url = await loop.run_in_executor(None, push_to_notion, raw, platform)

        reply = "Sokhraneno!\nStatus: Idea | Platforma: " + platform
        if url:
            reply += "\n\n" + url

        await query.edit_message_text(reply)

    except Exception as e:
        log.exception("notion error")
        await query.edit_message_text("Oshibka Notion: " + str(e))


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.VOICE, on_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(CallbackQueryHandler(on_platform))

    log.info("Bot is running...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
