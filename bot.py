import asyncio
import datetime as dt
import html
import json
import logging
import os
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv
from supabase import Client, create_client
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    InputMediaDocument,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
    User,
)
from telegram.constants import ChatType, ParseMode
from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

# --- Config ---------------------------------------------------------------

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN не задан в .env")

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    raise SystemExit("SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY не заданы в .env")

ADMIN_USERNAMES = {
    name.strip().lstrip("@").lower()
    for name in os.getenv("ADMIN_USERNAMES", "").split(",")
    if name.strip()
}
ADMIN_IDS = {
    int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()
}

DEFAULT_LANG = "ru"
SUPPORTED_LANGS = ("ru", "en")

WELCOME_MESSAGES = {
    "ru": (
        "Вас приветствует команда ELD, 🤝\n"
        "Мы доступны 24/7 🕐\n"
        "\n"
        "Не стесняйтесь обращаться, если у вас есть вопросы или нужна "
        "помощь с ELD.\n"
        "Мы всегда рады вам помочь! 📲\n"
        "\n"
        "<b>Во избежание проблем со связью, для запросов по ELD просьба "
        "использовать только эту группу</b>"
    ),
    "en": (
        "Welcome\n"
        "We are available 24/7 🕐\n"
        "\n"
        "Feel free to reach out if you have any questions or need "
        "assistance with ELD.\n"
        "We are always happy to help you! 📲\n"
        "\n"
        "<b>To avoid communication issues, please use only this group "
        "for any ELD-related requests.</b>"
    ),
}

APP_LINKS_MESSAGE = (
    "https://apps.apple.com/us/app/safe-lane-eld/id6749870586 - IOS\n"
    "\n"
    "https://play.google.com/store/apps/details?id=com.safelane.eld - ANDROID"
)

# ───────────────────────────────────────────────────────────────────────────
#  ДОП. ТЕКСТЫ В КОНЦЕ /welcome  ←  ВСТАВЛЯЙ СВОИ ТЕКСТЫ ЗДЕСЬ
# ───────────────────────────────────────────────────────────────────────────
# Эти тексты уходят ПОСЛЕДНИМИ в /welcome (после приветствия, ссылок и PDF),
# по одному сообщению на каждый элемент списка, сверху вниз, во все чаты
# одинаково (язык НЕ учитывается).
#
# • Это ОБЫЧНЫЙ текст — вставляй что угодно: эмодзи, значки, любые символы
#   (< > & и т.п.). Экранировать ничего НЕ нужно.
# • Слишком длинный текст бот сам порежет на части по границам строк
#   (лимит Telegram — 4096 символов на сообщение).
# • Пустой список = ничего лишнего не отправляется.
#
# Каждый текст бери в тройные кавычки """...""", элементы разделяй запятой.
# ВАЖНО: пиши строки БЕЗ отступа слева, иначе пробелы попадут в сообщение.
#
# Пример:
#     EXTRA_WELCOME_MESSAGES = [
# """🚛✅ ВНИМАНИЕ ВОДИТЕЛИ!
# Первый большой текст со значками 📲🔴
# может быть на много строк.""",
# """Второй текст 🕐
# тоже отправится отдельным сообщением.""",
#     ]
EXTRA_WELCOME_MESSAGES: list[str] = [
"""🚨 HIGH RISK ALERT — READ CAREFULLY 🚨

📸 LPR WARNING
LPR (License Plate Recognition) cameras scan your plate and track truck movement.
❗️ ELD logs must match real travel history → mismatch may cause HOS violation
⚠️LPR HIGH-RISK AREAS :
📍ARIZONA
• Kingman — (35.2123108, -113.9735641)
• St. David — (31.9883076, -110.1885575)
• Chandler — (33.2565727, -111.9493027)
• Phoenix — (33.3511581, -112.1908264)
• Littlefield — (36.9806633, -113.6454697)
• Doney Park  — (35.2123146, -111.5342026)
📍MISSOURI
• Mayview — (39.0037384, -93.8533554)
• Danville — (38.9111176, -91.7027893)
• Boonville — (38.9334450, -92.7803574)
📍NEBRASKA
• Hebron  — (40.0963050, -97.6140407)

📑 BOL & FUEL RECEIPT WARNING
⚠️ Some locations perform deep 8-day review of:
• BOLs
• Fuel receipts
• ELD logs

⚠️ DEEP CHECK AREAS
📍OREGON
• Ashland — POE (42.2222099, -122.7226715)
• Woodburn — Scale (45.1753289, -122.8547710)
• Umatilla — POE (45.9192215, -119.3278938)
• Salem — Hwy Patrol 
📍IOWA
• Colfax  (8-day BOL & fuel check)

✅ If routed through these areas → notify ELD team immediately
⚠️Avoid these locations when possible.

📌 DOCUMENT RULES

✅ Show ONLY fixed BOL
❗️Do NOT show original BOL
❗️Do NOT show fuel receipts
❗️Do NOT show Relay app
❗️Do NOT give your phone to inspectors or allow them to check your relay app.
❗️Do NOT provide any Love's receipts during inspections.

These instructions are very important. Please make sure you understand them and follow them during any inspection.
If you have any questions, let us know""",
]

