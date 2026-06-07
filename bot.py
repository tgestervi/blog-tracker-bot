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
ASSISTANT_CHAT_ID = int(os.environ.get("ASSISTANT_CHAT_ID", "750311841"))
TIMEZONE = os.environ.get("TIMEZONE", "Europe/Moscow")

bot = TeleBot(BOT_TOKEN)
notion = Client(auth=NOTION_TOKEN)


# Автоопределение названия поля-заголовка
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

PLATFORM_OPTIONS = ["YouTube", "Instagram", "Tik-tok", "VK", "Дзен"]
FORMAT_OPTIONS = ["Короткий ролик 9:16", "Длинный ролик", "Текстовый пост + фото", "Карусель", "Сторис"]
THEMES_OPTIONS = ["Экспертный", "Личный", "Продающий", "Развлекательный"]

# Статусы, которые попадают в утренний дайджест владелице
ACTIVE_STATUSES = ["Idea", "Research", "Copywriting", "Filming", "Editing"]


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
    kb.row(types.KeyboardButton("📅 Задачи на сегодня"))
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
    start_new_idea(message.chat.id, message.from_user.id)


@bot.message_handler(commands=['today'])
def cmd_today(message):
    send_digest_for(message.chat.id, message.from_user.id)


def start_new_idea(chat_id, uid):
    user_state[uid] = 'title'
    user_data[uid] = {}
    bot.send_message(chat_id, "📝 Название поста:")


@bot.message_handler(func=lambda m: True, content_types=['text'])
def handle_text(message):
    uid = message.from_user.id
    chat_id = message.chat.id
    state = user_state.get(uid)

    if message.text == "📝 Добавить идею":
        start_new_idea(chat_id, uid)
        return

    if message.text == "📅 Задачи на сегодня":
        send_digest_for(chat_id, uid)
        return

    if state == 'title':
        user_data[uid]['title'] = message.text.strip()
        user_state[uid] = 'date'
        bot.send_message(chat_id, "📅 Дата публикации (ДД.ММ.ГГГГ):", reply_markup=skip_kb("date"))

    elif state == 'date':
        try:
            d = datetime.datetime.strptime(message.text.strip(), "%d.%m.%Y").date()
            user_data[uid]['live_date'] = d.isoformat()
        except ValueError:
            bot.send_message(chat_id, "❌ Формат: ДД.ММ.ГГГГ. Попробуй ещё раз:", reply_markup=skip_kb("date"))
            return
        goto_platform(chat_id, uid)

    elif state == 'reference':
        user_data[uid]['reference'] = message.text.strip()
        goto_script(chat_id, uid)

    elif state == 'script':
        user_data[uid]['script'] = message.text.strip()
        save_to_notion(chat_id, uid)


def goto_platform(chat_id, uid):
    user_state[uid] = 'platform'
    user_data[uid].setdefault('platform', [])
    bot.send_message(chat_id, "📱 Платформа:", reply_markup=multiselect_kb(PLATFORM_OPTIONS, [], "platform"))


def goto_format(chat_id, uid):
    user_state[uid] = 'format'
    user_data[uid].setdefault('format', [])
    bot.send_message(chat_id, "🎬 Формат:", reply_markup=multiselect_kb(FORMAT_OPTIONS, [], "format"))


def goto_themes(chat_id, uid):
    user_state[uid] = 'themes'
    user_data[uid].setdefault('themes', [])
    bot.send_message(chat_id, "🏷 Тема:", reply_markup=multiselect_kb(THEMES_OPTIONS, [], "themes"))


def goto_reference(chat_id, uid):
    user_state[uid] = 'reference'
    bot.send_message(chat_id, "🔗 Референс (ссылка):", reply_markup=skip_kb("reference"))


def goto_script(chat_id, uid):
    user_state[uid] = 'script'
    bot.send_message(chat_id, "✍️ Сценарий (просто текстом — упадёт в тело карточки):", reply_markup=skip_kb("script"))


