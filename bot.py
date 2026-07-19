#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Blog Tracker Bot - голос/текст → Notion + уведомления команде
"""

import os
import logging
import tempfile
import asyncio
from datetime import date, timedelta, time as dt_time
from dotenv import load_dotenv

import pytz
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

# Telegram ID участников
ASSISTANT_TG_ID = int(os.environ.get("ASSISTANT_TG_ID", "750311841"))
EDITOR_TG_ID    = int(os.environ.get("EDITOR_TG_ID",    "5599078862"))

# Notion user ID ассистента для назначения в карточке
# Получить: GET https://api.notion.com/v1/users (с NOTION_TOKEN)
NOTION_ASSISTANT_USER_ID = os.environ.get("NOTION_ASSISTANT_USER_ID", "")

MOSCOW_TZ = pytz.timezone("Europe/Moscow")

groq_client = Groq(api_key=GROQ_API_KEY)

logging.basicConfig(format="%(asctime)s  %(levelname)s  %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

TASKS_BTN     = "📋 Задачи на сегодня"
PUBLISH_BTN   = "📢 Публикация"
SEND_LINK_BTN = "📎 Отправить ссылку на видео"

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


# ─── Notion helpers ───────────────────────────────────────────────────────────

def _notion_headers():
    return {
        "Authorization":  "Bearer " + NOTION_TOKEN,
        "Content-Type":   "application/json",
        "Notion-Version": "2022-06-28",
    }


def _page_title(page):
    parts = page.get("properties", {}).get("Video Title", {}).get("title", [])
    return "".join(t.get("plain_text", "") for t in parts) or "Без названия"


def _page_url(page):
    return page.get("url", "")


def _page_reference(page):
    ref = page.get("properties", {}).get("Референс", {})
    return ref.get("url") or ""


def _page_live_date(page):
    d = page.get("properties", {}).get("Live Date", {}).get("date")
    return d.get("start", "") if d else ""


def get_editing_pages():
    """Все карточки со статусом Editing."""
    payload = {
        "filter": {"property": "Status", "select": {"equals": "Editing"}},
        "sorts":  [{"property": "Live Date", "direction": "ascending"}],
    }
    resp = requests.post(
        "https://api.notion.com/v1/databases/" + NOTION_DATABASE_ID + "/query",
        headers=_notion_headers(), json=payload,
    )
    data = resp.json()
    if resp.status_code != 200:
        raise Exception("Notion: " + data.get("message", str(data)))
    return data.get("results", [])


def get_pages_by_date_status(target_date: str, status: str):
    """Карточки по дате выкладки и статусу."""
    payload = {
        "filter": {
            "and": [
                {"property": "Live Date", "date":   {"equals": target_date}},
                {"property": "Status",    "select": {"equals": status}},
            ]
        },
        "sorts": [{"property": "Live Date", "direction": "ascending"}],
    }
    resp = requests.post(
        "https://api.notion.com/v1/databases/" + NOTION_DATABASE_ID + "/query",
        headers=_notion_headers(), json=payload,
    )
    data = resp.json()
    if resp.status_code != 200:
        raise Exception("Notion: " + data.get("message", str(data)))
    return data.get("results", [])


def update_notion_scheduled(page_id: str, yd_link: str):
    """Записать ссылку на ЯД, поставить Scheduled, назначить ассистенту."""
    properties = {
        "Готовое видео": {"url": yd_link},
        "Status":        {"select": {"name": "Scheduled"}},
    }
    if NOTION_ASSISTANT_USER_ID:
        properties["Assistant"] = {
            "people": [{"object": "user", "id": NOTION_ASSISTANT_USER_ID}]
        }
    resp = requests.patch(
        "https://api.notion.com/v1/pages/" + page_id,
        headers=_notion_headers(), json={"properties": properties},
    )
    data = resp.json()
    if resp.status_code != 200:
        raise Exception("Notion: " + data.get("message", str(data)))
    return data.get("url", "")


def push_to_notion(title, body, formats, platforms, themes=None, pub_date=None, reference=None):
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
    resp = requests.post(
        "https://api.notion.com/v1/pages",
        headers=_notion_headers(), json=payload,
    )
    data = resp.json()
    if resp.status_code != 200:
        raise Exception("Notion: " + data.get("message", str(data)))
    return data.get("url", "")


def get_tasks_today():
    today = date.today().isoformat()
    payload = {
        "filter": {"property": "Live Date", "date": {"equals": today}},
        "sorts":  [{"property": "Live Date", "direction": "ascending"}],
    }
    resp = requests.post(
        "https://api.notion.com/v1/databases/" + NOTION_DATABASE_ID + "/query",
        headers=_notion_headers(), json=payload,
    )
    data = resp.json()
    if resp.status_code != 200:
        raise Exception("Notion: " + data.get("message", str(data)))
    return data.get("results", [])


# ─── VK helpers ───────────────────────────────────────────────────────────────

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
    msg     = await bot.send_message(capt_id=int(TELEGRAM_CHANNEL_ID), text=text)
    channel = TELEGRAM_CHANNEL_ID.replace("-100", "")
    return "https://t.me/c/" + channel + "/" + str(msg.message_id)


# ─── Audio ────────────────────────────────────────────────────────────────────

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


# ─── Keyboards ────────────────────────────────────────────────────────────────

def main_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton(TASKS_BTN), KeyboardButton(PUBLISH_BTN)]],
        resize_keyboard=True,
    )


def editor_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton(SEND_LINK_BTN)]],
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


def editing_pages_keyboard(pages):
    rows = []
    for page in pages[:10]:
        title     = _page_title(page)
        live_date = _page_live_date(page)
        label     = title[:30]
        if live_date:
            label += " (" + live_date + ")"
        rows.append([InlineKeyboardButton(label, callback_data="editor_card:" + page["id"])])
    rows.append([InlineKeyboardButton("Отмена", callback_data="editor_cancel")])
    return InlineKeyboardMarkup(rows)


# ─── Cron jobs ────────────────────────────────────────────────────────────────

async def notify_tomorrow_videos(context: ContextTypes.DEFAULT_TYPE):
    """9:00 МСК — уведомляет монтажёра о завтрашних видео в статусе Editing."""
    tomorrow = (date.today() + timedelta(1)).isoformat()
    loop = asyncio.get_event_loop()
    try:
        pages = await loop.run_in_executor(
            None, get_pages_by_date_status, tomorrow, "Editing"
        )
    except Exception as exc:
        log.error("notify_tomorrow: %s", exc)
        return
    if not pages:
        return
    for page in pages:
        title    = _page_title(page)
        ref      = _page_reference(page)
        page_url = _page_url(page)
        parts    = [
            "Приветик, завтра выходит видео:",
            "",
            "📹 " + title,
        ]
        if ref:
            parts.append("🔗 Референс: " + ref)
        if page_url:
            parts.append("📋 Карточка: " + page_url)
        parts += ["", "Если готово — отправь ссылочку на Яндекс.Диск. Благодарствую 🫶🏻"]
        try:
            await context.bot.send_message(chat_id=EDITOR_TG_ID, text="\n".join(parts))
        except Exception as exc:
            log.error("notify_tomorrow send failed: %s", exc)


async def notify_today_morning(context: ContextTypes.DEFAULT_TYPE):
    """9:05 МСК — Scheduled → ассистенту, Editing → монтажёру."""
    today = date.today().isoformat()
    loop  = asyncio.get_event_loop()

    # Scheduled → ассистенту
    try:
        scheduled = await loop.run_in_executor(
            None, get_pages_by_date_status, today, "Scheduled"
        )
    except Exception as exc:
        log.error("notify_today_morning (scheduled): %s", exc)
        scheduled = []

    if scheduled:
        lines = ["Сегодня к публикации:", ""]
        for page in scheduled:
            title    = _page_title(page)
            page_url = _page_url(page)
            lines.append("📹 " + title)
            if page_url:
                lines.append("   " + page_url)
        try:
            await context.bot.send_message(chat_id=ASSISTANT_TG_ID, text="\n".join(lines))
        except Exception as exc:
            log.error("notify_today_morning: assistant send failed: %s", exc)

    # Editing → монтажёру
    try:
        editing = await loop.run_in_executor(
            None, get_pages_by_date_status, today, "Editing"
        )
    except Exception as exc:
        log.error("notify_today_morning (editing): %s", exc)
        editing = []

    for page in editing:
        title    = _page_title(page)
        page_url = _page_url(page)
        parts = [
            "Приветик, у нас сегодня видео выходит, а ссылочки не вижу 🥲",
            "",
            "📹 " + title,
        ]
        if page_url:
            parts.append("📋 " + page_url)
        parts += ["", "Прикрепи пожалуйста ссылочку на ЯД с готовым видео. Благодарствую 🫶🏻"]
        try:
            await context.bot.send_message(chat_id=EDITOR_TG_ID, text="\n".join(parts))
        except Exception as exc:
            log.error("notify_today_morning: editor send failed: %s", exc)


async def notify_today_afternoon(context: ContextTypes.DEFAULT_TYPE):
    """14:00 МСК — если всё ещё Editing, уведомить ассистента."""
    today = date.today().isoformat()
    loop  = asyncio.get_event_loop()
    try:
        editing = await loop.run_in_executor(
            None, get_pages_by_date_status, today, "Editing"
        )
    except Exception as exc:
        log.error("notify_today_afternoon: %s", exc)
        return
    if not editing:
        return
    try:
        await context.bot.send_message(
            chat_id=ASSISTANT_TG_ID,
            text="У нас на сегодня не готово видео, сходи пожалуйста к Вере за уточнениями",
        )
    except Exception as exc:
        log.error("notify_today_afternoon: send failed: %s", exc)


# ─── State helpers ────────────────────────────────────────────────────────────

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


# ─── Handlers ─────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    user_id = update.effective_user.id
    if user_id == EDITOR_TG_ID:
        await update.message.reply_text(
            "Привет! Нажми кнопку ниже, чтобы прикрепить ссылку на готовое видео.",
            reply_markup=editor_keyboard(),
        )
    else:
        await update.message.reply_text(
            "Привет! Отправь идею для ролика голосом или текстом.\n\nИли нажми Публикация.",
            reply_markup=main_keyboard(),
        )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    user_id = update.effective_user.id
    if user_id == EDITOR_TG_ID:
        await update.message.reply_text("Отменено.", reply_markup=editor_keyboard())
    else:
        await update.message.reply_text(
            "Отменено. Отправь идею или нажми Публикация.", reply_markup=main_keyboard()
        )


async def on_tasks_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
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


async def on_publish_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["state"]              = "waiting_publish_content"
    context.user_data["publish_media_fid"]  = None
    context.user_data["publish_media_type"] = None
    await update.message.reply_text("Отправь текст поста (можно с фото или видео):")


async def on_send_link_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Монтажёр нажал кнопку → показываем список Editing карточек."""
    await update.message.reply_text("Загружаю список видео...")
    loop = asyncio.get_event_loop()
    try:
        pages = await loop.run_in_executor(None, get_editing_pages)
    except Exception as exc:
        await update.message.reply_text("Ошибка: " + str(exc))
        return
    if not pages:
        await update.message.reply_text("Нет видео в статусе Editing.")
        return
    await update.message.reply_text(
        "Выбери видео, к которому хочешь прикрепить ссылку:",
        reply_markup=editing_pages_keyboard(pages),
    )