WELCOME_FILES_DIR = Path(__file__).parent / "welcome_files"
WELCOME_PDFS = [
    "Safe Lane ELD - DOT Instruction Sheet ✅.pdf",
    "Safe Lane ELD - DOT Manual ✅.pdf",
    "Safe Lane ELD - FMCSA Certificate ✅.pdf",
]

_pdf_file_ids: dict[str, str] = {}

# Пауза между сообщениями приветствия (сек). Настраивается через .env,
# чтобы можно было замедлить отправку без правок кода.
WELCOME_INTER_MSG_DELAY = float(os.getenv("WELCOME_INTER_MSG_DELAY", "1.2"))

LANG_PROMPT = "🌐 Language / Язык:"
LANG_CONFIRM = {
    "ru": "✅ Язык: Русский",
    "en": "✅ Language: English",
}

ACCESS_DENIED_MSG = "❌ Access denied."

REFRESH_GROUP_ID = int(os.getenv("REFRESH_GROUP_ID", "-1003067626605"))
REFRESH_TRIGGERS = {
    "done, refresh please",
    "done, refresh please, update please",
}

CHATS_FILE = Path(__file__).parent / "chats.json"
USERS_FILE = Path(__file__).parent / "users.json"

LOG_FILE = Path(__file__).parent / "bot.log"

_log_handlers: list[logging.Handler] = [logging.StreamHandler()]
try:
    _log_handlers.append(
        RotatingFileHandler(
            LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
    )
except OSError:
    pass

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=_log_handlers,
)
logger = logging.getLogger(__name__)

# --- Supabase client ------------------------------------------------------

sb: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

# --- Buttons --------------------------------------------------------------

BTN_PTI = "🚛 PTI 15"
BTN_SHIFT = "⏰ Shift 14"
BTN_CYCLE = "📆 Cycle 70"
BTN_BREAK = "☕ Break 30"
BTN_DOT = "👮 DOT"


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(BTN_PTI), KeyboardButton(BTN_SHIFT)],
            [KeyboardButton(BTN_CYCLE), KeyboardButton(BTN_BREAK)],
            [KeyboardButton(BTN_DOT)],
        ],
        resize_keyboard=True,
        is_persistent=False,
    )


def language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🇷🇺 RU", callback_data="lang:ru"),
                InlineKeyboardButton("🇺🇸 EN", callback_data="lang:en"),
            ]
        ]
    )


# --- Storage: chats (Supabase + in-memory cache) -------------------------

_chats_cache: set[int] = set()
_chat_langs: dict[int, str] = {}


async def _load_chats() -> None:
    def _sync():
        resp = sb.table("chats").select("chat_id, lang").execute()
        return [
            (row["chat_id"], row.get("lang") or DEFAULT_LANG)
            for row in (resp.data or [])
        ]

    rows = await asyncio.to_thread(_sync)
    _chats_cache.clear()
    _chat_langs.clear()
    for cid, lng in rows:
        _chats_cache.add(cid)
        _chat_langs[cid] = lng
    logger.info("Загружено %s чатов из Supabase", len(_chats_cache))


