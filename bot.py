#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UGC Ideas Bot - голос/текст → Notion Blog Tracker + кросспостинг
"""

import os
import logging
import tempfile
import asyncio
from datetime import date, timedelta
from dotenv import load_dotenv

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)
from groq import Groq
import requests

load_dotenv()

TELEGRAM_TOKEN      = os.environ["TELEGRAM_BOT_TOKEN"]
GROQ_API_KEY        = os.environ["GROQ_API_KEY"]
NOTION_TOKEN        = os.environ["NOTION_TOKEN"]
NOTION_DATABASE_ID  = os.environ["NOTION_DATABASE_ID"]
VK_TOKEN            = os.environ.get("VK_TOKEN", "")
VK_COMMUNITY        = os.environ.get("VK_COMMUNITY", "")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "")

groq_client = Groq(api_key=GROQ_API_KEY)

logging.basicConfig(format="%(asctime)s  %(levelname)s  %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

TASKS_BTN   = "📋 Задачи на сегодня"
PUBLISH_BTN = "📢 Публикация"

FORMAT_PLATFORMS = {
    "Короткий ролик 9:16":    ["Instagram", "Tik-tok", "YouTube", "VK", "Дзен"],
    "Длинное видео 16:9":     ["YouTube", "VK", "Дзен"],
    "Текстовый пост + фото":  ["Instagram", "VK", "Telegram", "Дзен", "YouTube"],
    "Инфографика=карусель":   ["Instagram", "VK", "Pinterest", "Tik-tok", "Telegram"],
    "Сторис":                 ["Instagram", "VK", "Telegram", "Tik-tok"],
}

EXTRA_QUESTION = {
    "Длинное видео 16:9":    "В формате подкаста?",
    "Короткий ролик 9:16":   "Закрепить как хайлайтс?",
    "Текстовый пост + фото": "Закрепить как хайлайтс?",
    "Инфографика=карусель":  "Закрепить как хайлайтс?",
    "Сторис":                "Закрепить как хайлайтс?",
}

THEMES = ["Экспертный", "Личный", "Развлекательный"]

_vk_owner_id: int = 0


def _resolve_vk_owner() -> int:
    direct = os.environ.get("VK_OWNER_ID", "")
    if direct:
        return int(direct)
    if not VK_TOKEN or not VK_COMMUNITY:
        return 0
    try:
        resp = requests.get(
            "https://api.vk.com/method/utils.resolveScreenName",
            params={"screen_name": VK_COMMUNITY, "access_token": VK_TOKEN, "v": "5.199"},
            timeout=10,
        )
        obj = resp.json().get("response", {})
        if obj.get("type") == "group":
            return -int(obj["object_id"])
        return int(obj.get("object_id", 0))
    except Exception as exc:
        log.warning("VK resolve failed: %s", exc)
        return 0


def main_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton(TASKS_BTN), KeyboardButton(PUBLISH_BTN)]],
        resize_keyboard=True,
    )


def format_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Короткий ролик 9:16",   callback_data="fmt:Короткий ролик 9:16")],
        [InlineKeyboardButton("Длинное видео 16:9",    callback_data="fmt:Длинное видео 16:9")],
        [InlineKeyboardButton("Текстовый пост + фото", callback_data="fmt:Текстовый пост + фото")],
        [InlineKeyboardButton("Инфографика=карусель",  callback_data="fmt:Инфографика=карусель")],
        [InlineKeyboardButton("Сторис",                callback_data="fmt:Сторис")],
    ])


def yes_no_keyboard(fmt):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Да",  callback_data="extra:yes:" + fmt),
        InlineKeyboardButton("Нет", callback_data="extra:no:"  + fmt),
    ]])


def theme_keyboard(selected):
    rows = []
    for t in THEMES:
        label = ("[+] " if t in selected else "") + t
        rows.append([InlineKeyboardButton(label, callback_data="theme:" + t)])
    rows.append([InlineKeyboardButton("Далее →", callback_data="theme_done")])
    return InlineKeyboardMarkup(rows)


def date_keyboard():
    today = date.today()
    rows = [
        [
            InlineKeyboardButton("Сегодня", callback_data="date:" + today.isoformat()),
            InlineKeyboardButton("Завтра",  callback_data="date:" + (today + timedelta(1)).isoformat()),
        ],
        [
            InlineKeyboardButton("+2 дня",  callback_data="date:" + (today + timedelta(2)).isoformat()),
            InlineKeyboardButton("+3 дня",  callback_data="date:" + (today + timedelta(3)).isoformat()),
            InlineKeyboardButton("+7 дней", callback_data="date:" + (today + timedelta(7)).isoformat()),
        ],
        [InlineKeyboardButton("Пропустить", callback_data="date:skip")],
    ]
    return InlineKeyboardMarkup(rows)


def skip_ref_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("Пропустить", callback_data="ref:skip")]])


def publish_platform_keyboard(selected):
    rows = []
    if TELEGRAM_CHANNEL_ID:
        icon = "[+] " if "tg" in selected else ""
        rows.append([InlineKeyboardButton(icon + "Telegram канал", callback_data="pub_plat:tg")])
    if _vk_owner_id:
        icon = "[+] " if "vk" in selected else ""
        rows.append([InlineKeyboardButton(icon + "VK", callback_data="pub_plat:vk")])
    rows.append([InlineKeyboardButton("Опубликовать", callback_data="pub_go")])
    rows.append([InlineKeyboardButton("Отмена",       callback_data="pub_cancel")])
    return InlineKeyboardMarkup(rows)

def upload_photo_vk(path):
    r1 = requests.get(
        "https://api.vk.com/method/photos.getWallUploadServer",
        params={"owner_id": _vk_owner_id, "access_token": VK_TOKEN, "v": "5.199"},
        timeout=15,
    )
    upload_url = r1.json()["response"]["upload_url"]
    with open(path, "rb") as f:
        r2 = requests.post(upload_url, files={"photo": ("photo.jpg", f, "image/jpeg")}, timeout=60)
    d = r2.json()
    r3 = requests.post(
        "https://api.vk.com/method/photos.saveWallPhoto",
        data={
            "owner_id":     _vk_owner_id,
            "server":       d["server"],
            "photo":        d["photo"],
            "hash":         d["hash"],
            "access_token": VK_TOKEN,
            "v":            "5.199",
        },
        timeout=15,
    )
    info = r3.json()["response"][0]
    return "photo" + str(info["owner_id"]) + "_" + str(info["id"])


def upload_video_vk(path):
    import time
    r1 = requests.post(
        "https://api.vk.com/method/video.save",
        data={"wallpost": 1, "access_token": VK_TOKEN, "v": "5.199"},
        timeout=15,
    )
    d = r1.json()["response"]
    upload_url = d["upload_url"]
    video_id   = "video" + str(_vk_owner_id) + "_" + str(d["video_id"])
    with open(path, "rb") as f:
        requests.post(upload_url, files={"video_file": ("video.mp4", f, "video/mp4")}, timeout=300)
    time.sleep(3)
    return video_id


def publish_vk(text, attachment=None):
    data = {
        "owner_id":     _vk_owner_id,
        "message":      text,
        "access_token": VK_TOKEN,
        "v":            "5.199",
    }
    if attachment:
        data["attachments"] = attachment
    resp = requests.post("https://api.vk.com/method/wall.post", data=data, timeout=30)
    d = resp.json()
    if "error" in d:
        raise Exception(d["error"].get("error_msg", str(d["error"])))
    post_id = d["response"]["post_id"]
    owner   = abs(_vk_owner_id)
    return "https://vk.com/wall-" + str(owner) + "_" + str(post_id)


async def publish_telegram(bot, text):
    msg     = await bot.send_message(chat_id=int(TELEGRAM_CHANNEL_ID), text=text)
    channel = TELEGRAM_CHANNEL_ID.replace("-100", "")
    return "https://t.me/c/" + channel + "/" + str(msg.message_id)


def transcribe(path):
    with open(path, "rb") as f:
        result = groq_client.audio.transcriptions.create(
            file=(os.path.basename(path), f.read()),
            model="whisper-large-v3-turbo",
            language="ru",
            response_format="text",
        )
    return result


def generate_title(idea_text):
    resp = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {
                "role": "system",
                "content": (
                    "Ты помощник контент-мейкера. Придумай короткое цепляющее "
                    "название видео до 60 символов на основе идеи. "
                    "Отвечай ТОЛЬКО названием, без кавычек и объяснений."
                ),
            },
            {"role": "user", "content": idea_text},
        ],
        max_tokens=100,
    )
    return resp.choices[0].message.content.strip()


def push_to_notion(title, body, formats, platforms, themes=None, pub_date=None, reference=None):
    headers = {
        "Authorization":  "Bearer " + NOTION_TOKEN,
        "Content-Type":   "application/json",
        "Notion-Version": "2022-06-28",
    }
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
        "children": [
            {
                "object": "block",
                "type":   "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": body[:2000]}}]
                },
            }
        ],
    }
    resp = requests.post("https://api.notion.com/v1/pages", headers=headers, json=payload)
    data = resp.json()
    if resp.status_code != 200:
        raise Exception("Notion: " + data.get("message", str(data)))
    return data.get("url", "")


def get_tasks_today():
    today   = date.today().isoformat()
    headers = {
        "Authorization":  "Bearer " + NOTION_TOKEN,
        "Content-Type":   "application/json",
        "Notion-Version": "2022-06-28",
    }
    payload = {
        "filter": {"property": "Live Date", "date": {"equals": today}},
        "sorts":  [{"property": "Live Date", "direction": "ascending"}],
    }
    resp = requests.post(
        "https://api.notion.com/v1/databases/" + NOTION_DATABASE_ID + "/query",
        headers=headers,
        json=payload,
    )
    data = resp.json()
    if resp.status_code != 200:
        raise Exception("Notion: " + data.get("message", str(data)))
    return data.get("results", [])


async def _do_save(edit_fn, context):
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
        url   = await loop.run_in_executor(
            None, push_to_notion, title, body, formats, platforms, themes, pub_date, reference
        )
        parts = ["Сохранено!", "", "Название: " + title, "Формат: " + ", ".join(formats)]
        if themes:
            parts.append("Тема: " + ", ".join(themes))
        if pub_date:
            parts.append("Дата: " + pub_date)
        if url:
            parts.extend(["", url])
        await edit_fn("\n".join(parts))
    except Exception as exc:
        await edit_fn("Ошибка Notion: " + str(exc))

    context.user_data["state"]           = None
    context.user_data["selected_themes"] = set()


async def _init_idea(context, raw_idea):
    context.user_data["raw_idea"]        = raw_idea
    context.user_data["selected_themes"] = set()
    context.user_data["state"]           = None
    loop = asyncio.get_event_loop()
    try:
        gen_title = await loop.run_in_executor(None, generate_title, raw_idea)
    except Exception:
        gen_title = raw_idea[:60]
    context.user_data["generated_title"] = gen_title
    return gen_title


async def cmd_start(update, context):
    context.user_data.clear()
    await update.message.reply_text(
        "Привет! Отправь идею для ролика голосом или текстом.\n\nИли нажми Публикация.",
        reply_markup=main_keyboard(),
    )


async def cmd_cancel(update, context):
    context.user_data.clear()
    await update.message.reply_text("Отменено. Отправь идею или нажми Публикация.", reply_markup=main_keyboard())


async def on_tasks_today(update, context):
    await update.message.reply_text("Загружаю задачи...")
    try:
        loop  = asyncio.get_event_loop()
        pages = await loop.run_in_executor(None, get_tasks_today)
        if not pages:
            await update.message.reply_text("На сегодня задач нет!")
            return
        lines = ["Задачи на сегодня (" + date.today().strftime("%d.%m") + "):", ""]
        for page in pages:
            props       = page.get("properties", {})
            title_parts = props.get("Video Title", {}).get("title", [])
            title       = "".join(t.get("plain_text", "") for t in title_parts) or "Без названия"
            status_obj  = props.get("Status", {}).get("select") or {}
            status      = status_obj.get("name", "")
            url         = page.get("url", "")
            line        = "- " + title
            if status:
                line += " [" + status + "]"
            if url:
                line += "\n  " + url
            lines.append(line)
        await update.message.reply_text("\n".join(lines))
    except Exception as exc:
        await update.message.reply_text("Ошибка: " + str(exc))


async def on_publish_mode(update, context):
    context.user_data["state"]              = "waiting_publish_content"
    context.user_data["publish_media_fid"]  = None
    context.user_data["publish_media_type"] = None
    await update.message.reply_text("Отправь текст поста (можно с фото или видео):")


async def on_media_publish(update, context):
    if context.user_data.get("state") != "waiting_publish_content":
        return
    msg = update.message
    caption = msg.caption or ""
    if msg.photo:
        context.user_data["publish_media_fid"]  = msg.photo[-1].file_id
        context.user_data["publish_media_type"] = "photo"
    elif msg.video:
        context.user_data["publish_media_fid"]  = msg.video.file_id
        context.user_data["publish_media_type"] = "video"
    context.user_data["publish_text"]      = caption
    context.user_data["state"]             = None
    context.user_data["publish_platforms"] = set()
    await update.message.reply_text(
        "Выбери платформы для публикации:",
        reply_markup=publish_platform_keyboard(set()),
    )


async def on_voice(update, context):
    msg = await update.message.reply_text("Транскрибирую...")
    try:
        voice_file = await context.bot.get_file(update.message.voice.file_id)
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            await voice_file.download_to_drive(tmp.name)
            path = tmp.name
        loop      = asyncio.get_event_loop()
        text      = await loop.run_in_executor(None, transcribe, path)
        os.unlink(path)
        gen_title = await _init_idea(context, text)
        display   = "Транскрипция:\n\n" + text + "\n\nНазвание: " + gen_title + "\n\nВыбери формат контента:"
        await msg.edit_text(display, reply_markup=format_keyboard())
    except Exception as exc:
        await msg.edit_text("Ошибка: " + str(exc))


async def on_text(update, context):
    state = context.user_data.get("state")

    if state == "waiting_reference":
        context.user_data["reference"] = update.message.text
        context.user_data["state"]     = None
        msg = await update.message.reply_text("...")
        await _do_save(msg.edit_text, context)
        return

    if state == "waiting_publish_content":
        context.user_data["publish_text"]      = update.message.text
        context.user_data["state"]             = None
        context.user_data["publish_platforms"] = set()
        await update.message.reply_text(
            "Выбери платформы для публикации:",
            reply_markup=publish_platform_keyboard(set()),
        )
        return

    raw       = update.message.text
    gen_title = await _init_idea(context, raw)
    display   = "Идея:\n" + raw + "\n\nНазвание: " + gen_title + "\n\nВыбери формат контента:"
    await update.message.reply_text(display, reply_markup=format_keyboard())


async def on_format(update, context):
    query = update.callback_query
    await query.answer()
    fmt = query.data[4:]
    context.user_data["fmt"] = fmt
    if fmt in EXTRA_QUESTION:
        await query.edit_message_text(
            "Формат: " + fmt + "\n\n" + EXTRA_QUESTION[fmt],
            reply_markup=yes_no_keyboard(fmt),
        )
    else:
        context.user_data["formats"]   = [fmt]
        context.user_data["platforms"] = list(FORMAT_PLATFORMS.get(fmt, []))
        selected = context.user_data.get("selected_themes", set())
        await query.edit_message_text("Выбери тему (можно несколько):", reply_markup=theme_keyboard(selected))


async def on_extra(update, context):
    query    = update.callback_query
    await query.answer()
    parts    = query.data.split(":", 2)
    answer   = parts[1]
    fmt      = parts[2]
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
    theme    = query.data[6:]
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
    val = query.data[5:]
    context.user_data["pub_date"] = None if val == "skip" else val
    context.user_data["state"]    = "waiting_reference"
    await query.edit_message_text(
        "Отправь ссылку на референс или пропусти:",
        reply_markup=skip_ref_keyboard(),
    )


async def on_ref_skip(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data["reference"] = None
    context.user_data["state"]     = None
    await _do_save(query.edit_message_text, context)


async def on_pub_plat(update, context):
    query    = update.callback_query
    await query.answer()
    key      = query.data[9:]
    selected = context.user_data.get("publish_platforms", set())
    if key in selected:
        selected.discard(key)
    else:
        selected.add(key)
    context.user_data["publish_platforms"] = selected
    await query.edit_message_reply_markup(reply_markup=publish_platform_keyboard(selected))


async def on_pub_go(update, context):
    query    = update.callback_query
    await query.answer()
    selected = context.user_data.get("publish_platforms", set())
    text     = context.user_data.get("publish_text", "")
    if not selected:
        await query.answer("Выбери хотя бы одну платформу!", show_alert=True)
        return
    await query.edit_message_text("Публикую...")
    results      = []
    loop         = asyncio.get_event_loop()
    media_fid    = context.user_data.get("publish_media_fid")
    media_type   = context.user_data.get("publish_media_type")

    if "vk" in selected and _vk_owner_id:
        try:
            attachment = None
            if media_fid and media_type:
                tg_file = await context.bot.get_file(media_fid)
                suffix  = ".jpg" if media_type == "photo" else ".mp4"
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    await tg_file.download_to_drive(tmp.name)
                    tmp_path = tmp.name
                if media_type == "photo":
                    attachment = await loop.run_in_executor(None, upload_photo_vk, tmp_path)
                else:
                    attachment = await loop.run_in_executor(None, upload_video_vk, tmp_path)
                os.unlink(tmp_path)
            url = await loop.run_in_executor(None, publish_vk, text, attachment)
            results.append("VK: " + url)
        except Exception as exc:
            results.append("VK ошибка: " + str(exc))
    if "tg" in selected and TELEGRAM_CHANNEL_ID:
        try:
            url = await publish_telegram(context.bot, text)
            results.append("Telegram: " + url)
        except Exception as exc:
            results.append("Telegram ошибка: " + str(exc))
    await query.edit_message_text("Готово!\n\n" + "\n".join(results))
    context.user_data["publish_platforms"]  = set()
    context.user_data["publish_media_fid"]  = None
    context.user_data["publish_media_type"] = None
    context.user_data["state"]              = None


async def on_pub_cancel(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data["publish_platforms"]  = set()
    context.user_data["publish_media_fid"]  = None
    context.user_data["publish_media_type"] = None
    context.user_data["state"]              = None
    await query.edit_message_text("Публикация отменена.")


def main():
    global _vk_owner_id
    _vk_owner_id = _resolve_vk_owner()
    log.info("VK owner_id: %s", _vk_owner_id)

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(MessageHandler(filters.Text([TASKS_BTN]),                     on_tasks_today))
    app.add_handler(MessageHandler(filters.Text([PUBLISH_BTN]),                   on_publish_mode))
    app.add_handler(MessageHandler(filters.VOICE,                                 on_voice))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO,                 on_media_publish))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,               on_text))
    app.add_handler(CallbackQueryHandler(on_format,     pattern=r"^fmt:"))
    app.add_handler(CallbackQueryHandler(on_extra,      pattern=r"^extra:"))
    app.add_handler(CallbackQueryHandler(on_theme,      pattern=r"^theme"))
    app.add_handler(CallbackQueryHandler(on_date,       pattern=r"^date:"))
    app.add_handler(CallbackQueryHandler(on_ref_skip,   pattern=r"^ref:skip$"))
    app.add_handler(CallbackQueryHandler(on_pub_plat,   pattern=r"^pub_plat:"))
    app.add_handler(CallbackQueryHandler(on_pub_go,     pattern=r"^pub_go$"))
    app.add_handler(CallbackQueryHandler(on_pub_cancel, pattern=r"^pub_cancel$"))
    log.info("Бот запущен...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