async def on_media_publish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("state") != "waiting_publish_content":
        return
    msg     = update.message
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


async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
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


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state   = context.user_data.get("state")
    user_id = update.effective_user.id

    # Монтажёр прислал ссылку на ЯД
    if state == "editor_awaiting_link":
        yd_link  = update.message.text.strip()
        page_id  = context.user_data.get("editor_page_id", "")
        page_ttl = context.user_data.get("editor_page_title", "")
        if not yd_link.startswith("http"):
            await update.message.reply_text(
                "Пожалуйста, отправь корректную ссылку (начинается с http)."
            )
            return
        await update.message.reply_text("Обновляю карточку в Notion...")
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, update_notion_scheduled, page_id, yd_link)
            await update.message.reply_text(
                "✅ Готово! Ссылка прикреплена, статус → Scheduled.\n\n" + page_ttl,
                reply_markup=editor_keyboard(),
            )
        except Exception as exc:
            await update.message.reply_text(
                "Ошибка Notion: " + str(exc), reply_markup=editor_keyboard()
            )
        context.user_data["state"]             = None
        context.user_data["editor_page_id"]    = None
        context.user_data["editor_page_title"] = None
        return

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

    # Монтажёр без активного состояния
    if user_id == EDITOR_TG_ID:
        await update.message.reply_text(
            "Нажми кнопку ниже, чтобы прикрепить ссылку на видео.",
            reply_markup=editor_keyboard(),
        )
        return

    # Обычный пользователь — новая идея
    raw       = update.message.text
    gen_title = await _init_idea(context, raw)
    display   = "Идея:\n" + raw + "\n\nНазвание: " + gen_title + "\n\nВыбери формат контента:"
    await update.message.reply_text(display, reply_markup=format_keyboard())