async def set_chat_lang(chat_id: int, lang: str) -> None:
    def _sync():
        sb.table("chats").upsert(
            {"chat_id": chat_id, "lang": lang}
        ).execute()

    try:
        await asyncio.to_thread(_sync)
        _chat_langs[chat_id] = lang
        _chats_cache.add(chat_id)
    except Exception:
        logger.exception("set_chat_lang %s failed", chat_id)


async def get_chat_lang(chat_id: int) -> str | None:
    if chat_id in _chat_langs:
        return _chat_langs[chat_id]

    def _sync():
        resp = (
            sb.table("chats")
            .select("lang")
            .eq("chat_id", chat_id)
            .limit(1)
            .execute()
        )
        if resp.data:
            return resp.data[0].get("lang")
        return None

    try:
        lang = await asyncio.to_thread(_sync)
    except Exception:
        logger.exception("get_chat_lang %s failed", chat_id)
        return None

    if lang:
        _chat_langs[chat_id] = lang
    return lang


async def remember_chat(
    chat_id: int, title: str | None = None, chat_type: str | None = None
) -> None:
    if chat_id in _chats_cache and title is None and chat_type is None:
        return

    payload: dict = {"chat_id": chat_id}
    if title is not None:
        payload["title"] = title
    if chat_type is not None:
        payload["type"] = str(chat_type)

    def _sync():
        sb.table("chats").upsert(payload).execute()

    try:
        await asyncio.to_thread(_sync)
        if chat_id not in _chats_cache:
            _chats_cache.add(chat_id)
            logger.info("Добавлен чат %s. Всего: %s", chat_id, len(_chats_cache))
    except Exception:
        logger.exception("remember_chat %s failed", chat_id)


async def forget_chat(chat_id: int) -> None:
    if chat_id not in _chats_cache:
        return

    def _sync():
        sb.table("chats").delete().eq("chat_id", chat_id).execute()

    try:
        await asyncio.to_thread(_sync)
        _chats_cache.discard(chat_id)
        logger.info("Удалён чат %s. Всего: %s", chat_id, len(_chats_cache))
    except Exception:
        logger.exception("forget_chat %s failed", chat_id)


async def get_chats() -> set[int]:
    return set(_chats_cache)


# --- Storage: refresh events (analytics) ---------------------------------

async def record_refresh_event(
    chat_id: int,
    chat_title: str | None,
    user_id: int | None,
    username: str | None,
) -> None:
    payload = {
        "chat_id": chat_id,
        "chat_title": chat_title,
        "user_id": user_id,
        "username": username,
    }

    def _sync():
        sb.table("refresh_events").insert(payload).execute()

    try:
        await asyncio.to_thread(_sync)
    except Exception:
        logger.exception("record_refresh_event %s failed", chat_id)


async def fetch_refresh_stats(days: int = 7) -> list[dict]:
    def _sync():
        resp = (
            sb.table("refresh_daily_stats")
            .select("day, shift_morning, shift_evening, shift_night, total")
            .limit(days)
            .execute()
        )
        return resp.data or []

    try:
        return await asyncio.to_thread(_sync)
    except Exception:
        logger.exception("fetch_refresh_stats failed")
        return []


# --- Storage: users / language preference --------------------------------

_users_cache: dict[int, str] = {}