@bot.callback_query_handler(func=lambda c: '|' in c.data)
def handle_callback(call):
    uid = call.from_user.id
    chat_id = call.message.chat.id
    prefix, val = call.data.split('|', 1)

    if prefix == 'date' and val == 'SKIP':
        user_data[uid]['live_date'] = None
        bot.answer_callback_query(call.id)
        goto_platform(chat_id, uid)

    elif prefix == 'platform':
        if val in ('SKIP', 'DONE'):
            if val == 'SKIP':
                user_data[uid]['platform'] = []
            bot.answer_callback_query(call.id)
            goto_format(chat_id, uid)
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
            bot.answer_callback_query(call.id)
            goto_themes(chat_id, uid)
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
            bot.answer_callback_query(call.id)
            goto_reference(chat_id, uid)
        else:
            sel = user_data[uid].setdefault('themes', [])
            sel.remove(val) if val in sel else sel.append(val)
            bot.answer_callback_query(call.id)
            bot.edit_message_reply_markup(chat_id, call.message.message_id,
                                          reply_markup=multiselect_kb(THEMES_OPTIONS, sel, "themes"))

    elif prefix == 'reference' and val == 'SKIP':
        user_data[uid]['reference'] = None
        bot.answer_callback_query(call.id)
        goto_script(chat_id, uid)

    elif prefix == 'script' and val == 'SKIP':
        user_data[uid]['script'] = None
        bot.answer_callback_query(call.id)
        save_to_notion(chat_id, uid)


def chunk_text(text, size=1800):
    return [text[i:i + size] for i in range(0, len(text), size)]


def append_script_blocks(page_id, script_text):
    """Кладёт текст сценария в тело страницы параграфами (с разбивкой на чанки из-за лимита Notion)."""
    if not script_text:
        return
    children = [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": chunk}}]}
        }
        for chunk in chunk_text(script_text)
    ]
    try:
        notion.blocks.children.append(block_id=page_id, children=children)
    except Exception as e:
        logger.error(f"append_script_blocks error: {e}")


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
    if d.get('reference'):
        props["Референс"] = {"url": d['reference']}

    try:
        page = notion.pages.create(parent={"database_id": NOTION_DB_ID}, properties=props)
        append_script_blocks(page["id"], d.get('script'))
        known_statuses[page["id"]] = "Idea"

        lines = [f"✅ *{d['title']}* — добавлено в Notion (статус: Idea)"]
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


def status_or_filter(statuses):
    return {"or": [{"property": "Status", "select": {"equals": s}} for s in statuses]}