# ─── Callback handlers ────────────────────────────────────────────────────────

async def on_editor_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Монтажёр выбрал карточку из списка."""
    query = update.callback_query
    await query.answer()
    page_id = query.data[len("editor_card:"):]
    # Загружаем список чтобы получить заголовок
    loop = asyncio.get_event_loop()
    try:
        pages = await loop.run_in_executor(None, get_editing_pages)
        page  = next((p for p in pages if p["id"] == page_id), None)
        title = _page_title(page) if page else page_id
    except Exception:
        title = page_id
    context.user_data["editor_page_id"]    = page_id
    context.user_data["editor_page_title"] = title
    context.user_data["state"]             = "editor_awaiting_link"
    await query.edit_message_text(
        "Выбрано: " + title + "\n\nОтправь ссылку на Яндекс.Диск с готовым видео:"
    )


async def on_editor_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["state"] = None
    await query.edit_message_text("Отменено.")


async def on_format(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        await query.edit_message_text(
            "Выбери тему (можно несколько):", reply_markup=theme_keyboard(selected)
        )


async def on_extra(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts     = query.data.split(":", 2)
    answer    = parts[1]
    fmt       = parts[2]
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
    await query.edit_message_text(
        "Выбери тему (можно несколько):", reply_markup=theme_keyboard(selected)
    )


async def on_theme(update: Update, context: ContextTypes.DEFAULT_TYPE):
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


async def on_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    val = query.data[5:]
    context.user_data["pub_date"] = None if val == "skip" else val
    context.user_data["state"]    = "waiting_reference"
    await query.edit_message_text(
        "Отправь ссылку на референс или пропусти:",
        reply_markup=skip_ref_keyboard(),
    )


async def on_ref_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["reference"] = None
    context.user_data["state"]     = None
    await _do_save(query.edit_message_text, context)


async def on_pub_plat(update: Update, context: ContextTypes.DEFAULT_TYPE):
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


async def on_pub_go(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query    = update.callback_query
    await query.answer()
    selected = context.user_data.get("publish_platforms", set())
    uUxt     = context.user_data.get("publish_uUxt", "")
    if not selected:
        await query.answer("Выбери хотя бы одну платформу!", show_alert=True)
        return
    await query.edit_message_text("Публикую...")
    results    = []
    loop       = asyncio.get_event_loop()
    media_fid  = context.user_data.get("publish_media_fid")
    media_type = context.user_data.get("publish_media_type")

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
                    attachment = await loop.run_in_executor(None, upload_fideo_fk, tmp_path)
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


async def on_pub_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["publish_platforms"]  = set()
    context.user_data["publish_media_fid"]  = None
    context.user_data["publish_media_type"] = None
    context.user_data["state"]              = None
    await query.edit_message_text("Публикация отменена.")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    global _vk_owner_id
    _vk_owner_id = _resolve_vk_owner()
    log.info("VK owner_id: %s", _vk_owner_id)

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Cron-уведомления (МСК)
    jq = app.job_queue
    jq.run_daily(notify_tomorrow_videos, time=dt_time(9, 0,  tzinfo=MOSCOW_TZ))
    jq.run_daily(notify_today_morning,   time=dt_time(9, 5,  tzinfo=MOSCOW_TZ))
    jq.run_daily(notify_today_afternoon, time=dt_time(14, 0, tzinfo=MOSCOW_TZ))

    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(MessageHandler(filters.TExt([TASKS_BTN]),     on_tasks_today))
    app.add_handler(MessageHandler(filters.Text([PUBLISH_BTN]),   on_publish_mode))
    app.add_handler(MessageHandler(filters.Text([SEND_LINK_BTN]), on_send_link_mode))
    app.add_handler(MessageHandler(filters.VOICE,                 on_voice))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO, on_media_publish))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(CallbackQueryHandler(on_editor_card,   pattern=r"^editor_card:"))
    app.add_handler(CallbackQueryHandler(on_editor_cancel, pattern=r"^editor_cancel$"))
    app.add_handler(CallbackQueryHandler(on_format,        pattern=r"^fmt:"))
    app.add_handler(CallbackQueryHandler(on_extra,         pattern=r"^extra:"))
    app.add_handler(CallbackQueryHandler(on_theme,         pattern=r"^theme"))
    app.add_handler(CallbackQueryHandler(on_date,          pattern=r"^date:"))
    app.add_handler(CallbackQueryHandler(on_ref_skip,      pattern=r"^ref:skip$"))
    app.add_handler(CallbackQueryHandler(on_pub_plat,      pattern=r"^pub_plat:"))
    app.add_handler(CallbackQueryHandler(on_pub_go,        pattern=r"^pub_go$"))
    app.add_handler(CallbackQueryHandler(on_pub_cancel,    pattern=r"^pub_cancel$"))
    log.info("Бот запущен...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