async def get_user_lang(user_id: int) -> str:
    if user_id in _users_cache:
        return _users_cache[user_id]

    def _sync():
        resp = (
            sb.table("users")
            .select("lang")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if resp.data:
            return resp.data[0].get("lang", DEFAULT_LANG)
        return DEFAULT_LANG

    try:
        lang = await asyncio.to_thread(_sync)
    except Exception:
        logger.exception("get_user_lang %s failed", user_id)
        return DEFAULT_LANG

    _users_cache[user_id] = lang
    return lang


async def set_user_lang(
    user_id: int, lang: str, username: str | None = None
) -> None:
    payload = {"user_id": user_id, "lang": lang}
    if username is not None:
        payload["username"] = username

    def _sync():
        sb.table("users").upsert(payload).execute()

    try:
        await asyncio.to_thread(_sync)
        _users_cache[user_id] = lang
    except Exception:
        logger.exception("set_user_lang %s failed", user_id)


# --- One-time migration from JSON files ----------------------------------

async def migrate_json_to_supabase() -> None:
    if CHATS_FILE.exists():
        try:
            data = json.loads(CHATS_FILE.read_text(encoding="utf-8"))
            for cid in data:
                cid_int = int(cid)
                if cid_int not in _chats_cache:
                    await remember_chat(cid_int)
            CHATS_FILE.rename(CHATS_FILE.with_suffix(".json.migrated"))
            logger.info("chats.json мигрирован в Supabase")
        except Exception:
            logger.exception("Migration chats.json failed")

    if USERS_FILE.exists():
        try:
            data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
            for uid_str, payload in data.items():
                if isinstance(payload, dict) and payload.get("lang"):
                    await set_user_lang(int(uid_str), payload["lang"])
            USERS_FILE.rename(USERS_FILE.with_suffix(".json.migrated"))
            logger.info("users.json мигрирован в Supabase")
        except Exception:
            logger.exception("Migration users.json failed")


# --- Access control ------------------------------------------------------

def is_admin(user: User | None) -> bool:
    if not user:
        return False
    if user.id in ADMIN_IDS:
        return True
    if user.username and user.username.lower() in ADMIN_USERNAMES:
        return True
    return False


def admin_only(handler):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not is_admin(user):
            if update.callback_query:
                try:
                    await update.callback_query.answer(
                        ACCESS_DENIED_MSG, show_alert=True
                    )
                except Exception:
                    pass
            elif update.message:
                try:
                    await update.message.reply_text(ACCESS_DENIED_MSG)
                except (Forbidden, BadRequest):
                    pass
            logger.info(
                "Access denied: user_id=%s username=@%s cmd=%s",
                user.id if user else "?",
                (user.username if user else "?"),
                handler.__name__,
            )
            return
        return await handler(update, context)

    wrapper.__name__ = handler.__name__
    return wrapper


# --- Bot membership handler (NOT user-initiated, no admin check) ---------

async def on_my_chat_member(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    cmu = update.my_chat_member
    if not cmu:
        return

    chat = cmu.chat
    new_status = cmu.new_chat_member.status
    old_status = cmu.old_chat_member.status

    logger.info(
        "Статус в чате %s (%s): %s -> %s",
        chat.id,
        chat.title or chat.username or "<dm>",
        old_status,
        new_status,
    )

    if new_status in {"left", "kicked"}:
        await forget_chat(chat.id)
        return

    if new_status in {"member", "administrator"}:
        await remember_chat(chat.id, title=chat.title, chat_type=chat.type)

    became_admin = (
        new_status == "administrator" and old_status != "administrator"
    )
    is_group = chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}

    if became_admin and is_group:
        try:
            await context.bot.send_message(
                chat_id=chat.id,
                text="✅",
                reply_markup=main_keyboard(),
            )
            await asyncio.sleep(WELCOME_INTER_MSG_DELAY)
            await context.bot.send_message(
                chat_id=chat.id,
                text=LANG_PROMPT,
                reply_markup=language_keyboard(),
            )
        except (Forbidden, BadRequest) as e:
            logger.warning("Не удалось отправить ✅ в %s: %s", chat.id, e)


# --- Commands ------------------------------------------------------------

@admin_only
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message:
        return
    chat = update.effective_chat
    if chat:
        await remember_chat(chat.id, title=chat.title, chat_type=chat.type)

    await message.reply_text("✅", reply_markup=main_keyboard())

    if not chat:
        return

    if chat.type == ChatType.PRIVATE:
        await message.reply_text(
            LANG_PROMPT, reply_markup=language_keyboard()
        )
        return

    if chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}:
        await asyncio.sleep(WELCOME_INTER_MSG_DELAY)
        await message.reply_text(
            LANG_PROMPT, reply_markup=language_keyboard()
        )