def send_owner_digest(chat_id=None):
    """Дайджест для владелицы: всё что запланировано на сегодня и ещё не в работе у монтажа/публикации."""
    if chat_id is None:
        chat_id = CHAT_ID
    today = datetime.date.today().isoformat()
    try:
        res = notion.databases.query(
            database_id=NOTION_DB_ID,
            filter={
                "and": [
                    {"property": "Live Date", "date": {"equals": today}},
                    status_or_filter(ACTIVE_STATUSES)
                ]
            }
        )
        pages = res.get("results", [])
        if not pages:
            bot.send_message(chat_id, f"📅 На сегодня ({today}) ничего нет.")
            return
        lines = [f"📅 *На сегодня — {today}:*\n"]
        for p in pages:
            pr = p["properties"]
            title_list = pr.get(TITLE_FIELD, {}).get("title", [])
            title = title_list[0]["text"]["content"] if title_list else "—"
            status = (pr.get("Status", {}).get("select") or {}).get("name", "—")
            platform = " · ".join(x["name"] for x in pr.get("Platform", {}).get("multi_select", []))
            fmt = " · ".join(x["name"] for x in pr.get("Формат", {}).get("multi_select", []))
            block = f"▸ *{title}* ({status})"
            if platform:
                block += f"\n   📱 {platform}"
            if fmt:
                block += f"\n   🎬 {fmt}"
            lines.append(block)
        bot.send_message(chat_id, "\n\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        logger.error(e)


def send_assistant_digest(chat_id=None):
    """Дайджест для ассистентки: всё что в статусе Scheduled и должно было выйти сегодня или раньше."""
    if chat_id is None:
        chat_id = ASSISTANT_CHAT_ID
    today = datetime.date.today().isoformat()
    try:
        res = notion.databases.query(
            database_id=NOTION_DB_ID,
            filter={
                "and": [
                    {"property": "Status", "select": {"equals": "Scheduled"}},
                    {"property": "Live Date", "date": {"on_or_before": today}}
                ]
            }
        )
        pages = res.get("results", [])
        if not pages:
            bot.send_message(chat_id, f"📅 На сегодня ({today}) задач на публикацию нет.")
            return
        lines = ["🔥 *Почему до сих пор не выложено??*\n"]
        for p in pages:
            pr = p["properties"]
            title_list = pr.get(TITLE_FIELD, {}).get("title", [])
            title = title_list[0]["text"]["content"] if title_list else "—"
            live_date = (pr.get("Live Date", {}).get("date") or {}).get("start", "—")
            lines.append(f"▸ *{title}* — план: {live_date}\n   {p.get('url', '')}")
        bot.send_message(chat_id, "\n\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        logger.error(e)


def send_digest_for(chat_id, uid):
    if uid == ASSISTANT_CHAT_ID:
        send_assistant_digest(chat_id)
    else:
        send_owner_digest(chat_id)


# --- Слежение за сменой статуса на Scheduled -> уведомление ассистентке ---
known_statuses = {}


def query_all_pages():
    pages = []
    cursor = None
    while True:
        kwargs = {"database_id": NOTION_DB_ID, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        res = notion.databases.query(**kwargs)
        pages.extend(res.get("results", []))
        if not res.get("has_more"):
            break
        cursor = res.get("next_cursor")
    return pages


def init_known_statuses():
    """При старте бота запоминаем текущие статусы, чтобы не разослать уведомления по старым задачам."""
    try:
        for p in query_all_pages():
            status = (p["properties"].get("Status", {}).get("select") or {}).get("name")
            known_statuses[p["id"]] = status
        logger.info(f"Status watch initialized: {len(known_statuses)} pages")
    except Exception as e:
        logger.error(f"init_known_statuses error: {e}")


def check_status_changes():
    for p in query_all_pages():
        page_id = p["id"]
        pr = p["properties"]
        status = (pr.get("Status", {}).get("select") or {}).get("name")
        prev = known_statuses.get(page_id)
        if status == "Scheduled" and prev != "Scheduled":
            notify_assistant_scheduled(p)
        known_statuses[page_id] = status


def notify_assistant_scheduled(page):
    pr = page["properties"]
    title_list = pr.get(TITLE_FIELD, {}).get("title", [])
    title = title_list[0]["text"]["content"] if title_list else "—"
    url = page.get("url", "")
    text = f'🔔 Материал по «{title}» готов к публикации.\n{url}'
    try:
        bot.send_message(ASSISTANT_CHAT_ID, text)
    except Exception as e:
        logger.error(f"notify_assistant_scheduled error: {e}")


def status_watch_thread():
    init_known_statuses()
    while True:
        time.sleep(300)  # проверка раз в 5 минут
        try:
            check_status_changes()
        except Exception as e:
            logger.error(f"status watch error: {e}")


def daily_digest_thread():
    tz = pytz.timezone(TIMEZONE)
    while True:
        now = datetime.datetime.now(tz)
        target = now.replace(hour=9, minute=0, second=0, microsecond=0)
        if now >= target:
            target += datetime.timedelta(days=1)
        time.sleep((target - now).total_seconds())
        try:
            send_owner_digest()
            send_assistant_digest()
        except Exception as e:
            logger.error(f"Digest error: {e}")


if __name__ == "__main__":
    logger.info("Bot started")
    threading.Thread(target=daily_digest_thread, daemon=True).start()
    threading.Thread(target=status_watch_thread, daemon=True).start()
    bot.infinity_polling()
