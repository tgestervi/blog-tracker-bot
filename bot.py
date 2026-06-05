import os
import logging
import datetime
import threading
import time
from telebot import TeleBot, types
from notion_client import Client
import pytz

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_DB_ID = os.environ["NOTION_DATABASE_ID"]
CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"])
TIMEZONE = os.environ.get("TIMEZONE", "Europe/Moscow")

bot = TeleBot(BOT_TOKEN)
notion = Client(auth=NOTION_TOKEN)

# Auto-detect title field name
def get_title_field():
    try:
        db = notion.databases.retrieve(NOTION_DB_ID)
        for name, prop in db["properties"].items():
            if prop["type"] == "title":
                logger.info(f"Title field found: '{name}'")
                return name
    except Exception as e:
        logger.error(f"Could not retrieve DB schema: {e}")
    return "Name"

TITLE_FIELD = get_title_field()

user_state = {}
user_data = {}

PLATFORM_OPTIONS = ["Telegram", "Instagram", "YouTube", "TikTok"]
FORMAT_OPTIONS = ["Текстовый пост + фото", "Видео", "Reels", "Карусель", "Сторис"]
THEMES_OPTIONS = ["Экспертный", "Личный", "Продающий", "Развлекательный"]
VORONKA_OPTIONS = ["Консультация B2B", "Телеграмм", "Призвание контент-креатор", "Консультация B2C", "Лид-магнит в бота"]


def multiselect_kb(options, selected, prefix):
    kb = types.InlineKeyboardMarkup()
    for opt in options:
        label = f"✅ {opt}" if opt in selected else opt
        kb.add(types.InlineKeyboardButton(label, callback_data=f"{prefix}|{opt}"))
    kb.row(
        types.InlineKeyboardButton("✓ Готово", callback_data=f"{prefix}|DONE"),
        types.InlineKeyboardButton("⏭ Пропустить", callback_data=f"{prefix}|SKIP")
    )
    return kb


def skip_kb(prefix):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("⏭ Пропустить", callback_data=f"{prefix}|SKIP"))
    return kb


def main_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(types.KeyboardButton("📝 Добавить идею"))
    kb.row(types.KeyboardButton("📅 На сегодня"))
    return kb


@bot.message_handler(commands=['start'])
def cmd_start(message):
    bot.send_message(message.chat.id,
        "👋 *Blog Tracker Bot*\n\nНажми кнопку ниже чтобы добавить идею в Notion.",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )


@bot.message_handler(commands=['cancel'])
def cmd_cancel(message):
    uid = message.from_user.id
    user_state.pop(uid, None)
    user_data.pop(uid, None)
    bot.send_message(message.chat.id, "❌ Отменено.", reply_markup=main_keyboard())


@bot.message_handler(commands=['new'])
def cmd_new(message):
    uid = message.from_user.id
    user_state[uid] = 'title'
    user_data[uid] = {}
    bot.send_message(message.chat.id, "📝 Название поста:")


@bot.message_handler(commands=['today'])
def cmd_today(message):
    send_digest(message.chat.id)


@bot.message_handler(func=lambda m: True, content_types=['text'])
def handle_text(message):
    uid = message.from_user.id
    state = user_state.get(uid)

    if message.text == "📝 Добавить идею":
        user_state[uid] = 'title'
        user_data[uid] = {}
        bot.send_message(message.chat.id, "📝 Название поста:")
        return

    if message.text == "📅 На сегодня":
        send_digest(message.chat.id)
        return

    if state == 'title':
        user_data[uid]['title'] = message.text.strip()
        user_state[uid] = 'date'
        bot.send_message(message.chat.id, "📅 Дата публикации (ДД.ММ.ГГГГ):", reply_markup=skip_kb("date"))

    elif state == 'date':
        try:
            d = datetime.datetime.strptime(message.text.strip(), "%d.%m.%Y").date()
            user_data[uid]['live_date'] = d.isoformat()
        except ValueError:
            bot.send_message(message.chat.id, "❌ Формат: ДД.ММ.ГГГГ. Попробуй ещё раз:", reply_markup=skip_kb("date"))
            return
        user_state[uid] = 'platform'
        user_data[uid].setdefault('platform', [])
        bot.send_message(message.chat.id, "📱 Платформа:",
                         reply_markup=multiselect_kb(PLATFORM_OPTIONS, [], "platform"))

    elif state == 'url':
        user_data[uid]['url'] = message.text.strip()
        save_to_notion(message.chat.id, uid)