@admin_only
async def on_language_button(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if not query or not query.data or not query.data.startswith("lang:"):
        return

    lang = query.data.split(":", 1)[1]
    if lang not in SUPPORTED_LANGS:
        await query.answer("Unsupported language")
        return

    user = query.from_user
    chat = update.effective_chat

    if chat and chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}:
        await set_chat_lang(chat.id, lang)
    elif user:
        await set_user_lang(user.id, lang, username=user.username)
    else:
        await query.answer()
        return

    await query.answer(LANG_CONFIRM[lang], show_alert=False)
    try:
        await query.edit_message_text(LANG_CONFIRM[lang])
    except BadRequest:
        pass


def _safe_filename(name: str) -> str:
    cleaned = "".join(ch for ch in name if ord(ch) < 128).strip()
    return cleaned or "document.pdf"


async def _safe_send(coro_factory, *, retries: int = 3):
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            return await coro_factory()
        except RetryAfter as e:
            wait_for = e.retry_after + 1
            logger.warning(
                "RetryAfter %.1fs (attempt %s/%s)",
                wait_for, attempt + 1, retries,
            )
            await asyncio.sleep(wait_for)
            last_exc = e
        except (Forbidden, BadRequest) as e:
            raise
        except TelegramError as e:
            logger.warning(
                "TG error (attempt %s/%s): %s",
                attempt + 1, retries, e,
            )
            await asyncio.sleep(1.0)
            last_exc = e
    if last_exc:
        raise last_exc
    return None


TELEGRAM_MAX_MSG_LEN = 4096


def _split_message(text: str, limit: int = TELEGRAM_MAX_MSG_LEN) -> list[str]:
    """Режет длинный текст на части <= limit символов по границам строк,
    чтобы уложиться в лимит Telegram. Это обычный текст (без HTML-разметки),
    поэтому резать безопасно. Пустой/пробельный текст -> []."""
    text = text.strip("\n")
    if not text.strip():
        return []
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        # Одна строка длиннее лимита — режем её жёстко по символам.
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = line if not current else current + "\n" + line
        if len(candidate) > limit:
            if current:
                chunks.append(current)
            current = line
        else:
            current = candidate
    if current.strip():
        chunks.append(current)
    return chunks


async def _send_welcome_pdfs(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int
) -> None:
    media: list[InputMediaDocument] = []
    open_files: list = []
    fresh_indices: list[int] = []
    try:
        for name in WELCOME_PDFS:
            cached_id = _pdf_file_ids.get(name)
            if cached_id:
                media.append(InputMediaDocument(media=cached_id))
                continue
            path = WELCOME_FILES_DIR / name
            if not path.is_file():
                logger.warning("Welcome PDF not found: %s", path)
                continue
            fh = path.open("rb")
            open_files.append(fh)
            media.append(
                InputMediaDocument(
                    media=fh, filename=_safe_filename(name)
                )
            )
            fresh_indices.append(len(media) - 1)

        if not media:
            return

        sent = await _safe_send(
            lambda: context.bot.send_media_group(
                chat_id=chat_id, media=media
            )
        )

        if not sent:
            return

        for idx in fresh_indices:
            if idx >= len(sent):
                continue
            msg = sent[idx]
            original_name = WELCOME_PDFS[idx]
            if msg.document and not _pdf_file_ids.get(original_name):
                _pdf_file_ids[original_name] = msg.document.file_id
    finally:
        for fh in open_files:
            try:
                fh.close()
            except Exception:
                pass


