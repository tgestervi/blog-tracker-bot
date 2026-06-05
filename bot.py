import os
import asyncio
import logging
import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, filters, ContextTypes
)
from notion_client import Client
import pytz

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_DB_ID = os.environ["NOTION_DATABASE_ID"]
CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"])
TIMEZONE = os.environ.get("TIMEZONE", "Europe/Moscow")

notion = Client(auth=NOTION_TOKEN)

# --- Conversation states ---
TITLE, LIVE_DATE, PLATFORM, FORMAT_STATE, THEMES, VORONKA, URL_STATE = range(7)

# --- Field options (from your Notion database) ---
PLATFORM_OPTIONS = ["Telegram", "Instagram", "YouTube", "TikTok"]
FORMAT_OPTIONS = ["Текстовый пост + фото", "Видео", "Reels", "Карусель", "Сторис", "Текстовый пост"]
THEMES_OPTIONS = ["Экспертный", "Личный", "Продающий", "Развлекательный"]
VORONKA_OPTIONS = [
    "Консультация B2B",
    "Телеграмм",
    "Призвание контент-креатор",
    "Консультация B2C",
    "Лид-магнит в бота",
]


# --- Keyboard helpers ---

def multiselect_kb(options, selected, prefix):
    rows = []
    for opt in options:
        label = f"✅ {opt}" if opt in selected else opt
        rows.append([InlineKeyboardButton(label, callback_data=f"{prefix}|{opt}")])
    rows.append([
        InlineKeyboardButton("✓ Готово", callback_data=f"{prefix}|DONE"),
        InlineKeyboardButton("⏭ Пропустить", callback_data=f"{prefix}|SKIP"),
    ])
    return InlineKeyboardMarkup(rows)


def skip_kb(prefix):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ Пропустить", callback_data=f"{prefix}|SKIP")]
    ])


# --- /start ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Blog Tracker Bot*\n\n"
        "/new — добавить идею в Notion\n"
        "/today — что запланировано на сегодня\n"
        "/cancel — отменить ввод",
        parse_mode="Markdown"
    )


# --- /new: добавление идеи ---

async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("📝 Название поста:")
    return TITLE


async def recv_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["title"] = update.message.text.strip()
    await update.message.reply_text(
        "📅 Дата публикации (ДД.ММ.ГГГГ):",
        reply_markup=skip_kb("date")
    )
    return LIVE_DATE


async def recv_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        d = datetime.datetime.strptime(update.message.text.strip(), "%d.%m.%Y").date()
        context.user_data["live_date"] = d.isoformat()
    except ValueError:
        await update.message.reply_text(
            "❌ Формат: ДД.ММ.ГГГГ. Попробуй ещё раз:",
            reply_markup=skip_kb("date")
        )
        return LIVE_DATE
    return await ask_platform(update.message, context)


async def skip_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    context.user_data["live_date"] = None
    return await ask_platform(update.callback_query.message, context)


async def ask_platform(msg, context):
    context.user_data.setdefault("platform", [])
    await msg.reply_text(
        "📱 Платформа (можно выбрать несколько):",
        reply_markup=multiselect_kb(PLATFORM_OPTIONS, context.user_data["platform"], "platform")
    )
    return PLATFORM