@bot.callback_query_handler(func=lambda c: '|' in c.data)
def handle_callback(call):
    uid = call.from_user.id
    chat_id = call.message.chat.id
    prefix, val = call.data.split('|', 1)

    if prefix == 'date' and val == 'SKIP':
        user_data[uid]['live_date'] = None
        user_state[uid] = 'platform'
        user_data[uid].setdefault('platform', [])
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, "📱 Платформа:",
                         reply_markup=multiselect_kb(PLATFORM_OPTIONS, [], "platform"))

    elif prefix == 'platform':
        if val in ('SKIP', 'DONE'):
            if val == 'SKIP':
                user_data[uid]['platform'] = []
            user_state[uid] = 'format'
            user_data[uid].setdefault('format', [])
            bot.answer_callback_query(call.id)
            bot.send_message(chat_id, "🎬 Формат:",
                             reply_markup=multiselect_kb(FORMAT_OPTIONS, [], "format"))
        else:
            sel = user_data[uid].setdefault('platform', [])
            sel.remove(val) if val in sel else sel.append(val)
            bot.answer_callback_query(call.id)
            bot.edit_message_reply_markup(chat_id, call.message.message_id,
                                          reply_markup=multiselect_kb(PLATFORM_OPTIONS, sel, "platform"))

    elif prefix == 'format':
        if val in ('SKIP', 'DONE'):
            if val == 'SKIP':
                user_data[uid]['format'] = []
            user_state[uid] = 'themes'
            user_data[uid].setdefault('themes', [])
            bot.answer_callback_query(call.id)
            bot.send_message(chat_id, "🏷 Темы:",
                             reply_markup=multiselect_kb(THEMES_OPTIONS, [], "themes"))
        else:
            sel = user_data[uid].setdefault('format', [])
            sel.remove(val) if val in sel else sel.append(val)
            bot.answer_callback_query(call.id)
            bot.edit_message_reply_markup(chat_id, call.message.message_id,
                                          reply_markup=multiselect_kb(FORMAT_OPTIONS, sel, "format"))

    elif prefix == 'themes':
        if val in ('SKIP', 'DONE'):
            if val == 'SKIP':
                user_data[uid]['themes'] = []
            user_state[uid] = 'voronka'
            user_data[uid].setdefault('voronka', [])
            bot.answer_callback_query(call.id)
            bot.send_message(chat_id, "🔀 Воронка / сюжетная линия:",
                             reply_markup=multiselect_kb(VORONKA_OPTIONS, [], "voronka"))
        else:
            sel = user_data[uid].setdefault('themes', [])
            sel.remove(val) if val in sel else sel.append(val)
            bot.answer_callback_query(call.id)
            bot.edit_message_reply_markup(chat_id, call.message.message_id,
                                          reply_markup=multiselect_kb(THEMES_OPTIONS, sel, "themes"))

    elif prefix == 'voronka':
        if val in ('SKIP', 'DONE'):
            if val == 'SKIP':
                user_data[uid]['voronka'] = []
            user_state[uid] = 'url'
            bot.answer_callback_query(call.id)
            bot.send_message(chat_id, "🔗 URL:", reply_markup=skip_kb("url"))
        else:
            sel = user_data[uid].setdefault('voronka', [])
            sel.remove(val) if val in sel else sel.append(val)
            bot.answer_callback_query(call.id)
            bot.edit_message_reply_markup(chat_id, call.message.message_id,
                                          reply_markup=multiselect_kb(VORONKA_OPTIONS, sel, "voronka"))

    elif prefix == 'url' and val == 'SKIP':
        user_data[uid]['url'] = None
        bot.answer_callback_query(call.id)
        save_to_notion(chat_id, uid)


def save_to_notion(chat_id, uid):
    d = user_data.get(uid, {})
    props = {
        TITLE_FIELD: {"title": [{"text": {"content": d.get('title', '')}}]},
        "Status": {"select": {"name": "Idea"}},
    }
    if d.get('live_date'):
        props["Live Date"] = {"date": {"start": d['live_date']}}
    if d.get('platform'):
        props["Platform"] = {"multi_select": [{"name": p} for p in d['platform']]}
    if d.get('format'):
        props["Формат"] = {"multi_select": [{"name": f} for f in d['format']]}
    if d.get('themes'):
        props["Themes"] = {"multi_select": [{"name": t} for t in d['themes']]}
    if d.get('voronka'):
        props["Воронка/сюж линия"] = {"multi_select": [{"name": v} for v in d['voronka']]}
    if d.get('url'):
        props["URL"] = {"url": d['url']}

    try:
        notion.pages.create(parent={"database_id": NOTION_DB_ID}, properties=props)
        lines = [f"✅ *{d['title']}* — добавлено в Notion!"]
        if d.get('live_date'):
            lines.append(f"📅 {d['live_date']}")
        if d.get('platform'):
            lines.append(f"📱 {', '.join(d['platform'])}")
        bot.send_message(chat_id, "\n".join(lines), parse_mode="Markdown", reply_markup=main_keyboard())
    except Exception as e:
        logger.error(e)
        bot.send_message(chat_id, f"❌ Ошибка: {e}", reply_markup=main_keyboard())

    user_state.pop(uid, None)
    user_data.pop(uid, None)


def send_digest(chat_id=None):
    if chat_id is None:
        chat_id = CHAT_ID
    today = datetime.date.today().isoformat()
    try:
        res = notion.databases.query(
            database_id=NOTION_DB_ID,
            filter={"property": "Live Date", "date": {"equals": today}}
        )
        pages = res.get("results", [])
        if not pages:
            bot.send_message(chat_id, f"📅 На сегодня ({today}) ничего нет.")
            return
        lines = [f"📅 *На сегодня — {today}:*\n"]
        for p in pages:
            pr = p["properties"]
            title_list = pr.get("Title", {}).get("title", [])
            title = title_list[0]["text"]["content"] if title_list else "—"
            platform = " · ".join(x["name"] for x in pr.get("Platform", {}).get("multi_select", []))
            fmt = " · ".join(x["name"] for x in pr.get("Формат", {}).get("multi_select", []))
            block = f"▸ *{title}*"
            if platform:
                block += f"\n   📱 {platform}"
            if fmt:
                block += f"\n   🎬 {fmt}"
            lines.append(block)
        bot.send_message(chat_id, "\n\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        logger.error(e)


def daily_digest_thread():
    tz = pytz.timezone(TIMEZONE)
    while True:
        now = datetime.datetime.now(tz)
        target = now.replace(hour=9, minute=0, second=0, microsecond=0)
        if now >= target:
            target += datetime.timedelta(days=1)
        time.sleep((target - now).total_seconds())
        try:
            send_digest()
        except Exception as e:
            logger.error(f"Digest error: {e}")


if __name__ == "__main__":
    logger.info("Bot started")
    threading.Thread(target=daily_digest_thread, daemon=True).start()
    bot.infinity_polling()