@admin_only
async def cmd_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    chat = update.effective_chat
    if not message or not chat:
        return

    user = update.effective_user
    if chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}:
        lang = await get_chat_lang(chat.id) or DEFAULT_LANG
    elif user:
        lang = await get_user_lang(user.id)
    else:
        lang = DEFAULT_LANG
    text = WELCOME_MESSAGES.get(lang, WELCOME_MESSAGES[DEFAULT_LANG])

    # Best-effort: каждый шаг отправляется независимо, чтобы сбой/флуд-лимит
    # на одном сообщении не оборвал остальные. _safe_send уже ждёт RetryAfter.
    failed: list[str] = []

    try:
        await _safe_send(
            lambda: context.bot.send_message(
                chat_id=chat.id,
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        )
    except TelegramError as e:
        logger.warning("welcome text failed in %s: %s", chat.id, e)
        failed.append("текст")

    await asyncio.sleep(WELCOME_INTER_MSG_DELAY)

    try:
        await _safe_send(
            lambda: context.bot.send_message(
                chat_id=chat.id,
                text=APP_LINKS_MESSAGE,
                disable_web_page_preview=False,
            )
        )
    except TelegramError as e:
        logger.warning("welcome links failed in %s: %s", chat.id, e)
        failed.append("ссылки")

    await asyncio.sleep(WELCOME_INTER_MSG_DELAY)

    try:
        await _send_welcome_pdfs(context, chat.id)
    except TelegramError as e:
        logger.warning("welcome pdfs failed in %s: %s", chat.id, e)
        failed.append("PDF")

    # Доп. тексты (EXTRA_WELCOME_MESSAGES) — уходят самыми последними,
    # по одному сообщению на элемент списка; длинные режутся на части.
    for idx, extra in enumerate(EXTRA_WELCOME_MESSAGES, start=1):
        for part in _split_message(extra or ""):
            await asyncio.sleep(WELCOME_INTER_MSG_DELAY)
            try:
                await _safe_send(
                    lambda part=part: context.bot.send_message(
                        chat_id=chat.id,
                        text=part,
                        disable_web_page_preview=True,
                    )
                )
            except TelegramError as e:
                logger.warning(
                    "welcome extra #%s failed in %s: %s", idx, chat.id, e
                )
                failed.append(f"доп.текст #{idx}")

    if failed:
        try:
            await message.reply_text("⚠️ Не отправлено: " + ", ".join(failed))
        except TelegramError:
            pass


@admin_only
async def cmd_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(
            LANG_PROMPT, reply_markup=language_keyboard()
        )


@admin_only
async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if not user or not update.message:
        return
    await update.message.reply_text(
        f"user_id: <code>{user.id}</code>\n"
        f"username: @{user.username or '-'}\n"
        f"chat_id: <code>{chat.id if chat else '-'}</code>",
        parse_mode=ParseMode.HTML,
    )


@admin_only
async def cmd_chats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chats = await get_chats()
    if update.message:
        await update.message.reply_text(f"Бот сейчас в {len(chats)} чатах.")


@admin_only
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    days = 7
    if context.args:
        try:
            days = max(1, min(31, int(context.args[0])))
        except ValueError:
            pass

    rows = await fetch_refresh_stats(days=days)
    if not rows:
        await update.message.reply_text("📊 Пока нет данных по refresh-пингам.")
        return

    lines = [
        f"📊 <b>Refresh-пинги (Asia/Bishkek, последние {len(rows)} дн.)</b>",
        "",
        "🌅 07-15  🌆 15-23  🌙 23-07  Σ",
    ]
    grand = 0
    for r in rows:
        m = r.get("shift_morning") or 0
        e = r.get("shift_evening") or 0
        n = r.get("shift_night") or 0
        t = r.get("total") or 0
        grand += t
        lines.append(
            f"<code>{r['day']}</code>  {m:>3}     {e:>3}     {n:>3}    {t:>3}"
        )
    lines.append("")
    lines.append(f"<b>Всего за период: {grand}</b>")

    await update.message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML
    )


@admin_only
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await update.message.reply_text(
        "Available commands (admin only):\n"
        "/start — show keyboard + language\n"
        "/welcome — send welcome (text + app links + 3 PDFs)\n"
        "/language /lang — change language (RU / EN)\n"
        "/dot [day] — mark group title with DOT date\n"
        "/broadcast <text> — broadcast to all chats\n"
        "/stats [days] — refresh-ping statistics (default 7 days)\n"
        "/chats — count of chats bot is in\n"
        "/myid — your user_id\n"
        "/help — this list"
    )


# --- Broadcast (background + retry + lock) -------------------------------