async def handle_platform(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, val = q.data.split("|", 1)
    if val == "SKIP":
        context.user_data["platform"] = []
        return await ask_format(q.message, context)
    elif val == "DONE":
        return await ask_format(q.message, context)
    else:
        sel = context.user_data.setdefault("platform", [])
        sel.remove(val) if val in sel else sel.append(val)
        await q.edit_message_reply_markup(multiselect_kb(PLATFORM_OPTIONS, sel, "platform"))
        return PLATFORM


async def ask_format(msg, context):
    context.user_data.setdefault("format", [])
    await msg.reply_text(
        "🎬 Формат:",
        reply_markup=multiselect_kb(FORMAT_OPTIONS, context.user_data["format"], "format")
    )
    return FORMAT_STATE


async def handle_format(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, val = q.data.split("|", 1)
    if val in ("SKIP", "DONE"):
        if val == "SKIP":
            context.user_data["format"] = []
        return await ask_themes(q.message, context)
    sel = context.user_data.setdefault("format", [])
    sel.remove(val) if val in sel else sel.append(val)
    await q.edit_message_reply_markup(multiselect_kb(FORMAT_OPTIONS, sel, "format"))
    return FORMAT_STATE


async def ask_themes(msg, context):
    context.user_data.setdefault("themes", [])
    await msg.reply_text(
        "🏷 Темы:",
        reply_markup=multiselect_kb(THEMES_OPTIONS, context.user_data["themes"], "themes")
    )
    return THEMES


async def handle_themes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, val = q.data.split("|", 1)
    if val in ("SKIP", "DONE"):
        if val == "SKIP":
            context.user_data["themes"] = []
        return await ask_voronka(q.message, context)
    sel = context.user_data.setdefault("themes", [])
    sel.remove(val) if val in sel else sel.append(val)
    await q.edit_message_reply_markup(multiselect_kb(THEMES_OPTIONS, sel, "themes"))
    return THEMES


async def ask_voronka(msg, context):
    context.user_data.setdefault("voronka", [])
    await msg.reply_text(
        "🔀 Воронка / сюжетная линия:",
        reply_markup=multiselect_kb(VORONKA_OPTIONS, context.user_data["voronka"], "voronka")
    )
    return VORONKA


async def handle_voronka(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, val = q.data.split("|", 1)
    if val in ("SKIP", "DONE"):
        if val == "SKIP":
            context.user_data["voronka"] = []
        return await ask_url(q.message, context)
    sel = context.user_data.setdefault("voronka", [])
    sel.remove(val) if val in sel else sel.append(val)
    await q.edit_message_reply_markup(multiselect_kb(VORONKA_OPTIONS, sel, "voronka"))
    return VORONKA


async def ask_url(msg, context):
    await msg.reply_text("🔗 URL (ссылка на материал):", reply_markup=skip_kb("url"))
    return URL_STATE


async def recv_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["url"] = update.message.text.strip()
    return await save_to_notion(update.message, context)


async def skip_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    context.user_data["url"] = None
    return await save_to_notion(update.callback_query.message, context)


async def save_to_notion(msg, context):
    d = context.user_data

    props = {
        "Title": {"title": [{"text": {"content": d["title"]}}]},
        "Status": {"status": {"name": "Idea"}},
    }
    if d.get("live_date"):
        props["Live Date"] = {"date": {"start": d["live_date"]}}
    if d.get("platform"):
        props["Platform"] = {"multi_select": [{"name": p} for p in d["platform"]]}
    if d.get("format"):
        props["Формат"] = {"multi_select": [{"name": f} for f in d["format"]]}
    if d.get("themes"):
        props["Themes"] = {"multi_select": [{"name": t} for t in d["themes"]]}
    if d.get("voronka"):
        props["Воронка/сюж линия"] = {"multi_select": [{"name": v} for v in d["voronka"]]}
    if d.get("url"):
        props["URL"] = {"url": d["url"]}

    try:
        notion.pages.create(parent={"database_id": NOTION_DB_ID}, properties=props)

        lines = [f"✅ *{d['title']}* — добавлено в Notion!"]
        if d.get("live_date"):
            lines.append(f"📅 {d['live_date']}")
        if d.get("platform"):
            lines.append(f"📱 {', '.join(d['platform'])}")
        if d.get("format"):
            lines.append(f"🎬 {', '.join(d['format'])}")
        if d.get("themes"):
            lines.append(f"🏷 {', '.join(d['themes'])}")

        await msg.reply_text("\n".join(lines), parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Notion error: {e}")
        await msg.reply_text(f"❌ Ошибка при сохранении в Notion:\n{e}")

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Отменено.")
    return ConversationHandler.END


# --- /today: что запланировано ---

async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_digest(context.bot, update.effective_chat.id)


async def daily_digest_loop(bot):
    tz = pytz.timezone(TIMEZONE)
    while True:
        now = datetime.datetime.now(tz)
        target = now.replace(hour=9, minute=0, second=0, microsecond=0)
        if now >= target:
            target += datetime.timedelta(days=1)
        wait_seconds = (target - now).total_seconds()
        await asyncio.sleep(wait_seconds)
        try:
            await send_digest(bot, CHAT_ID)
        except Exception as e:
            logger.error(f"Digest error: {e}")


async def send_digest(bot, chat_id):
    today = datetime.date.today().isoformat()
    try:
        res = notion.databases.query(
            database_id=NOTION_DB_ID,
            filter={"property": "Live Date", "date": {"equals": today}}
        )
        pages = res.get("results", [])

        if not pages:
            await bot.send_message(chat_id, f"📅 На сегодня ({today}) ничего не запланировано.")
            return

        lines = [f"📅 *На сегодня — {today}:*\n"]
        for p in pages:
            pr = p["properties"]

            title_list = pr.get("Title", {}).get("title", [])
            title = title_list[0]["text"]["content"] if title_list else "—"

            platform = " · ".join(x["name"] for x in pr.get("Platform", {}).get("multi_select", []))
            fmt = " · ".join(x["name"] for x in pr.get("Формат", {}).get("multi_select", []))
            themes = " · ".join(x["name"] for x in pr.get("Themes", {}).get("multi_select", []))
            status = pr.get("Status", {}).get("status", {}).get("name", "")

            block = f"▸ *{title}*"
            if platform:
                block += f"\n   📱 {platform}"
            if fmt:
                block += f"\n   🎬 {fmt}"
            if themes:
                block += f"\n   🏷 {themes}"
            if status:
                block += f"\n   ◉ {status}"
            lines.append(block)

        await bot.send_message(chat_id, "\n\n".join(lines), parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Notion fetch error: {e}")
        await bot.send_message(chat_id, f"❌ Ошибка при получении данных из Notion:\n{e}")


# --- Main ---

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("new", cmd_new)],
        states={
            TITLE:        [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_title)],
            LIVE_DATE:    [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_date),
                CallbackQueryHandler(skip_date, pattern=r"^date\|SKIP$"),
            ],
            PLATFORM:     [CallbackQueryHandler(handle_platform, pattern=r"^platform\|")],
            FORMAT_STATE: [CallbackQueryHandler(handle_format,   pattern=r"^format\|")],
            THEMES:       [CallbackQueryHandler(handle_themes,   pattern=r"^themes\|")],
            VORONKA:      [CallbackQueryHandler(handle_voronka,  pattern=r"^voronka\|")],
            URL_STATE:    [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_url),
                CallbackQueryHandler(skip_url, pattern=r"^url\|SKIP$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(conv)

    async def on_startup(app):
        asyncio.create_task(daily_digest_loop(app.bot))

    app.post_init = on_startup

    logger.info("Bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