_broadcast_lock = asyncio.Lock()


async def _do_broadcast(
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    sender_id: int,
    sender_name: str,
    status_msg,
) -> None:
    chats = await get_chats()
    sent = failed = removed = 0

    for chat_id in chats:
        delivered = False
        for attempt in range(3):
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
                delivered = True
                break
            except RetryAfter as e:
                logger.warning(
                    "Rate-limit на %s: ждём %s сек", chat_id, e.retry_after
                )
                await asyncio.sleep(e.retry_after + 1)
            except Forbidden as e:
                removed += 1
                await forget_chat(chat_id)
                logger.warning("Forbidden в %s: %s — удалили", chat_id, e)
                break
            except BadRequest as e:
                err = str(e).lower()
                if (
                    "chat not found" in err
                    or "kicked" in err
                    or "deactivated" in err
                ):
                    removed += 1
                    await forget_chat(chat_id)
                logger.warning("BadRequest в %s: %s", chat_id, e)
                break
            except TelegramError as e:
                logger.warning(
                    "TG ошибка в %s (попытка %s): %s", chat_id, attempt + 1, e
                )
                await asyncio.sleep(1)
            except Exception:
                logger.exception("Ошибка рассылки в %s", chat_id)
                break

        if delivered:
            sent += 1
        else:
            failed += 1

        await asyncio.sleep(0.05)

    logger.info(
        "Broadcast от @%s (id=%s) завершён: ok=%s fail=%s removed=%s total=%s",
        sender_name, sender_id, sent, failed, removed, len(chats),
    )
    try:
        await status_msg.edit_text(
            f"📣 Рассылка завершена (от @{sender_name}).\n"
            f"✅ Отправлено: {sent}\n"
            f"❌ Ошибок: {failed}\n"
            f"🗑 Удалено: {removed}\n"
            f"📊 Всего чатов: {len(chats)}"
        )
    except (Forbidden, BadRequest):
        pass


@admin_only
async def cmd_broadcast(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.message
    if not message:
        return
    user = update.effective_user

    text = " ".join(context.args) if context.args else None
    if not text and message.reply_to_message:
        text = (
            message.reply_to_message.text_html
            or message.reply_to_message.text
        )

    if not text:
        await message.reply_text(
            "Использование:\n"
            "  /broadcast <текст>\n"
            "  или ответь на сообщение командой /broadcast"
        )
        return

    if _broadcast_lock.locked():
        await message.reply_text("⏳ Уже идёт другая рассылка, подожди.")
        return

    chats = await get_chats()
    status_msg = await message.reply_text(
        f"📣 Отправляю в {len(chats)} чатов (в фоне)..."
    )

    sender_id = user.id if user else 0
    sender_name = (user.username if user and user.username else "unknown")

    async def run():
        async with _broadcast_lock:
            await _do_broadcast(
                context, text, sender_id, sender_name, status_msg
            )

    asyncio.create_task(run())


# --- /dot ----------------------------------------------------------------

DOT_PREFIX_RE = re.compile(r"^🔴[^🔴]*🔴\s*")
ENG_MONTHS = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]

NO_PERMISSION_MSG = (
    "❌ Bot doesn't have admin permission to change the group name.\n"
    "Please make the bot an admin with the «Change group info» permission."
)


def ordinal_suffix(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def parse_dot_date(args: list[str]) -> tuple[dt.date | None, str | None]:
    today = dt.date.today()
    if not args:
        return today, None
    try:
        day = int(args[0])
    except ValueError:
        return None, "Day must be a number. Example: /dot 22"
    if not 1 <= day <= 31:
        return None, "Day must be between 1 and 31."
    try:
        return dt.date(today.year, today.month, day), None
    except ValueError:
        return (
            None,
            f"This month doesn't have a {day}{ordinal_suffix(day)} day.",
        )


@admin_only
async def cmd_dot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    chat = update.effective_chat
    if not message:
        return
    if not chat or chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        await message.reply_text("/dot only works in groups.")
        return

    date, err = parse_dot_date(context.args or [])
    if err:
        await message.reply_text(err)
        return

    date_str = f"{date.day} {ENG_MONTHS[date.month - 1]}"
    new_prefix = f"🔴{date_str}🔴"

    try:
        chat_full = await context.bot.get_chat(chat.id)
        current_title = chat_full.title or ""
    except (Forbidden, BadRequest) as e:
        await message.reply_text(f"Couldn't fetch chat title: {e}")
        return

    base_title = DOT_PREFIX_RE.sub("", current_title)
    new_title = f"{new_prefix} {base_title}".strip()
    if len(new_title) > 128:
        new_title = new_title[:128]

    if new_title == current_title:
        return

    for attempt in range(3):
        try:
            await context.bot.set_chat_title(chat.id, new_title)
            return
        except Forbidden:
            await message.reply_text(NO_PERMISSION_MSG)
            return
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except BadRequest as e:
            err_text = str(e).lower()
            if (
                "not enough rights" in err_text
                or "chat_admin_required" in err_text
            ):
                await message.reply_text(NO_PERMISSION_MSG)
                return
            if attempt == 2:
                await message.reply_text(f"❌ Failed to rename: {e}")
                return
            await asyncio.sleep(1)


# --- Refresh trigger -----------------------------------------------------

async def on_refresh_trigger(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.message
    if not message or not message.text:
        return
    if message.text.strip().lower() not in REFRESH_TRIGGERS:
        return
    if not is_admin(update.effective_user):
        return
    chat = update.effective_chat
    if not chat or chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        return

    if chat.id == REFRESH_GROUP_ID:
        return

    title = chat.title or str(chat.id)
    user = update.effective_user
    try:
        await context.bot.send_message(chat_id=REFRESH_GROUP_ID, text=title)
        logger.info("Refresh ping: '%s' -> %s", title, REFRESH_GROUP_ID)
        await record_refresh_event(
            chat_id=chat.id,
            chat_title=title,
            user_id=user.id if user else None,
            username=user.username if user else None,
        )
    except (Forbidden, BadRequest) as e:
        logger.warning(
            "Refresh ping failed to %s: %s", REFRESH_GROUP_ID, e
        )


# --- Group migrate (group -> supergroup) ---------------------------------

async def on_group_migrated(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.message
    if not message or not message.migrate_to_chat_id:
        return
    old_id = message.chat_id
    new_id = message.migrate_to_chat_id
    logger.info("Migrate chat: %s -> %s", old_id, new_id)
    await forget_chat(old_id)
    await remember_chat(new_id, title=message.chat.title, chat_type="supergroup")


# --- Error handler -------------------------------------------------------

async def error_handler(
    update: object, context: ContextTypes.DEFAULT_TYPE
) -> None:
    logger.exception(
        "Exception while handling update: %s", context.error
    )

    if not ADMIN_IDS:
        return
    err_text = (
        f"⚠️ <b>Bot error</b>\n"
        f"<code>{html.escape(str(context.error)[:500])}</code>"
    )
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=err_text,
                parse_mode=ParseMode.HTML,
            )
        except (Forbidden, BadRequest):
            pass
        except Exception:
            pass


# --- Startup -------------------------------------------------------------

async def post_init(app: Application) -> None:
    await _load_chats()
    await migrate_json_to_supabase()
    logger.info(
        "Бот готов. Админы (usernames): %s | (ids): %s",
        ADMIN_USERNAMES or "—",
        ADMIN_IDS or "—",
    )


def main() -> None:
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_start))
    app.add_handler(CommandHandler("welcome", cmd_welcome))
    app.add_handler(CommandHandler("language", cmd_language))
    app.add_handler(CommandHandler("lang", cmd_language))
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CommandHandler("chats", cmd_chats))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("dot", cmd_dot))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(
        CallbackQueryHandler(on_language_button, pattern=r"^lang:")
    )
    app.add_handler(
        ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER)
    )
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS,
            on_refresh_trigger,
        )
    )
    app.add_handler(
        MessageHandler(filters.StatusUpdate.MIGRATE, on_group_migrated)
    )
    app.add_error_handler(error_handler)

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
