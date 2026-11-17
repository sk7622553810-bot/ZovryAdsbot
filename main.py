import os
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent
os.chdir(APP_ROOT)

import asyncio
import string
import re
import json
import html
import random
from datetime import datetime, timedelta
from telethon import TelegramClient, functions, types
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError,
    FloodWaitError,
    UpdateAppToLoginError,
    PhoneNumberInvalidError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    SessionExpiredError,
    PasswordHashInvalidError
)
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ChatType, ParseMode
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramAPIError,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.filters import Command
from aiogram.filters.chat_member_updated import (
    ChatMemberUpdatedFilter,
    JOIN_TRANSITION,
    LEAVE_TRANSITION,
)
from aiogram.types import ChatMemberUpdated
from aiogram.utils.text_decorations import html_decoration

try:
    import config
except ValueError as error:
    print(f"Invalid environment configuration: {error}", file=sys.stderr)
    raise SystemExit(1)

from database import EnhancedDatabaseManager
from force_subscription import (
    chat_member_is_joined,
    dashboard_access_allowed,
    has_seen_post_welcome,
    required_force_join_chat_kind,
)
from utils import (
    edit_message_or_reply,
    is_start_command,
    validate_phone_number,
    generate_progress_bar,
    format_duration,
)
from keyboards import (
    dashboard_keyboard,
    dashboard_caption_text,
    danger_inline_button,
    force_join_inline_keyboard,
    forced_subscription_message,
    hosted_accounts_keyboard,
    auto_join_keyboard,
    auto_join_text,
    human_behaviour_keyboard,
    joiner_prompt_keyboard,
    joiner_prompt_text,
    joiner_status_keyboard,
    joiner_summary_keyboard,
    joiner_summary_text,
    kb,
    otp_keyboard,
    primary_inline_button as InlineKeyboardButton,
    quick_delay_keyboard,
    welcome_keyboard,
    welcome_message,
)
from auto_join import (
    ALREADY_JOINED,
    FAILED,
    JOINED,
    PENDING,
    REQUEST_SENT,
    count_statuses,
    join_group_link,
    parse_joiner_items,
    process_folder_link,
    run_with_concurrency,
    safe_telegram_reason,
)
from profile_enforcement import (
    ProfileEnforcementError,
    enforce_account_profile,
)
import logging
from logging.handlers import RotatingFileHandler
from cryptography.fernet import Fernet

# Logging setup
os.makedirs('logs', exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler('logs/ZovryAds.log', maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

try:
    config.validate_runtime_configuration()
except RuntimeError as error:
    logger.error("Configuration error: %s", error)
    raise SystemExit(1)

print("Zovry Bot Free Version Started. 🚀")

GROUP_DELIVERY_INTERVAL_SECONDS = 1.0
HUMAN_BEHAVIOUR_MIN_DELAY_SECONDS = 1.0
HUMAN_BEHAVIOUR_MAX_DELAY_SECONDS = 2.5
MAX_CONCURRENT_ACCOUNT_JOIN_WORKERS = 2

# Initialize encryption key with persistence
ENCRYPTION_KEY = getattr(config, 'ENCRYPTION_KEY', None)
KEY_FILE = 'encryption.key'
if not ENCRYPTION_KEY:
    logger.warning("No ENCRYPTION_KEY in config. Loading or generating from file.")
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE, 'r') as f:
            ENCRYPTION_KEY = f.read().strip()
    else:
        ENCRYPTION_KEY = Fernet.generate_key().decode()
        with open(KEY_FILE, 'w') as f:
            f.write(ENCRYPTION_KEY)
        logger.info("Generated and saved new encryption key to encryption.key")
else:
    with open(KEY_FILE, 'w') as f:
        f.write(ENCRYPTION_KEY)
    logger.info("Using ENCRYPTION_KEY from config and saved to file.")

cipher_suite = Fernet(ENCRYPTION_KEY.encode())

# Initialize database
try:
    db = EnhancedDatabaseManager()
except Exception as e:
    logger.error(f"Failed to initialize database: {e}. Exiting.")
    print("Bot failed to start due to database error. Check logs/ZovryAds.log for details.")
    exit(1)

# Admin check
ADMIN_IDS = list(config.ADMIN_IDS)
ALLOWED_BD_IDS = ADMIN_IDS

def is_owner(uid):
    return uid in ALLOWED_BD_IDS


def account_limit_label(user):
    limit = user.get("accounts_limit", 5)
    if isinstance(limit, str) and limit.lower() == "unlimited":
        return "Unlimited"
    return str(limit)


def account_limit_value(user, uid):
    if is_owner(uid):
        return None

    limit = user.get("accounts_limit", 5)
    if isinstance(limit, str) and limit.lower() == "unlimited":
        return None
    try:
        return int(limit)
    except (TypeError, ValueError):
        logger.error(f"Invalid accounts_limit for user {uid}: {limit}. Defaulting to 5")
        return 5


def account_is_active(account):
    return bool(account.get("is_active", True))


def active_accounts_count(uid):
    return sum(
        1 for account in db.get_user_accounts(uid)
        if account_is_active(account)
    )


def account_success_message(phone_number):
    return (
        "Account Successfully added!✅\n\n"
        f"Phone: {phone_number}\n"
        "╰_╯Your account is ready for broadcasting!\n"
        "Note: Your profile bio and name are automatically set to the Zovry branding before each broadcast."
    )


def page_header(title):
    return f"━━━━━━━━━━━━━━━━━━\n<b>{title}</b>\n━━━━━━━━━━━━━━━━━━\n\n"


def format_interval(seconds):
    if seconds % 60 == 0:
        minutes = seconds // 60
        return f"{minutes} Minute" if minutes == 1 else f"{minutes} Minutes"
    return f"{seconds} Seconds"


def dashboard_caption(uid):
    user = db.get_user(uid) or {}
    accounts_count = db.get_user_accounts_count(uid)
    saved_messages = db.get_user_ad_messages(uid)
    delay = db.get_user_ad_delay(uid)
    running = db.get_broadcast_state(uid).get("running", False)
    return dashboard_caption_text(
        accounts_count,
        account_limit_label(user),
        len(saved_messages),
        1,
        running,
        format_interval(delay),
        str(user.get("plan", "Free")),
        config.ADMIN_USERNAME,
    )


async def edit_dashboard(message, uid):
    caption = dashboard_caption(uid)
    reply_markup = dashboard_keyboard(
        db.get_broadcast_state(uid).get("running", False),
        active_accounts_count(uid),
    )
    sender = getattr(message, "from_user", None)
    if sender is not None and not sender.is_bot:
        return await message.answer_photo(
            photo=config.START_IMAGE,
            caption=caption,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML,
        )
    await edit_message_or_reply(
        message,
        caption,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML,
    )


INPUT_PROMPT_STATES = {
    "telethon_wait_phone",
    "telethon_wait_otp",
    "telethon_wait_password",
    "waiting_broadcast_msg",
    "waiting_broadcast_delay",
    "waiting_group_link",
    "waiting_group_join_links",
    "waiting_folder_join_links",
}
input_prompt_refs = {}


def is_input_prompt_state(state):
    return state in INPUT_PROMPT_STATES or (
        state.startswith("waiting_group_join_links:")
        or state.startswith("waiting_folder_join_links:")
        or state.startswith("waiting_folder_batch:")
    )


async def delete_message_safely(bot, chat_id, message_id, context):
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except TelegramAPIError as error:
        logger.warning("Could not delete %s message %s: %s", context, message_id, error)


async def reply_with_input_prompt(message, uid, text, **kwargs):
    prompt = await message.answer(text, **kwargs)
    input_prompt_refs[uid] = (prompt.chat.id, prompt.message_id)
    return prompt


main_bot = Bot(
    token=config.BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
logger_bot = Bot(
    token=config.LOGGER_BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
main_router = Router(name="main")
logger_router = Router(name="logger")
main_dispatcher = Dispatcher()
logger_dispatcher = Dispatcher()
main_dispatcher.include_router(main_router)
logger_dispatcher.include_router(logger_router)


def register_main_message(*filters):
    def decorator(handler):
        async def adapted(message, bot: Bot):
            uid = message.from_user.id if message.from_user else None
            state_before = db.get_user_state(uid) if uid is not None else ""
            prompt_before = input_prompt_refs.get(uid) if uid is not None else None
            is_command = bool(message.text and message.text.lstrip().startswith("/"))
            is_start = is_start_command(message.text)
            should_delete_input = not is_start and (
                is_command or is_input_prompt_state(state_before)
            )
            try:
                return await handler(bot, message)
            finally:
                if should_delete_input:
                    try:
                        await message.delete()
                    except TelegramAPIError as error:
                        logger.warning(
                            "Could not delete user input %s: %s",
                            getattr(message, "message_id", "unknown"),
                            error,
                        )

                if uid is not None and is_input_prompt_state(state_before):
                    state_after = db.get_user_state(uid)
                    prompt_after = input_prompt_refs.get(uid)
                    if (
                        is_command
                        or state_after != state_before
                        or prompt_after != prompt_before
                    ):
                        if prompt_before:
                            await delete_message_safely(
                                bot,
                                prompt_before[0],
                                prompt_before[1],
                                "input prompt",
                            )
                        if is_command:
                            db.set_user_state(uid, "")
                            db.set_temp_data(uid, None)
                        if is_command or not is_input_prompt_state(state_after):
                            input_prompt_refs.pop(uid, None)

        adapted.__name__ = handler.__name__
        main_router.message(*filters)(adapted)
        return handler

    return decorator


def register_main_callback(*filters):
    def decorator(handler):
        async def adapted(callback, bot: Bot):
            uid = callback.from_user.id
            if callback.data != "menu_main":
                user = db.get_user(uid)
                if not user or not user.get("dashboard_unlocked", False):
                    await callback.answer(
                        "Please open Dashboard from the welcome message first.",
                        show_alert=True,
                    )
                    return
            try:
                return await handler(bot, callback)
            finally:
                state = db.get_user_state(uid)
                if is_input_prompt_state(state) and callback.message:
                    input_prompt_refs[uid] = (
                        callback.message.chat.id,
                        callback.message.message_id,
                    )
                else:
                    input_prompt_refs.pop(uid, None)

        adapted.__name__ = handler.__name__
        main_router.callback_query(*filters)(adapted)
        return handler

    return decorator


def register_main_chat_member(*filters):
    def decorator(handler):
        async def adapted(event: ChatMemberUpdated, bot: Bot):
            return await handler(bot, event)

        adapted.__name__ = handler.__name__
        main_router.chat_member(*filters)(adapted)
        return handler

    return decorator


def register_logger_message(*filters):
    def decorator(handler):
        async def adapted(message, bot: Bot):
            try:
                return await handler(bot, message)
            finally:
                if (
                    message.text
                    and message.text.lstrip().startswith("/")
                    and not is_start_command(message.text)
                ):
                    try:
                        await message.delete()
                    except TelegramAPIError as error:
                        logger.warning(
                            "Could not delete logger command %s: %s",
                            getattr(message, "message_id", "unknown"),
                            error,
                        )

        adapted.__name__ = handler.__name__
        logger_router.message(*filters)(adapted)
        return handler

    return decorator


# In-memory storage for broadcast tasks
user_tasks = {}
broadcast_operations = set()
dashboard_message_refs = {}
joiner_tasks = {}
folder_join_runs = {}
force_join_locks = {}


def force_join_lock(user_id):
    return force_join_locks.setdefault(user_id, asyncio.Lock())


async def delete_force_join_prompts(bot, user_id):
    for message_id in db.get_force_join_prompt_message_ids(user_id):
        try:
            await bot.delete_message(chat_id=user_id, message_id=message_id)
        except TelegramAPIError as error:
            logger.warning(
                f"Failed to delete force-join prompt {message_id} for {user_id}: {error}"
            )


async def send_welcome_message(bot, user_id):
    message = await bot.send_photo(
        chat_id=user_id,
        photo=config.START_IMAGE,
        caption=welcome_message(),
        reply_markup=welcome_keyboard(),
        parse_mode=ParseMode.HTML,
    )
    db.record_welcome_message(user_id, message.message_id)
    return message


def joiner_status_keyboard():
    return kb([
        [danger_inline_button("Stop joining", callback_data="cancel_joiner")],
        [danger_inline_button("Back", callback_data="auto_join")],
    ])


def joiner_status_icon(status):
    return {
        JOINED: "✅ Joined",
        REQUEST_SENT: "☑️ Request Sent",
        ALREADY_JOINED: "⚠️ Already Joined",
        FAILED: "❌ Failed",
        PENDING: "⏳ Pending / Unprocessed",
        "duplicate": "↪️ Duplicate — skipped",
        "processed": "✅ Processed",
    }.get(status, status)


def joiner_detail_line(index, item):
    label = html.escape(str(item.get("label", f"Item {index}")))
    status = joiner_status_icon(item.get("status", PENDING))
    line = f"{index}. <b>{label}</b> — {status}"
    if item.get("reason"):
        line += f"\n   <i>{html.escape(str(item['reason']))}</i>"
    return line


async def send_joiner_report(uid, heading, summary, details, keyboard=None):
    prefix = page_header(heading) + summary
    chunks = []
    current = prefix
    for index, detail in enumerate(details, 1):
        line = joiner_detail_line(index, detail)
        if len(current) + len(line) + 2 > 3500:
            chunks.append(current)
            current = page_header(f"{heading} — DETAILS")
        current += "\n\n" + line
    if current:
        chunks.append(current)
    for index, chunk in enumerate(chunks):
        await main_bot.send_message(
            uid,
            chunk,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard if index == len(chunks) - 1 else None,
        )


async def update_joiner_status(message, text, uid):
    try:
        await message.edit_text(
            page_header("⏳ AUTO JOIN") + text,
            parse_mode=ParseMode.HTML,
            reply_markup=joiner_status_keyboard(),
        )
    except TelegramBadRequest as error:
        if "message is not modified" not in error.message.lower():
            logger.error(f"Could not update auto-join status for user {uid}: {error}")
    except Exception as error:
        logger.error(f"Could not update auto-join status for user {uid}: {error}")


async def run_auto_join(uid, mode, accounts, items, status_message):
    total_links = len(accounts) * len(items)
    successful_count = 0
    failed_count = 0
    requests_count = 0
    total_completed = 0
    operation_number = 0
    status_lock = asyncio.Lock()

    async def update_status(text):
        async with status_lock:
            await update_joiner_status(status_message, text, uid)

    async def on_flood_wait(seconds):
        await update_status(
            f"Telegram requested a <b>{seconds}</b>-second wait for this account. "
            "It will resume after that wait; other account queues can continue."
        )

    async def process_account(account):
        nonlocal successful_count, failed_count, requests_count
        nonlocal total_completed, operation_number

        tg_client = None
        try:
            tg_client = await open_account_session(account, on_flood_wait)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error(
                "Could not connect auto-join account for user %s: %s",
                uid,
                safe_telegram_reason(error),
            )
            failed_count += len(items)
            total_completed += len(items)
            return

        try:
            for item in items:
                operation_number += 1
                if operation_number == 1 or operation_number % 5 == 0:
                    await update_status(
                        f"Joining link <b>{operation_number}/{total_links}</b> across your hosted accounts. "
                        "Telegram rate limits are observed; you can stop at any time."
                    )

                if not item["parsed"]:
                    failed_count += 1
                    total_completed += 1
                    continue

                try:
                    if mode == "group":
                        outcome = await join_group_link(
                            tg_client,
                            item["parsed"],
                            on_flood_wait,
                        )
                    else:
                        outcome = await process_folder_link(
                            tg_client,
                            item["parsed"],
                            operation_number,
                            on_flood_wait,
                        )
                        if outcome.get("cancelled"):
                            raise asyncio.CancelledError
                        group_statuses = {
                            group.get("status")
                            for group in outcome.get("groups", [])
                        }
                        if REQUEST_SENT in group_statuses:
                            outcome["status"] = REQUEST_SENT
                        elif outcome.get("status") != FAILED:
                            if JOINED in group_statuses:
                                outcome["status"] = JOINED
                            elif group_statuses and group_statuses <= {ALREADY_JOINED}:
                                outcome["status"] = ALREADY_JOINED
                            elif group_statuses and group_statuses <= {FAILED}:
                                outcome["status"] = FAILED
                            else:
                                outcome["status"] = JOINED
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    logger.error(
                        "Auto-join operation failed for user %s: %s",
                        uid,
                        safe_telegram_reason(error),
                    )
                    outcome = {
                        "status": FAILED,
                        "reason": safe_telegram_reason(error),
                    }

                outcome.setdefault("status", FAILED)
                total_completed += 1
                if outcome["status"] in (JOINED, ALREADY_JOINED):
                    successful_count += 1
                elif outcome["status"] == REQUEST_SENT:
                    requests_count += 1
                else:
                    failed_count += 1
        finally:
            if tg_client is not None:
                try:
                    await tg_client.disconnect()
                except Exception as error:
                    logger.error(
                        "Could not disconnect auto-join session for user %s: %s",
                        uid,
                        type(error).__name__,
                    )

    try:
        await run_with_concurrency(
            accounts,
            process_account,
            MAX_CONCURRENT_ACCOUNT_JOIN_WORKERS,
        )
    except asyncio.CancelledError:
        pass
    finally:
        db.set_user_state(uid, "")
        joiner_tasks.pop(uid, None)

    summary = joiner_summary_text(
        total_completed,
        total_links,
        successful_count,
        failed_count,
        requests_count,
    )
    try:
        await status_message.edit_text(
            summary,
            reply_markup=joiner_summary_keyboard(),
        )
    except TelegramAPIError as error:
        logger.error("Could not update auto-join summary for user %s: %s", uid, error)
        try:
            await main_bot.send_message(
                uid,
                summary,
                reply_markup=joiner_summary_keyboard(),
            )
        except TelegramAPIError as send_error:
            logger.error(
                "Could not deliver auto-join summary to user %s: %s",
                uid,
                send_error,
            )


async def start_joiner_from_input(uid, message, text, mode):
    items, maximum = parse_joiner_items(text, mode)
    if not items:
        await reply_with_input_prompt(
            message,
            uid,
            "Send at least one link or username.",
            reply_markup=joiner_prompt_keyboard(),
        )
        return

    accounts = [
        account for account in db.get_user_accounts(uid)
        if account.get("is_active", True)
    ]
    if not accounts:
        db.set_user_state(uid, "")
        await message.answer(
            "No active hosted accounts are available. Add or activate an account first.",
            reply_markup=kb([[danger_inline_button("Back", callback_data="auto_join")]]),
        )
        return

    fetched_count = len(items)
    status_message = await message.answer(
        f"Fetched {fetched_count}/{maximum} links. ✅\n\n"
        "Please wait patiently for your adbots to join them, you'll be notified when joining is completed. ⏳",
        reply_markup=joiner_status_keyboard(),
    )
    db.set_user_state(uid, f"processing_{mode}_join")
    input_prompt_refs.pop(uid, None)
    task = asyncio.create_task(run_auto_join(uid, mode, accounts, items, status_message))
    joiner_tasks[uid] = task


async def replace_with_banner(message, caption, reply_markup):
    await edit_message_or_reply(
        message,
        caption,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML,
    )
    return message


def get_active_account(user_id, account_id):
    return next(
        (
            account for account in db.get_user_accounts(user_id)
            if str(account.get("_id")) == account_id and account.get("is_active", True)
        ),
        None,
    )


async def open_account_session(account, on_flood_wait):
    session_string = cipher_suite.decrypt(account["session_string"].encode()).decode()
    while True:
        tg_client = TelegramClient(
            StringSession(session_string),
            config.API_ID,
            config.API_HASH,
        )
        try:
            await tg_client.connect()
            if not await tg_client.is_user_authorized():
                await tg_client.disconnect()
                raise RuntimeError("The selected Telegram account session is no longer authorized.")
            return tg_client
        except FloodWaitError as error:
            await tg_client.disconnect()
            await on_flood_wait(error.seconds)
            await asyncio.sleep(error.seconds)
        except asyncio.CancelledError:
            if tg_client.is_connected():
                await tg_client.disconnect()
            raise
        except Exception:
            if tg_client.is_connected():
                await tg_client.disconnect()
            raise


async def wait_for_joiner_flood_wait(message, uid, seconds):
    await update_joiner_status(
        message,
        f"Telegram rate limit received. Waiting exactly <b>{seconds}</b> seconds before continuing. "
        "You can stop the task at any time.",
        uid,
    )


def split_nonempty_lines(text):
    return [line.strip() for line in text.splitlines() if line.strip()]


def group_join_summary(total, valid, results, duplicates):
    counts = count_statuses([item for item in results if item.get("status") != "duplicate"])
    return (
        f"<b>Total links submitted:</b> {total}\n"
        f"<b>Valid links:</b> {valid}\n"
        f"<b>✅ Successfully joined:</b> {counts[JOINED]}\n"
        f"<b>☑️ Join requests successfully submitted:</b> {counts[REQUEST_SENT]}\n"
        f"<b>⚠️ Already joined:</b> {counts[ALREADY_JOINED]}\n"
        f"<b>❌ Failed:</b> {counts[FAILED]}\n"
        f"<b>⏳ Pending / Unprocessed:</b> {counts[PENDING]}\n"
        f"<b>Duplicates skipped:</b> {duplicates}\n"
        f"<b>Successful actions (joined + request sent):</b> {counts[JOINED] + counts[REQUEST_SENT]}"
    )


def folder_join_summary(run):
    return (
        f"<b>Total folders submitted:</b> {run['submitted']}\n"
        f"<b>Folders successfully processed:</b> {run['processed']}\n"
        f"<b>Folders failed:</b> {run['failed']}\n"
        f"<b>Folders pending / unprocessed:</b> {run['pending']}\n"
        f"<b>Total groups found:</b> {run['groups_found']}\n"
        f"<b>✅ Successfully joined:</b> {run['groups_joined']}\n"
        f"<b>☑️ Join requests successfully submitted:</b> {run['groups_requested']}\n"
        f"<b>⚠️ Already joined:</b> {run['groups_already']}\n"
        f"<b>❌ Failed groups:</b> {run['groups_failed']}\n"
        f"<b>⏳ Pending / Unprocessed groups:</b> {run['groups_pending']}\n"
        f"<b>Successful actions (joined + request sent):</b> "
        f"{run['groups_joined'] + run['groups_requested']}"
    )


async def finish_folder_join(uid, stopped=False):
    run = folder_join_runs.pop(uid, None)
    db.set_user_state(uid, "")
    if not run:
        return
    title = "⏹ FOLDER JOIN STOPPED" if stopped else "📊 FOLDER JOIN FINAL REPORT"
    details = []
    for folder in run["details"]:
        details.append(folder)
        details.extend(folder.get("groups", []))
    await send_joiner_report(
        uid,
        title,
        folder_join_summary(run),
        details,
        kb([[InlineKeyboardButton("← Auto Join", callback_data="auto_join")]]),
    )


async def run_group_join(uid, account, status_message, total, valid_entries, results, duplicates):
    tg_client = None
    current_index = None
    stopped = False
    try:
        if valid_entries:
            tg_client = await open_account_session(
                account,
                lambda seconds: wait_for_joiner_flood_wait(status_message, uid, seconds),
            )
            for position, entry in enumerate(valid_entries):
                current_index = entry["result_index"]
                parsed = entry["parsed"]
                await update_joiner_status(
                    status_message,
                    f"Processing link <b>{position + 1}/{len(valid_entries)}</b>: "
                    f"<b>{html.escape(parsed['label'])}</b>\n"
                    "Requests are sent sequentially using the selected account.",
                    uid,
                )
                outcome = await join_group_link(
                    tg_client,
                    parsed,
                    lambda seconds: wait_for_joiner_flood_wait(status_message, uid, seconds),
                )
                results[current_index].update(outcome)
                current_index = None
    except asyncio.CancelledError:
        stopped = True
        if current_index is not None:
            results[current_index].update(
                status=PENDING,
                reason="Task stopped before Telegram confirmed this action.",
            )
        for entry in valid_entries:
            index = entry["result_index"]
            if results[index].get("status") == PENDING:
                results[index]["reason"] = "Not processed because the task was stopped."
    except Exception as error:
        reason = safe_telegram_reason(error)
        logger.error(f"Auto-join session setup failed for user {uid}: {type(error).__name__}")
        for entry in valid_entries:
            results[entry["result_index"]].update(status=FAILED, reason=reason)
    finally:
        if tg_client is not None:
            try:
                await tg_client.disconnect()
            except Exception as error:
                logger.error(f"Could not disconnect auto-join session for user {uid}: {type(error).__name__}")
        db.set_user_state(uid, "")
        joiner_tasks.pop(uid, None)

    report_heading = "⏹ GROUP JOIN STOPPED" if stopped else "📊 GROUP JOIN FINAL REPORT"
    try:
        status_text = (
            "Task stopped. Completed outcomes are preserved; all unconfirmed items remain pending."
            if stopped else "Processing complete. Preparing the detailed report."
        )
        await update_joiner_status(status_message, status_text, uid)
        await send_joiner_report(
            uid,
            report_heading,
            group_join_summary(total, len(valid_entries), results, duplicates),
            results,
            kb([[InlineKeyboardButton("← Auto Join", callback_data="auto_join")]]),
        )
    except Exception as error:
        logger.error(f"Could not send group-join report for user {uid}: {type(error).__name__}")


async def process_folder_batch(uid, status_message, folder_items):
    run = folder_join_runs[uid]
    first_folder_number = run["submitted"] + 1
    run["submitted"] += len(folder_items)
    valid_items = [item for item in folder_items if item.get("slug")]
    account = get_active_account(uid, run["account_id"]) if valid_items else None
    if valid_items and not account:
        for item in folder_items:
            result = {
                "status": FAILED,
                "label": item["label"],
                "reason": (
                    "Invalid folder link format."
                    if not item.get("slug")
                    else "The selected account is no longer available or active."
                ),
                "groups_found": 0,
                "groups": [],
            }
            run["failed"] += 1
            run["details"].append(result)
        await show_folder_batch_result(uid, status_message, stopped=False, batch_results=run["details"][-len(folder_items):])
        return

    tg_client = None
    stopped = False
    current_item_index = None
    batch_results = []
    try:
        if valid_items:
            tg_client = await open_account_session(
                account,
                lambda seconds: wait_for_joiner_flood_wait(status_message, uid, seconds),
            )
        for index, item in enumerate(folder_items):
            current_item_index = index
            if not item.get("slug"):
                result = {
                    "status": FAILED,
                    "label": item["label"],
                    "reason": "Invalid folder link format.",
                    "groups_found": 0,
                    "groups": [],
                }
                run["failed"] += 1
                run["details"].append(result)
                batch_results.append(result)
                current_item_index = None
                continue
            await update_joiner_status(
                status_message,
                f"Processing folder <b>{index + 1}/{len(folder_items)}</b>: "
                f"<b>{html.escape(item['label'])}</b>\n"
                "Folders and their groups are processed sequentially. This batch is limited to two folders.",
                uid,
            )
            result = await process_folder_link(
                tg_client,
                item["slug"],
                first_folder_number + index,
                lambda seconds: wait_for_joiner_flood_wait(status_message, uid, seconds),
            )
            result["label"] = result.pop("title", item["label"])
            run["groups_found"] += result["groups_found"]
            run["details"].append(result)
            batch_results.append(result)
            if result["status"] == "processed":
                run["processed"] += 1
            elif result["status"] == PENDING:
                run["pending"] += 1
            else:
                run["failed"] += 1

            group_counts = count_statuses(result["groups"])
            run["groups_joined"] += group_counts[JOINED]
            run["groups_requested"] += group_counts[REQUEST_SENT]
            run["groups_already"] += group_counts[ALREADY_JOINED]
            run["groups_failed"] += group_counts[FAILED]
            run["groups_pending"] += group_counts[PENDING]
            if result.get("cancelled"):
                stopped = True
                current_item_index = index + 1
                break
            current_item_index = None
    except asyncio.CancelledError:
        stopped = True
    except Exception as error:
        reason = safe_telegram_reason(error)
        logger.error(f"Folder-join session setup failed for user {uid}: {type(error).__name__}")
        remaining_start = 0 if current_item_index is None else current_item_index
        for item in folder_items[remaining_start:]:
            failed_result = {
                "status": FAILED,
                "label": item["label"],
                "reason": "Invalid folder link format." if not item.get("slug") else reason,
                "groups_found": 0,
                "groups": [],
            }
            run["failed"] += 1
            run["details"].append(failed_result)
            batch_results.append(failed_result)
    finally:
        if tg_client is not None:
            try:
                await tg_client.disconnect()
            except Exception as error:
                logger.error(f"Could not disconnect folder-join session for user {uid}: {type(error).__name__}")

    if stopped:
        first_unprocessed = current_item_index
        if first_unprocessed is None:
            first_unprocessed = 0
        for item in folder_items[first_unprocessed:]:
            run["pending"] += 1
            pending_result = {
                "status": PENDING,
                "label": item["label"],
                "reason": "Not processed because the task was stopped.",
                "groups_found": 0,
                "groups": [],
            }
            run["details"].append(pending_result)
            batch_results.append(pending_result)
        db.set_user_state(uid, "")
        joiner_tasks.pop(uid, None)
        await show_folder_batch_result(uid, status_message, stopped=True, batch_results=batch_results)
        await finish_folder_join(uid, stopped=True)
        return

    await show_folder_batch_result(uid, status_message, stopped=False, batch_results=batch_results)


async def show_folder_batch_result(uid, status_message, stopped, batch_results):
    try:
        await send_folder_batch_details(uid, batch_results)
    except Exception as error:
        logger.error(f"Could not send folder batch details for user {uid}: {type(error).__name__}")
    if stopped:
        db.set_user_state(uid, "")
        joiner_tasks.pop(uid, None)
        await update_joiner_status(
            status_message,
            "Task stopped. The final report includes completed and unprocessed items.",
            uid,
        )
        return

    batch_buttons = kb([
        [InlineKeyboardButton("Next batch (up to 2 folders)", callback_data="next_folder_batch")],
        [InlineKeyboardButton("Finish and show final report", callback_data="finish_folder_join")],
        [InlineKeyboardButton("⏹ Stop / Cancel", callback_data="cancel_joiner")],
    ])
    db.set_user_state(uid, f"waiting_folder_batch:{folder_join_runs[uid]['account_id']}")
    await status_message.edit_text(
        page_header("📁 FOLDER BATCH COMPLETE") +
        "This batch has been processed. Send up to two more folder links, or finish to receive the final report.",
        parse_mode=ParseMode.HTML,
        reply_markup=batch_buttons,
    )
    input_prompt_refs[uid] = (status_message.chat.id, status_message.message_id)
    joiner_tasks.pop(uid, None)


async def send_folder_batch_details(uid, batch_results):
    details = []
    for folder in batch_results:
        details.append(folder)
        details.extend(folder.get("groups", []))
    if details:
        await send_joiner_report(
            uid,
            "📁 FOLDER BATCH RESULTS",
            "Per-folder and per-group outcomes for this completed batch:",
            details,
        )


async def refresh_active_dashboard(client, uid):
    message_ref = dashboard_message_refs.pop(uid, None)
    if not message_ref:
        return
    chat_id, message_id, is_media_message = message_ref
    try:
        text = dashboard_caption(uid)
        markup = dashboard_keyboard(
            db.get_broadcast_state(uid).get("running", False),
            active_accounts_count(uid),
        )
        if is_media_message:
            await client.edit_message_caption(
                chat_id=chat_id,
                message_id=message_id,
                caption=text,
                reply_markup=markup,
                parse_mode=ParseMode.HTML,
            )
        else:
            await client.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=markup,
                parse_mode=ParseMode.HTML,
            )
    except Exception as e:
        logger.error(f"Failed to refresh dashboard for user {uid}: {e}")

async def send_dm_log(user_id, log_message):
    if not db.get_logger_status(user_id):
        logger.info(f"User {user_id} has not started logger bot. Skipping DM log.")
        return
    try:
        await logger_bot.send_message(user_id, log_message, parse_mode=ParseMode.HTML)
        logger.info(f"DM log sent to {user_id}: {log_message[:50]}...")
    except TelegramForbiddenError:
        logger.error(f"DM log failed for {user_id}: logger bot cannot message this user.")
        db.log_logger_failure(user_id, "Logger bot cannot message user; user must start or unblock logger bot")
        try:
            await main_bot.send_message(
                user_id,
                "<b>⚠️ Logger bot not started!</b>\n\n"
                f"Please start @{config.LOGGER_BOT_USERNAME} to receive broadcast logs. 🌟",
                parse_mode=ParseMode.HTML,
                reply_markup=kb([[InlineKeyboardButton("Start Logger Bot 📩", url=f"https://t.me/{config.LOGGER_BOT_USERNAME.lstrip('@')}")]])
            )
        except Exception as e:
            logger.error(f"Failed to notify user {user_id} to start logger bot: {e}")
    except Exception as e:
        logger.error(f"DM log failed for {user_id}: {e} - Message: {log_message[:50]}...")
        db.log_logger_failure(user_id, str(e))

@register_logger_message(Command("start"))
async def logger_start(client, m):
    uid = m.from_user.id
    username = m.from_user.username or "Unknown"
    first_name = m.from_user.first_name or "User"
    
    db.create_user(uid, username, first_name)
    if is_owner(uid):
        db.db.users.update_one({"user_id": uid}, {"$set": {"accounts_limit": "unlimited"}})
    db.set_logger_status(uid, is_active=True)
    await m.answer(
        '<tg-emoji emoji-id="5391072026867802122">🔥</tg-emoji> '
        "Welcome to Zovry Logger Bot!\n\n"
        "Logs for your ad broadcasts will be sent here.\n"
        "Start the main bot (@Zovrybot) to begin broadcasting! "
        '<tg-emoji emoji-id="5462950031143216831">🌟</tg-emoji>',
        parse_mode=ParseMode.HTML
    )
    logger.info(f"Logger bot started by user {uid}")

async def is_joined(client, uid, chat_id):
    max_retries = 3
    retry_delay = 2
    for attempt in range(max_retries):
        try:
            logger.info(f"Checking membership for user {uid} in chat_id {chat_id} (Attempt {attempt + 1})")
            member = await client.get_chat_member(chat_id, uid)
            is_member = (
                member.status
                in {
                    ChatMemberStatus.MEMBER,
                    ChatMemberStatus.ADMINISTRATOR,
                    ChatMemberStatus.CREATOR,
                }
                or bool(getattr(member, "is_member", False))
            )
            logger.info(
                f"User {uid} {'is' if is_member else 'is not'} a member of chat_id {chat_id}"
            )
            return is_member
        except TelegramBadRequest as error:
            if "user not found" in error.message.lower():
                logger.info(f"User {uid} is not a member of chat_id {chat_id}")
                return False
            logger.error(
                f"Attempt {attempt + 1}: Failed to check join status for {uid} "
                f"in chat_id {chat_id}: {error}"
            )
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
                retry_delay *= 2
                continue
            logger.error(f"All retries failed for user {uid} in chat_id {chat_id}: {error}")
            return False
        except Exception as e:
            logger.error(f"Attempt {attempt + 1}: Failed to check join status for {uid} in chat_id {chat_id}: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
                retry_delay *= 2
                continue
            logger.error(f"All retries failed for user {uid} in chat_id {chat_id}: {e}")
            return False
    return False

async def is_joined_all(client, uid):
    channel_joined = await is_joined(client, uid, config.MUST_JOIN_CHANNEL_ID)
    await asyncio.sleep(0.5)
    group_joined = await is_joined(client, uid, config.MUSTJOIN_GROUP_ID)
    logger.info(f"User {uid} - Channel ({config.MUST_JOIN_CHANNEL_ID}) joined: {channel_joined}, Group ({config.MUSTJOIN_GROUP_ID}) joined: {group_joined}")
    if not channel_joined:
        logger.info(f"User {uid} has not joined channel {config.MUST_JOIN_CHANNEL_ID}")
    if not group_joined:
        logger.info(f"User {uid} has not joined group {config.MUSTJOIN_GROUP_ID}")
    return channel_joined and group_joined

async def validate_session(tg_client):
    try:
        await tg_client.connect()
        return await tg_client.is_user_authorized()
    except Exception as e:
        logger.error(f"Session validation failed: {e}")
        return False


async def wait_for_group_delivery(last_attempt_at, loop, human_behaviour_active=False):
    if last_attempt_at is not None:
        minimum_interval = (
            random.uniform(
                HUMAN_BEHAVIOUR_MIN_DELAY_SECONDS,
                HUMAN_BEHAVIOUR_MAX_DELAY_SECONDS,
            )
            if human_behaviour_active
            else GROUP_DELIVERY_INTERVAL_SECONDS
        )
        remaining = minimum_interval - (loop.time() - last_attempt_at)
        if remaining > 0:
            await asyncio.sleep(remaining)
    return loop.time()


async def stop_broadcast_task(client, uid):
    if uid in broadcast_operations:
        return False

    broadcast_operations.add(uid)
    try:
        if not db.get_broadcast_state(uid).get("running", False):
            logger.info(f"No broadcast running for user {uid}")
            return False

        task = user_tasks.get(uid)
        if task:
            try:
                task.cancel()
                await task
                logger.info(f"Cancelled broadcast task for {uid}")
            except asyncio.CancelledError:
                logger.info(f"Broadcast task for {uid} was cancelled successfully")
            except Exception as e:
                logger.error(f"Failed to cancel broadcast task for {uid}: {e}")
            finally:
                user_tasks.pop(uid, None)
        else:
            db.set_broadcast_state(uid, running=False)
            await refresh_active_dashboard(client, uid)
        return True
    finally:
        broadcast_operations.discard(uid)

async def run_broadcast(client, uid):
    clients = {}
    connected_clients = []
    try:
        cycle_count = 1
        saved_messages = db.get_user_ad_messages(uid)
        ad_message = saved_messages[0] if saved_messages else None
        msg = ad_message.get("message") if ad_message else None
        ad_parse_mode = (
            "html"
            if ad_message and ad_message.get("message_format") == "html"
            else None
        )
        if not msg:
            db.set_broadcast_state(uid, running=False)
            await refresh_active_dashboard(client, uid)
            return
        delay = db.get_user_ad_delay(uid)
        accounts = db.get_user_accounts(uid)
        target_groups = db.get_target_groups(uid)
        group_ids = {g['group_id'] for g in target_groups}
        group_cache = {}

        skip_group_ids = [config.MUSTJOIN_GROUP_ID]

        for acc in accounts:
            tg_client = None
            try:
                tg_client = TelegramClient(
                    StringSession(cipher_suite.decrypt(acc['session_string'].encode()).decode()),
                    config.API_ID,
                    config.API_HASH
                )
                connected_clients.append(tg_client)
                if not await validate_session(tg_client):
                    await tg_client.disconnect()
                    db.deactivate_account(acc['_id'])
                    logger.warning(f"Deactivated invalid session for {acc['phone_number']}")
                    continue
                
                await enforce_account_profile(tg_client, acc['phone_number'])

                cached_groups = []
                async for dialog in tg_client.iter_dialogs(limit=None):
                    if dialog.is_group and (not group_ids or dialog.id in group_ids):
                        if dialog.id in skip_group_ids:
                            logger.info(f"Skipping protected group {dialog.name} ({dialog.id}) for account {acc['phone_number']}")
                            continue
                        cached_groups.append((dialog.id, dialog.name))
                group_cache[acc['_id']] = cached_groups
                clients[acc['_id']] = tg_client
            except ProfileEnforcementError:
                raise
            except Exception as e:
                if tg_client is not None:
                    try:
                        await tg_client.disconnect()
                    except Exception as disconnect_error:
                        logger.error(f"Failed to disconnect account {acc['phone_number']} after setup error: {disconnect_error}")
                logger.error(f"Failed to start client for {acc['phone_number']}: {e}")
                db.increment_broadcast_stats(uid, False)
                await send_dm_log(
                    uid,
                    f"<b>╰_╯Failed to start account {acc['phone_number']}:</b> "
                    f"{html.escape(str(e))}❌",
                )
        
        if not clients:
            db.set_broadcast_state(uid, running=False)
            await refresh_active_dashboard(client, uid)
            return

        db.set_broadcast_state(uid, running=True)

        active_accounts = [acc for acc in accounts if acc['_id'] in clients]
        account_phones = ", ".join(acc['phone_number'] for acc in active_accounts)
        total_groups = sum(len(group_cache[acc['_id']]) for acc in active_accounts)

        try:
            while True:
                human_behaviour_active = db.get_human_behaviour(uid)
                processed_groups = 0
                successful_count = 0
                failed_count = 0
                flood_wait_count = 0
                auto_replied_count = 0
                pending_sent = 0
                pending_failed = 0
                last_stats_flush = 0
                last_summary_update = 0
                summary_message = None
                summary_disabled = False
                last_delivery_attempt = None
                loop = asyncio.get_running_loop()

                def flush_broadcast_stats():
                    nonlocal pending_sent, pending_failed, last_stats_flush
                    if pending_sent or pending_failed:
                        db.increment_broadcast_stats_batch(uid, pending_sent, pending_failed)
                        pending_sent = 0
                        pending_failed = 0
                    last_stats_flush = processed_groups

                async def update_cycle_summary(status, force=False):
                    nonlocal summary_message, summary_disabled, last_summary_update
                    if summary_disabled:
                        return
                    if status == "RUNNING" and not force and processed_groups - last_summary_update < 10:
                        return

                    if status == "RUNNING":
                        status_icon = '<tg-emoji emoji-id="6097937804573021893">🔄</tg-emoji>'
                    elif status == "COMPLETED":
                        status_icon = '<tg-emoji emoji-id="5465327879657102830">🏁</tg-emoji>'
                    else:
                        status_icon = "⏹️"
                    summary_text = (
                        '<tg-emoji emoji-id="6097979946792130160">📊</tg-emoji> '
                        f"<b>Log of: {account_phones}</b>\n\n"
                        f"<b>Cycle number: {cycle_count}</b>\n"
                        f"<b>Status:</b> {status} {status_icon}\n\n"
                        '<tg-emoji emoji-id="5397782960512444700">🎯</tg-emoji> '
                        f"<b>Target groups:</b> <b>{processed_groups}/{total_groups}</b>\n\n"
                        '<tg-emoji emoji-id="5287237090793270898">✅</tg-emoji> '
                        f"<b>Successful:</b> <b>{successful_count}</b>\n"
                        '<tg-emoji emoji-id="6100304499056844996">❌</tg-emoji> '
                        f"<b>Failed:</b> <b>{failed_count}</b>\n"
                        '<tg-emoji emoji-id="5296482716567495148">⏳</tg-emoji> '
                        f"<b>Flood wait:</b> <b>{flood_wait_count}</b>\n"
                        f"💬 <b>Auto replied:</b> <b>{auto_replied_count}</b>\n\n"
                        "Logs of this cycle will be updated after the cycle is completed."
                    )

                    while True:
                        try:
                            if summary_message is None:
                                summary_message = await logger_bot.send_message(
                                    uid, summary_text, parse_mode=ParseMode.HTML
                                )
                            else:
                                await summary_message.edit_text(
                                    summary_text, parse_mode=ParseMode.HTML
                                )
                            break
                        except TelegramBadRequest as error:
                            if "message is not modified" in error.message.lower():
                                break
                            logger.error(
                                f"Failed to update broadcast cycle summary for user {uid}: {error}"
                            )
                            db.log_logger_failure(uid, str(error))
                            summary_disabled = True
                            return
                        except TelegramRetryAfter as error:
                            await asyncio.sleep(error.retry_after)
                        except Exception as e:
                            logger.error(f"Failed to update broadcast cycle summary for user {uid}: {e}")
                            db.log_logger_failure(uid, str(e))
                            summary_disabled = True
                            return
                    last_summary_update = processed_groups

                try:
                    await update_cycle_summary("RUNNING", force=True)
                    for acc in active_accounts:
                        tg_client = clients[acc['_id']]
                        for gid, group_name in group_cache[acc['_id']]:
                            processed_groups += 1
                            was_flood_wait = False
                            try:
                                while True:
                                    try:
                                        last_delivery_attempt = await wait_for_group_delivery(
                                            last_delivery_attempt,
                                            loop,
                                            human_behaviour_active,
                                        )
                                        await tg_client.send_message(
                                            gid,
                                            msg,
                                            parse_mode=ad_parse_mode,
                                        )
                                        successful_count += 1
                                        pending_sent += 1
                                        break
                                    except FloodWaitError as e:
                                        if not was_flood_wait:
                                            flood_wait_count += 1
                                            was_flood_wait = True
                                            await update_cycle_summary("RUNNING", force=True)
                                        logger.warning(f"Flood wait in group {gid}: Wait {e.seconds} seconds")
                                        await asyncio.sleep(e.seconds)
                            except Exception as e:
                                logger.error(f"Failed to send message to group {gid}: {e}")
                                failed_count += 1
                                pending_failed += 1

                            if processed_groups - last_stats_flush >= 10:
                                flush_broadcast_stats()
                            if processed_groups - last_summary_update >= 10:
                                await update_cycle_summary("RUNNING", force=True)

                    flush_broadcast_stats()
                    db.increment_broadcast_cycle(uid)
                    await update_cycle_summary("COMPLETED", force=True)
                except asyncio.CancelledError:
                    await update_cycle_summary("STOPPED", force=True)
                    flush_broadcast_stats()
                    raise
                except Exception:
                    await update_cycle_summary("STOPPED", force=True)
                    flush_broadcast_stats()
                    raise

                cycle_count += 1
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            logger.info(f"Broadcast task cancelled for {uid}")
            raise
        finally:
            for tg_client in connected_clients:
                try:
                    await tg_client.disconnect()
                except Exception as e:
                    logger.error(f"Failed to disconnect client: {e}")
    except asyncio.CancelledError:
        logger.info(f"Broadcast task cancelled for {uid}")
    except Exception as e:
        logger.error(f"Broadcast task failed for {uid}: {e}")
        db.increment_broadcast_stats(uid, False)
        db.set_broadcast_state(uid, running=False)
        if uid in user_tasks:
            del user_tasks[uid]
        if isinstance(e, ProfileEnforcementError):
            failure_message = (
                "<b>Broadcast aborted: mandatory profile update failed.</b>\n"
                f"{html.escape(str(e))}"
            )
        else:
            failure_message = f"<b>❌ Broadcast task failed:</b> {html.escape(str(e))}"
        await send_dm_log(uid, failure_message)
        await refresh_active_dashboard(client, uid)
        for admin_id in ALLOWED_BD_IDS:
            try:
                await client.send_message(
                    admin_id,
                    f"Broadcast task failed for user {uid}: {html.escape(str(e))}",
                    parse_mode=ParseMode.HTML,
                )
                break
            except Exception as admin_e:
                logger.error(f"Failed to notify admin {admin_id}: {admin_e}")
    finally:
        for tg_client in clients.values():
            try:
                if tg_client.is_connected():
                    await tg_client.disconnect()
            except Exception as disconnect_error:
                logger.error(f"Failed to close a broadcast account client for {uid}: {disconnect_error}")
        db.set_broadcast_state(uid, running=False)
        user_tasks.pop(uid, None)
        await refresh_active_dashboard(client, uid)

@register_main_callback(F.data.regexp("^otp_"))
async def otp_callback(client, cb):
    uid = cb.from_user.id
    state = db.get_user_state(uid)
    if state != "telethon_wait_otp":
        await cb.answer("This verification session is no longer active. Start again from the dashboard.", show_alert=True)
        return

    temp_encrypted = db.get_temp_data(uid)
    if not temp_encrypted:
        await cb.answer("Verification session expired. Start account setup again.", show_alert=True)
        db.set_user_state(uid, "")
        return

    try:
        temp_json = cipher_suite.decrypt(temp_encrypted.encode()).decode()
        temp_dict = json.loads(temp_json)
        phone = temp_dict["phone"]
        session_str = temp_dict["session_str"]
        phone_code_hash = temp_dict["phone_code_hash"]
        otp = temp_dict.get("otp", "")
    except (json.JSONDecodeError, Fernet.InvalidToken) as e:
        logger.error(f"Invalid temp data for user {uid}: {e}")
        await cb.answer("Verification data is invalid. Start account setup again.", show_alert=True)
        db.set_user_state(uid, "")
        db.set_temp_data(uid, None)
        return

    try:
        StringSession(session_str)
    except Exception as e:
        logger.error(f"Invalid session string for user {uid}: {e}")
        await cb.answer("This account session is invalid. Start account setup again.", show_alert=True)
        db.set_user_state(uid, "")
        db.set_temp_data(uid, None)
        return

    action = cb.data.replace("otp_", "")
    if action.isdigit():
        if len(otp) < 5:
            otp += action
    elif action == "back":
        otp = otp[:-1] if otp else ""
    elif action == "cancel":
        db.set_user_state(uid, "")
        db.set_temp_data(uid, None)
        await edit_message_or_reply(
            cb.message,
            page_header("✖ VERIFICATION CANCELLED") + "No account was added.",
            parse_mode=ParseMode.HTML,
            reply_markup=kb([
                [danger_inline_button("← Dashboard", callback_data="menu_main")]
            ]),
        )
        return

    temp_dict["otp"] = otp
    temp_json = json.dumps(temp_dict)
    temp_encrypted = cipher_suite.encrypt(temp_json.encode()).decode()
    db.set_temp_data(uid, temp_encrypted)

    masked = " ".join("*" for _ in otp) if otp else "_____"
    base_caption = (
        page_header("🔐 ACCOUNT VERIFICATION") +
        f"Phone: <code>{phone}</code>\n"
        f"OTP sent successfully ✅\n\n"
        "Enter the code using the keypad below.\n"
        f"<b>Current:</b> <code>{masked}</code>\n"
        f"<b>Code format:</b> <code>12345</code>\n"
        f"<b>Expires in:</b> {config.OTP_EXPIRY // 60} minutes"
    )

    await edit_message_or_reply(
        cb.message,
        base_caption,
        parse_mode=ParseMode.HTML,
        reply_markup=otp_keyboard(),
    )

    if len(otp) == 5:
        await edit_message_or_reply(
            cb.message,
            base_caption + "\n\n<b>⏳ VERIFYING CODE</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=None,
        )
        max_retries = 3
        retry_delay = 2

        for attempt in range(max_retries):
            tg = TelegramClient(StringSession(session_str), config.API_ID, config.API_HASH)
            try:
                await tg.connect()
                await tg.sign_in(phone, code=otp, phone_code_hash=phone_code_hash)

                session_encrypted = cipher_suite.encrypt(session_str.encode()).decode()
                db.add_user_account(uid, phone, session_encrypted)

                await edit_message_or_reply(
                    cb.message,
                    account_success_message(phone),
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb([
                        [danger_inline_button("Back", callback_data="menu_main")]
                    ]),
                )
                await send_dm_log(
                    uid,
                    f'Account added successfully: {phone} '
                    '<tg-emoji emoji-id="5195100606250889609">✅</tg-emoji>',
                )
                db.set_user_state(uid, "")
                db.set_temp_data(uid, None)
                break
            except SessionPasswordNeededError:
                temp_dict_2fa = {
                    "phone": phone,
                    "session_str": session_str
                }
                temp_json_2fa = json.dumps(temp_dict_2fa)
                temp_encrypted_2fa = cipher_suite.encrypt(temp_json_2fa.encode()).decode()
                db.set_user_state(uid, "telethon_wait_password")
                db.set_temp_data(uid, temp_encrypted_2fa)
                await edit_message_or_reply(
                    cb.message,
                    page_header("🔒 TWO-STEP VERIFICATION") +
                    f"Phone: <code>{phone}</code>\n"
                    "This account requires its Telegram cloud password.\n"
                    "Send the password in this chat to continue.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb([
                        [danger_inline_button("Cancel", callback_data="menu_main")]
                    ]),
                )
                break
            except PhoneCodeInvalidError:
                if attempt < max_retries - 1:
                    logger.warning(f"Invalid OTP attempt {attempt + 1} for {uid}, retrying...")
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
                    continue
                await edit_message_or_reply(
                    cb.message,
                    base_caption + "\n\n<b>❌ INVALID CODE</b>\nEnter the correct code and try again.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=otp_keyboard(),
                )
                temp_dict["otp"] = ""
                temp_json = json.dumps(temp_dict)
                temp_encrypted = cipher_suite.encrypt(temp_json.encode()).decode()
                db.set_temp_data(uid, temp_encrypted)
            except PhoneCodeExpiredError:
                await edit_message_or_reply(
                    cb.message,
                    base_caption + "\n\n<b>❌ CODE EXPIRED</b>\nRestart account verification to request a new code.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb([
                        [danger_inline_button("← Dashboard", callback_data="menu_main")]
                    ]),
                )
                db.set_user_state(uid, "")
                db.set_temp_data(uid, None)
                break
            except FloodWaitError as e:
                logger.warning(f"Flood wait during OTP verification for {uid}: Wait {e.seconds} seconds")
                await asyncio.sleep(e.seconds)
                if attempt < max_retries - 1:
                    continue
                await edit_message_or_reply(
                    cb.message,
                    base_caption + f"\n\n<b>⏳ TELEGRAM RATE LIMIT</b>\nWait {e.seconds} seconds before retrying.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb([
                        [danger_inline_button("← Dashboard", callback_data="menu_main")]
                    ]),
                )
                db.set_user_state(uid, "")
                db.set_temp_data(uid, None)
                break
            except Exception as e:
                logger.error(f"Error signing in for {uid} (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
                    continue
                await edit_message_or_reply(
                    cb.message,
                    base_caption + f"\n\n<blockquote><b>❌ Login failed:</b>{str(e)}</blockquote>\n\n"
                    f"<b>Contact support:</b> @{config.ADMIN_USERNAME}",
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb([
                        [danger_inline_button("← Dashboard", callback_data="menu_main")]
                    ]),
                )
                await send_dm_log(uid, f"<b>❌ Account login failed:</b> {str(e)}")
                db.set_user_state(uid, "")
                db.set_temp_data(uid, None)
                break
            finally:
                await tg.disconnect()

@register_main_callback(F.data.regexp("back_to_start"))
async def back_to_start(client, cb):
    await edit_dashboard(cb.message, cb.from_user.id)

@register_main_callback(F.data.regexp("menu_main"))
async def menu_main(client, cb):
    try:
        uid = cb.from_user.id
        state = db.get_user_state(uid)
        if state in {
            "telethon_wait_phone",
            "telethon_wait_otp",
            "telethon_wait_password",
        }:
            db.set_temp_data(uid, None)
        if not state.startswith("processing_") and uid not in joiner_tasks:
            db.set_user_state(uid, "")
        db.update_user_last_interaction(uid)
        user = db.get_user(uid)
        
        if not user:
            await cb.answer("Please restart with /start ", show_alert=True)
            return
        if config.ENABLE_FORCE_JOIN and not await is_joined_all(client, uid):
            await cb.answer(
                "Join the required channel and group before opening the dashboard.",
                show_alert=True,
            )
            return
        welcome_message_id = user.get("welcome_message_id")
        message_id = cb.message.message_id if cb.message else None
        if not dashboard_access_allowed(user, message_id):
            await cb.answer(
                "Open the Dashboard button on your welcome message first.",
                show_alert=True,
            )
            return
        if not user.get("dashboard_unlocked", False):
            if not db.unlock_dashboard_from_welcome(uid, welcome_message_id):
                await cb.answer("Please run /start to receive the welcome screen.", show_alert=True)
                return
        await edit_dashboard(cb.message, uid)
        logger.info(f"Menu main accessed by user {uid}, callback_data: {cb.data}")
    except Exception as e:
        logger.error(f"Error in menu_main for user {uid}: {e}")
        await cb.answer("Error loading dashboard. Try /start.", show_alert=True)

@register_main_callback(F.data.regexp("host_account"))
async def host_account(client, cb):
    uid = cb.from_user.id
    user = db.get_user(uid)
    
    if not user:
        await cb.answer("Please restart with /start", show_alert=True)
        return
    
    accounts_count = db.get_user_accounts_count(uid)
    limit = account_limit_value(user, uid)

    if limit is not None and accounts_count >= limit:
        await cb.answer(f"Account limit reached ({accounts_count}/{limit}) Use @ZovryChat to increase limit.", show_alert=True)
        return
    
    try:
        db.set_user_state(uid, "telethon_wait_phone")
        db.set_temp_data(uid, None)
    except Exception as e:
        logger.error(f"Failed to set user state for {uid}: {e}")
        await cb.answer("Error initiating account hosting. Try again.", show_alert=True)
        return
    
    await edit_message_or_reply(
        cb.message,
        "HOST NEW ACCOUNT\n\n"
        "<b>Secure Account Hosting</b>\n\n"
        "Enter your phone number with country code:\n\n"
        "<blockquote>Example: +1234567890</blockquote>\n\n"
        "Your data is encrypted and secure",
        reply_markup=kb([
            [danger_inline_button("← Dashboard", callback_data="menu_main")]
        ]),
        parse_mode=ParseMode.HTML,
    )

@register_main_callback(F.data.regexp("view_accounts"))
async def view_accounts(client, cb):
    uid = cb.from_user.id
    accounts = db.get_user_accounts(uid)
    user = db.get_user(uid) or {}
    accounts_count = db.get_user_accounts_count(uid)
    limit = account_limit_value(user, uid)
    can_add_account = limit is None or accounts_count < limit
    if not accounts:
        await edit_message_or_reply(
            cb.message,
            "╰_╯HOSTED ACCOUNTS\n"
            "╰_╯Choose an action:",
            reply_markup=hosted_accounts_keyboard(accounts, can_add_account),
            parse_mode=ParseMode.HTML,
        )
        return
    
    caption = "╰_╯HOSTED ACCOUNTS\n"
    for i, acc in enumerate(accounts, 1):
        active = account_is_active(acc)
        status = "(Active)" if active else "(Inactive)"
        status_icon = "✅" if active else "⚪"
        phone_number = str(acc["phone_number"])
        caption += f"{i}. {html.escape(phone_number)} {status} {status_icon}\n"

    caption += "╰_╯Choose an action:"
    
    await edit_message_or_reply(
        cb.message,
        caption,
        reply_markup=hosted_accounts_keyboard(accounts, can_add_account),
        parse_mode=ParseMode.HTML,
    )

@register_main_callback(F.data.regexp("delete_accounts"))
async def delete_accounts(client, cb):
    uid = cb.from_user.id
    accounts = db.get_user_accounts(uid)
    if not accounts:
        user = db.get_user(uid) or {}
        limit = account_limit_value(user, uid)
        can_add_account = (
            limit is None
            or db.get_user_accounts_count(uid) < limit
        )
        buttons = []
        if can_add_account:
            buttons.append([InlineKeyboardButton("➕ Add Account", callback_data="host_account")])
        buttons.append([InlineKeyboardButton("← Dashboard", callback_data="menu_main")])
        await edit_message_or_reply(
            cb.message,
            page_header("🗑 ACCOUNT REMOVAL")
            + "There are no hosted accounts to remove.",
            reply_markup=kb(buttons),
            parse_mode=ParseMode.HTML,
        )
        return
    
    caption = page_header("🗑 ACCOUNT REMOVAL") + "Choose an account to review or remove:\n\n"
    buttons = []
    for i, acc in enumerate(accounts, 1):
        status = "Active 🟢" if account_is_active(acc) else "Inactive ⚪"
        caption += f"<b>{i}.</b> <code>{acc['phone_number']}</code> · {status}\n"
        buttons.append([
            InlineKeyboardButton(f"Account {i} · {status}", callback_data=f"view_acc_{acc['_id']}"),
            danger_inline_button("Delete", callback_data=f"delete_acc_{acc['_id']}")
        ])
    
    buttons.append([InlineKeyboardButton("← Dashboard", callback_data="menu_main")])
    
    await edit_message_or_reply(
        cb.message,
        caption,
        reply_markup=kb(buttons),
        parse_mode=ParseMode.HTML,
    )

@register_main_callback(F.data.regexp("delete_acc_"))
async def delete_account(client, cb):
    uid = cb.from_user.id
    acc_id = cb.data.replace("delete_acc_", "")
    accounts = db.get_user_accounts(uid)
    account = next((acc for acc in accounts if str(acc["_id"]) == acc_id), None)
    if not account:
        await cb.answer("That account is no longer available.", show_alert=True)
        return

    phone_number = str(account["phone_number"])
    await edit_message_or_reply(
        cb.message,
        "⚠️ "
        f"<b>Are you sure you want to delete the account "
        f"{html.escape(phone_number)}?</b>",
        reply_markup=kb([
            [danger_inline_button(
                "Yes, Delete Account",
                callback_data=f"confirm_account_deletion_{acc_id}",
            )],
            [danger_inline_button(
                "Back",
                callback_data=f"cancel_account_deletion_{acc_id}",
            )],
        ]),
        parse_mode=ParseMode.HTML,
    )
    await cb.answer()


@register_main_callback(F.data.regexp("confirm_account_deletion_"))
async def confirm_account_deletion(client, cb):
    uid = cb.from_user.id
    acc_id = cb.data.replace("confirm_account_deletion_", "")
    try:
        deleted = db.delete_user_account(uid, acc_id)
    except Exception as e:
        logger.error(f"Failed to delete account {acc_id} for user {uid}: {e}")
        await cb.answer("Error deleting account. Try again.", show_alert=True)
        await send_dm_log(uid, f"<b>❌ Failed to delete account:</b> {str(e)}")
        return

    if not deleted:
        await cb.answer("That account is no longer available.", show_alert=True)
        await view_accounts(client, cb)
        return

    await edit_message_or_reply(
        cb.message,
        "<b>Account deleted!</b>\n\n"
        "Account removed successfully.✅",
        reply_markup=kb([
            [danger_inline_button("Back", callback_data="view_accounts")]
        ]),
        parse_mode=ParseMode.HTML,
    )
    await cb.answer()
    await send_dm_log(
        uid,
        f'Account deleted: ID {acc_id} '
        '<tg-emoji emoji-id="5258269142699489886">🗑️</tg-emoji>',
    )
    logger.info(f"Account {acc_id} deleted by user {uid}")


@register_main_callback(F.data.regexp("cancel_account_deletion_"))
async def cancel_account_deletion(client, cb):
    await view_accounts(client, cb)
    await cb.answer()


@register_main_callback(F.data.regexp("view_acc_"))
async def view_account(client, cb):
    uid = cb.from_user.id
    acc_id = cb.data.replace("view_acc_", "")
    accounts = db.get_user_accounts(uid)
    account = next((acc for acc in accounts if str(acc["_id"]) == acc_id), None)
    if not account:
        await cb.answer("That account is no longer available. Refresh Account Management.", show_alert=True)
        return
    
    status = "Active ✅" if account_is_active(account) else "Inactive ⭕"
    caption = (
        page_header("📱 ACCOUNT DETAILS") +
        f"<b>Phone:</b> <code>{account['phone_number']}</code>\n"
        f"<b>Status:</b> {status}\n\n"
        "Account sessions and authentication settings are managed through the existing account flow."
    )
    
    await edit_message_or_reply(
        cb.message,
        caption,
        reply_markup=kb([
            [danger_inline_button("Delete Account", callback_data=f"delete_acc_{acc_id}")],
            [InlineKeyboardButton("← Account Management", callback_data="view_accounts")]
        ]),
        parse_mode=ParseMode.HTML,
    )

@register_main_callback(F.data.regexp("set_msg"))
async def set_msg(client, cb):
    uid = cb.from_user.id
    db.set_user_state(uid, "waiting_broadcast_msg")
    await render_advertisement_prompt(cb.message, uid)


def advertisement_prompt_text(human_behaviour_active):
    status = "Active 🛟" if human_behaviour_active else "Deactivated"
    return (
        "Set Advertisement\n\n"
        f"• Human behaviour: {status}\n\n"
        "<blockquote><b>Human behaviour:</b>\n"
        "Human behaviour adds a natural pause between group deliveries. Your ad text is preserved "
        "and Telegram rate limits are observed.\n"
        "<b>Tips for effective ads:</b>\n"
        "•Keep it concise and engaging\n"
        "•Use premium emojis for flair(premium emojis will only work if you have premium on your account.)\n"
        "•You can use bold, italic, underline, mono, strikethrough, quotes, spoilers &amp; links — they're preserved\n"
        "•Include clear call-to-action\n"
        "•Avoid excessive caps or spam words</blockquote>\n\n"
        "Send your new message to set as your advertisement:"
    )


async def render_advertisement_prompt(message, uid):
    active = db.get_human_behaviour(uid)
    caption = advertisement_prompt_text(active)
    reply_markup = human_behaviour_keyboard(active)
    await edit_message_or_reply(
        message,
        caption,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML,
    )
    return message


@register_main_callback(F.data.regexp("^toggle_human_behaviour$"))
async def toggle_human_behaviour(client, cb):
    uid = cb.from_user.id
    active = not db.get_human_behaviour(uid)
    db.set_human_behaviour(uid, active)
    db.set_user_state(uid, "waiting_broadcast_msg")
    await render_advertisement_prompt(cb.message, uid)
    await cb.answer(
        "Human behaviour activated." if active else "Human behaviour deactivated."
    )

@register_main_callback(F.data.regexp("set_delay"))
async def set_delay(client, cb):
    uid = cb.from_user.id
    current_delay = db.get_user_ad_delay(uid)
    
    await edit_message_or_reply(
        cb.message,
        "Set Cycle Interval\n\n"
        f"Current Interval: {format_interval(current_delay)}\n\n"
        "Choose duration out of the below options as your interval between cycles:",
        reply_markup=quick_delay_keyboard(),
        parse_mode=ParseMode.HTML,
    )
    db.set_user_state(uid, "waiting_broadcast_delay")

@register_main_callback(F.data.regexp("quick_delay_"))
async def quick_delay(client, cb):
    uid = cb.from_user.id
    delay = int(cb.data.split("_")[-1])
    
    try:
        db.set_user_ad_delay(uid, delay)
    except Exception as e:
        logger.error(f"Failed to set ad delay for user {uid}: {e}")
        await cb.answer("Error setting delay. Try again.", show_alert=True)
        return
    
    await edit_message_or_reply(
        cb.message,
        f"<b>{delay // 60} min has been set as interval duration for your adbots. ✅</b>",
        reply_markup=kb([[InlineKeyboardButton("← Dashboard", callback_data="menu_main")]]),
        parse_mode=ParseMode.HTML,
    )
    await send_dm_log(
        uid,
        '<tg-emoji emoji-id="5373236586760651455">⏱️</tg-emoji> '
        f"Cycle interval updated: {format_interval(delay).lower()}",
    )
    db.set_user_state(uid, "")

@register_main_callback(F.data.regexp("start_broadcast"))
async def start_broadcast(client, cb):
    uid = cb.from_user.id
    if uid in joiner_tasks:
        await cb.answer("Stop the active join operation before starting a broadcast.", show_alert=True)
        return
    if uid in broadcast_operations:
        await cb.answer("Please wait for the current broadcast action to finish.", show_alert=True)
        return
    if db.get_broadcast_state(uid).get("running"):
        await cb.answer("Broadcast is already running.", show_alert=True)
        return

    broadcast_operations.add(uid)
    task = None
    try:
        if not db.get_user_ad_messages(uid):
            await cb.answer("Set an advertisement before starting.", show_alert=True)
            return
        
        accounts = db.get_user_accounts(uid)
        if not accounts:
            await cb.answer("Add an account before starting.", show_alert=True)
            return
        
        if not db.get_logger_status(uid):
            try:
                await edit_message_or_reply(
                    cb.message,
                    page_header("📋 LOGGER SETUP")
                    + f"Start @{config.LOGGER_BOT_USERNAME.lstrip('@')} to receive cycle summaries, then return to the dashboard.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb([
                        [InlineKeyboardButton("Start Logger Bot 📩", url=f"https://t.me/{config.LOGGER_BOT_USERNAME.lstrip('@')}")],
                        [InlineKeyboardButton("← Dashboard", callback_data="menu_main")]
                    ])
                )
            except Exception as e:
                logger.error(f"Failed to edit logger bot message for {uid}: {e}")
                await cb.answer("Unable to open the logger setup. Please try again.", show_alert=True)
            return
        
        current_task = user_tasks.get(uid)
        if current_task and not current_task.done():
            await cb.answer("A broadcast task is already active.", show_alert=True)
            return

        db.set_broadcast_state(uid, running=True)
        dashboard_message_refs[uid] = (
            cb.message.chat.id,
            cb.message.message_id,
            bool(
                getattr(cb.message, "content_type", None) == "photo"
                or getattr(cb.message, "caption", None) is not None
                or getattr(cb.message, "photo", None)
            ),
        )
        await edit_dashboard(cb.message, uid)
        task = asyncio.create_task(run_broadcast(client, uid))
        user_tasks[uid] = task
        broadcast_operations.discard(uid)
        await cb.answer("Ad is running.", show_alert=True)
        await send_dm_log(
            uid,
            '<tg-emoji emoji-id="5285439518130857782">🚀</tg-emoji> '
            "Broadcast started! Logs will come here",
        )
        logger.info(f"Broadcast started via dashboard for user {uid}")
    except Exception as e:
        logger.error(f"Error in start_broadcast for {uid}: {e}")
        if task is None:
            db.set_broadcast_state(uid, running=False)
            dashboard_message_refs.pop(uid, None)
            try:
                await edit_dashboard(cb.message, uid)
            except Exception as dashboard_error:
                logger.error(f"Failed to restore dashboard after start error for {uid}: {dashboard_error}")
        try:
            await cb.answer("Unable to start the broadcast. Please try again.", show_alert=True)
        except Exception as answer_error:
            logger.error(f"Failed to answer start callback for user {uid}: {answer_error}")
        await send_dm_log(
            uid,
            f"<b>❌ Failed to start broadcast:</b> {html.escape(str(e))} 😔",
        )
    finally:
        broadcast_operations.discard(uid)

@register_main_callback(F.data.regexp("stop_broadcast"))
async def stop_broadcast(client, cb):
    uid = cb.from_user.id
    has_active_dashboard = uid in dashboard_message_refs
    stopped = await stop_broadcast_task(client, uid)
    if not stopped:
        await cb.answer("No broadcast is currently running.", show_alert=True)
        return
    
    try:
        if not has_active_dashboard:
            await edit_dashboard(cb.message, uid)
        await cb.answer("Ads stopped.", show_alert=False)
    except Exception as e:
        logger.error(f"Failed to refresh dashboard after stopping broadcast for {uid}: {e}")
        await cb.answer("Ads stopped, but the dashboard could not be refreshed.", show_alert=True)
    await send_dm_log(
        uid,
        '<tg-emoji emoji-id="5440632582209287180">⏹️</tg-emoji> '
        "Broadcast stopped!",
    )
    logger.info(f"Broadcast stopped via callback for user {uid}")

@register_main_callback(F.data.regexp("auto_reply"))
async def auto_reply(client, cb):
    await cb.answer("Launching Soon.", show_alert=True)

@register_main_callback(F.data.regexp("^auto_join$"))
async def auto_join(client, cb):
    uid = cb.from_user.id
    if uid in joiner_tasks:
        await edit_message_or_reply(
            cb.message,
            auto_join_text(),
            reply_markup=auto_join_keyboard(),
            parse_mode=ParseMode.HTML,
        )
    else:
        db.set_user_state(uid, "")
        await replace_with_banner(
            cb.message,
            auto_join_text(),
            auto_join_keyboard(),
        )
    await cb.answer()


@register_main_callback(F.data.regexp("^joiner_dashboard$"))
async def joiner_dashboard(client, cb):
    uid = cb.from_user.id
    db.set_user_state(uid, "")
    await edit_dashboard(cb.message, uid)
    await cb.answer()


@register_main_callback(F.data.regexp(r"^joiner_select_(group|folder)$"))
async def joiner_select_account(client, cb):
    uid = cb.from_user.id
    if uid in joiner_tasks:
        await cb.answer("A join task is already running.", show_alert=True)
        return
    if db.get_broadcast_state(uid).get("running", False):
        await cb.answer("Stop the active broadcast before starting a join operation.", show_alert=True)
        return

    mode = cb.data.rsplit("_", 1)[1]
    if mode not in {"group", "folder"}:
        await cb.answer("Unknown join mode.", show_alert=True)
        return
    state = "waiting_group_join_links" if mode == "group" else "waiting_folder_join_links"
    db.set_user_state(uid, state)
    await replace_with_banner(
        cb.message,
        joiner_prompt_text(mode),
        joiner_prompt_keyboard(),
    )
    await cb.answer()


@register_main_callback(F.data.regexp(r"^joiner_account_(group|folder)_[a-fA-F0-9]{24}$"))
async def joiner_account_selected(client, cb):
    uid = cb.from_user.id
    _, _, mode, _ = cb.data.split("_", 3)
    if uid in joiner_tasks or db.get_broadcast_state(uid).get("running", False):
        await cb.answer("Stop the active operation before starting another join task.", show_alert=True)
        return

    if mode not in {"group", "folder"}:
        await cb.answer("Unknown join mode.", show_alert=True)
        return
    db.set_user_state(
        uid,
        "waiting_group_join_links" if mode == "group" else "waiting_folder_join_links",
    )
    await replace_with_banner(
        cb.message,
        joiner_prompt_text(mode),
        joiner_prompt_keyboard(),
    )
    await cb.answer()


@register_main_callback(F.data.regexp("^next_folder_batch$"))
async def next_folder_batch(client, cb):
    uid = cb.from_user.id
    if uid in joiner_tasks:
        await cb.answer("Stop the active join task before submitting more links.", show_alert=True)
        return
    db.set_user_state(uid, "waiting_folder_join_links")
    await replace_with_banner(
        cb.message,
        joiner_prompt_text("folder"),
        joiner_prompt_keyboard(),
    )
    await cb.answer()


@register_main_callback(F.data.regexp("^finish_folder_join$"))
async def finish_folder_join_callback(client, cb):
    await cb.answer(
        "Submit up to six add-list links together; each submission has one summary.",
        show_alert=True,
    )


@register_main_callback(F.data.regexp("^cancel_joiner$"))
async def cancel_joiner(client, cb):
    uid = cb.from_user.id
    task = joiner_tasks.get(uid)
    if task and not task.done():
        task.cancel()
        await cb.answer("Stopping safely; confirmed results will be reported.")
        try:
            await task
        except asyncio.CancelledError:
            logger.info(f"Auto-join task cancelled for user {uid}")
        return

    db.set_user_state(uid, "")
    await auto_join(client, cb)


@register_main_callback(F.data.regexp("^delete_joiner_summary$"))
async def delete_joiner_summary(client, cb):
    await cb.message.delete()
    await cb.answer()

@register_main_callback(F.data.regexp("^settings$"))
async def settings(client, cb):
    uid = cb.from_user.id
    user = db.get_user(uid) or {}
    ad_is_set = bool(db.get_user_ad_messages(uid))
    logger_is_active = db.get_logger_status(uid)
    await edit_message_or_reply(
        cb.message,
        page_header("⚙️ SETTINGS")
        + f"• Advertisement: <b>{'Configured ✅' if ad_is_set else 'Not configured ⚪'}</b>\n"
        + f"• Broadcast interval: <b>{format_interval(db.get_user_ad_delay(uid))}</b>\n"
        + f"• Cycle-summary logs: <b>{'Ready ✅' if logger_is_active else 'Logger not started ⚪'}</b>\n"
        + f"• Account plan: <b>{user.get('plan', 'Free')}</b>",
        reply_markup=kb([
            [
                InlineKeyboardButton("Advertisement", callback_data="set_msg"),
                InlineKeyboardButton("Interval", callback_data="set_delay")
            ],
            [InlineKeyboardButton("Auto Reply", callback_data="auto_reply")],
            [InlineKeyboardButton("← Dashboard", callback_data="menu_main")]
        ]),
        parse_mode=ParseMode.HTML,
    )

@register_main_callback(F.data.regexp("^view_logs$"))
async def view_logs(client, cb):
    uid = cb.from_user.id
    is_logger_active = db.get_logger_status(uid)
    await edit_message_or_reply(
        cb.message,
        page_header("📋 BROADCAST LOGS")
        + f"<b>Logger status:</b> {'Connected ✅' if is_logger_active else 'Not started ⚪'}\n\n"
        + "Cycle summaries are sent to your logger chat. Each cycle uses one summary message; "
        + "there are no separate group progress messages.",
        reply_markup=kb([
            [InlineKeyboardButton(
                "Open Logger Bot",
                url=f"https://t.me/{config.LOGGER_BOT_USERNAME.lstrip('@')}",
            )],
            [InlineKeyboardButton("← Dashboard", callback_data="menu_main")]
        ]),
        parse_mode=ParseMode.HTML,
    )

@register_main_callback(F.data.regexp("^premium$"))
async def premium(client, cb):
    uid = cb.from_user.id
    user = db.get_user(uid) or {}
    plan = html.escape(str(user.get("plan", "Free")))
    subscription_status = html.escape(str(user.get("subscription_status", "Not configured")))
    await edit_message_or_reply(
        cb.message,
        page_header("⭐ PREMIUM MEMBERSHIP")
        + "👤 <b>CURRENT PLAN</b>\n"
        + f"• Plan: <b>{plan}</b>\n"
        + f"• Hosted accounts: <b>{db.get_user_accounts_count(uid)}/{account_limit_label(user)}</b>\n"
        + f"• Subscription status: <b>{subscription_status}</b>\n\n"
        + "⚡ <b>PREMIUM FEATURES</b>\n"
        + "Premium features are not configured in this build.\n\n"
        + "✅ <b>PLAN BENEFITS</b>\n"
        + "No premium benefits or entitlements are configured for this account.\n\n"
        + "💳 <b>AVAILABLE PLANS</b>\n"
        + "A premium plan catalog and pricing are not configured.\n\n"
        + "Payment integration is not configured. This bot cannot take payments "
        + "or activate a subscription.",
        reply_markup=kb([
            [InlineKeyboardButton("💬 CONTACT SUPPORT", url=config.SUPPORT_GROUP_URL)],
            [InlineKeyboardButton("← BACK TO DASHBOARD", callback_data="menu_main")]
        ]),
        parse_mode=ParseMode.HTML,
    )

@register_main_callback(F.data.regexp("^about_bot$"))
async def about_bot(client, cb):
    await edit_message_or_reply(
        cb.message,
        page_header("ℹ️ ABOUT ZOVRY ADS")
        + "ZovryAds helps manage Telegram ad broadcasts from your hosted accounts.\n"
        + "Use the dashboard to configure an advertisement, set an interval, and manage accounts.\n\n"
        + f"<b>Support:</b> @{config.ADMIN_USERNAME}",
        reply_markup=kb([
            [InlineKeyboardButton("Support", url=config.SUPPORT_GROUP_URL)],
            [InlineKeyboardButton("← Dashboard", callback_data="menu_main")]
        ]),
        parse_mode=ParseMode.HTML,
    )

@register_main_callback(F.data.regexp("analytics"))
async def analytics(client, cb):
    uid = cb.from_user.id
    user_stats = db.get_user_analytics(uid)
    accounts = db.get_user_accounts(uid)
    logger_failures = len(db.get_logger_failures(uid))
    
    analytics_text = (
        page_header("📊 BROADCAST STATISTICS") +
        f"✅ Successful deliveries: <b>{user_stats.get('total_sent', 0):,}</b>\n"
        f"❌ Failed deliveries: <b>{user_stats.get('total_failed', 0):,}</b>\n"
        f"🔁 Completed cycles: <b>{user_stats.get('total_cycles', 0):,}</b>\n"
        f"👤 Active accounts: <b>{len([a for a in accounts if a['is_active']])}</b>\n"
        f"📋 Logger issues: <b>{logger_failures}</b>\n"
        f"⏱ Interval: <b>{format_interval(db.get_user_ad_delay(uid))}</b>\n\n"
        f"<b>Delivery rate:</b> {generate_progress_bar(user_stats.get('total_sent', 0), user_stats.get('total_sent', 0) + user_stats.get('total_failed', 0))}"
    )
    
    await edit_message_or_reply(
        cb.message,
        analytics_text,
        reply_markup=kb([
            [InlineKeyboardButton("Detailed Statistics", callback_data="detailed_report")],
            [InlineKeyboardButton("← Dashboard", callback_data="menu_main")]
        ]),
        parse_mode=ParseMode.HTML,
    )

@register_main_callback(F.data.regexp("detailed_report"))
async def detailed_report(client, cb):
    uid = cb.from_user.id
    user_stats = db.get_user_analytics(uid)
    accounts = db.get_user_accounts(uid)
    logger_failures = db.get_logger_failures(uid)
    
    detailed_text = (
        page_header("📈 DETAILED STATISTICS") +
        f"Report date: <b>{datetime.now().strftime('%d/%m/%y')}</b>\n"
        f"Account ID: <code>{uid}</code>\n\n"
        "<b>Broadcasts</b>\n"
        f"• Successful: <b>{user_stats.get('total_sent', 0):,}</b>\n"
        f"• Failed: <b>{user_stats.get('total_failed', 0):,}</b>\n"
        f"• Total deliveries: <b>{user_stats.get('total_broadcasts', 0):,}</b>\n"
        f"• Completed cycles: <b>{user_stats.get('total_cycles', 0):,}</b>\n\n"
        "<b>Accounts</b>\n"
        f"• Total: <b>{len(accounts)}</b>\n"
        f"• Active: <b>{len([a for a in accounts if a['is_active']])}</b>\n"
        f"• Inactive: <b>{len([a for a in accounts if not a['is_active']])}</b>\n\n"
        f"<b>Logger errors:</b> {len(logger_failures)}"
    )
    
    await edit_message_or_reply(
        cb.message,
        detailed_text,
        reply_markup=kb([
            [InlineKeyboardButton("← Statistics", callback_data="analytics")]
        ]),
        parse_mode=ParseMode.HTML,
    )

@register_main_message(Command("stats"), F.from_user.id.in_(ALLOWED_BD_IDS))
async def admin_stats(client, m):
    try:
        stats = db.get_admin_stats()
        
        stats_text = (
            page_header("🛠 ADMIN OVERVIEW") +
            f"<b>Report:</b> {datetime.now().strftime('%d/%m/%y · %I:%M %p')}\n\n"
            f"👤 Users: <b>{stats.get('total_users', 0):,}</b>\n"
            f"📱 Hosted accounts: <b>{stats.get('total_accounts', 0):,}</b>\n"
            f"✅ Successful deliveries: <b>{stats.get('total_forwards', 0):,}</b>\n"
            f"📋 Active logger users: <b>{stats.get('active_logger_users', 0):,}</b>\n"
        )
        
        await m.answer_photo(
            photo=config.START_IMAGE,
            caption=stats_text,
            reply_markup=kb([[InlineKeyboardButton("Open Dashboard", callback_data="menu_main")]]),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await m.answer(f"<b>Unable to generate statistics</b>\n{str(e)}", parse_mode=ParseMode.HTML)

@register_main_message(Command("stats"), ~F.from_user.id.in_(ALLOWED_BD_IDS))
async def non_admin_stats(client, m):
    await m.answer("This command is available to administrators only.")

@register_main_message(Command("bd"), F.from_user.id.in_(ALLOWED_BD_IDS))
async def admin_broadcast(client, m):
    uid = m.from_user.id
    if not is_owner(uid):
        await m.answer("This command is available to administrators only.", parse_mode=ParseMode.HTML)
        return
    
    if not m.reply_to_message:
        await m.answer("<b>📣 ADMIN ANNOUNCEMENT</b>\n\nReply to a message to send it to users.", parse_mode=ParseMode.HTML)
        return
    
    all_users = db.get_all_users(limit=0)  # Fetch all users without limit
    if not all_users:
        await m.answer("No users are available for this announcement.", parse_mode=ParseMode.HTML)
        return
    
    total_users = len(all_users)
    status_msg = await m.answer(
        "<b>📣 ADMIN ANNOUNCEMENT</b>\n\nPreparing the broadcast…",
        parse_mode=ParseMode.HTML
    )
    
    sent_count = 0
    failed_count = 0
    
    reply_msg = m.reply_to_message
    media = None
    caption = reply_msg.caption or reply_msg.text or ""
    
    if reply_msg.photo:
        media = reply_msg.photo[-1].file_id
    elif reply_msg.document:
        media = reply_msg.document.file_id
    elif reply_msg.video:
        media = reply_msg.video.file_id
    
    for user in all_users:
        user_id = user['user_id']
        try:
            if media:
                await client.send_photo(
                    chat_id=user_id,
                    photo=media,
                    caption=caption,
                    parse_mode=ParseMode.HTML
                )
            else:
                await client.send_message(
                    chat_id=user_id,
                    text=caption,
                    parse_mode=ParseMode.HTML
                )
            sent_count += 1
        except (TelegramForbiddenError, TelegramBadRequest) as error:
            logger.error(f"Failed to send broadcast to user {user_id}: {error}")
            failed_count += 1
            await send_dm_log(user_id, f"<b>⚠️ Admin broadcast failed:</b> {str(error)}")
        except TelegramRetryAfter as e:
            logger.warning(f"Flood wait for user {user_id}: Wait {e.retry_after} seconds")
            await asyncio.sleep(e.retry_after)
            try:
                if media:
                    await client.send_photo(chat_id=user_id, photo=media, caption=caption, parse_mode=ParseMode.HTML)
                else:
                    await client.send_message(chat_id=user_id, text=caption, parse_mode=ParseMode.HTML)
                sent_count += 1
            except Exception:
                failed_count += 1
                await send_dm_log(user_id, f"<b>⚠️ Admin broadcast failed after wait:</b> {str(e)} ")
        except Exception as e:
            logger.error(f"Failed to send broadcast to user {user_id}: {e}")
            failed_count += 1
            await send_dm_log(user_id, f"<b>⚠️ Admin broadcast failed:</b> {str(e)} ")
        if (sent_count + failed_count) % 10 == 0 or (sent_count + failed_count) == total_users:
            try:
                await status_msg.edit_text(
                    "<b>📣 ADMIN ANNOUNCEMENT</b>\n\n"
                    "<b>Status:</b> Sending\n"
                    f"✅ Delivered: <b>{sent_count}/{total_users}</b>\n"
                    f"❌ Failed: <b>{failed_count}</b>\n"
                    f"<b>Progress:</b> {generate_progress_bar(sent_count + failed_count, total_users)}",
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                logger.error(f"Failed to update broadcast status: {e}")
        await asyncio.sleep(0.5)
    
    await status_msg.edit_text(
        "<b>✅ ADMIN ANNOUNCEMENT COMPLETE</b>\n\n"
        f"✅ Delivered: <b>{sent_count}/{total_users}</b>\n"
        f"❌ Failed: <b>{failed_count}</b>\n"
        f"<b>Delivery rate:</b> {generate_progress_bar(sent_count, total_users)}",
        parse_mode=ParseMode.HTML
    )
    await send_dm_log(uid, f"<b>🏁 Admin broadcast completed:</b> Sent {sent_count}/{total_users}, Failed {failed_count} ✨")

@register_main_message(Command("bd"), ~F.from_user.id.in_(ALLOWED_BD_IDS))
async def non_admin_bd(client, m):
    await m.answer("This command is available to administrators only.")

@register_main_message(Command("stop"))
async def stop_command(client, m):
    uid = m.from_user.id
    stopped = await stop_broadcast_task(client, uid)
    if stopped:
        await m.answer("<b>⏹️ Ads stopped</b>\n\nThe broadcast task has been stopped.", parse_mode=ParseMode.HTML)
        await send_dm_log(
            uid,
            '<tg-emoji emoji-id="5440632582209287180">⏹️</tg-emoji> '
            "Broadcast stopped!",
        )
    else:
        await m.answer("<b>ℹ️ No active broadcast</b>", parse_mode=ParseMode.HTML)

@register_main_message(Command("me"))
async def user_info(client, m):
    uid = m.from_user.id
    user = db.get_user(uid)
    
    if not user:
        await m.answer("Your profile is not registered. Send /start to begin.", parse_mode=ParseMode.HTML)
        return
    
    accounts_count = db.get_user_accounts_count(uid)
    
    status_text = (
        page_header("👤 YOUR ACCOUNT")
        +
        f"<b>User ID:</b> <code>{uid}</code>\n"
        f"<b>Username:</b> @{user.get('username', 'N/A')}\n"
        f"<b>Plan:</b> {user.get('plan', 'Free')}\n"
        f"<b>Hosted accounts:</b> {accounts_count}/{account_limit_label(user)}\n"
        f"<b>Cycle-summary logger:</b> {'Connected ✅' if db.get_logger_status(uid) else 'Not started ⚪'}\n\n"
        "<b>Available tools</b>\n"
        "• Account management\n"
        "• Scheduled ad broadcasts\n"
        "• Cycle-based statistics and logs"
    )
    
    status_buttons = [
        [InlineKeyboardButton("Open Dashboard", callback_data="menu_main")],
        [InlineKeyboardButton("Support 💬", url=config.SUPPORT_GROUP_URL)]
    ]
    
    await m.answer_photo(
        photo=config.START_IMAGE,
        caption=status_text,
        reply_markup=kb(status_buttons),
        parse_mode=ParseMode.HTML
    )

@register_main_message(Command("start"))
async def start(client, m):
    uid = m.from_user.id
    username = m.from_user.username or "Unknown"
    first_name = m.from_user.first_name or "User"
    
    db.create_user(uid, username, first_name)
    if is_owner(uid):
        db.db.users.update_one({"user_id": uid}, {"$set": {"accounts_limit": "unlimited"}})
    db.update_user_last_interaction(uid)

    try:
        async with force_join_lock(uid):
            if config.ENABLE_FORCE_JOIN and not await is_joined_all(client, uid):
                await delete_force_join_prompts(client, uid)
                db.reset_force_join_cycle(uid)
                sent = await client.send_photo(
                    chat_id=uid,
                    photo=config.FORCE_JOIN_IMAGE,
                    caption=forced_subscription_message(
                        config.MUST_JOIN_CHANNEL_URL,
                        config.MUSTJOIN_GROUP_URL,
                    ),
                    reply_markup=force_join_inline_keyboard(
                        config.MUST_JOIN_CHANNEL_ID,
                        config.MUST_JOIN_CHANNEL_URL,
                        config.MUSTJOIN_GROUP_ID,
                        config.MUSTJOIN_GROUP_URL,
                    ),
                    parse_mode=ParseMode.HTML
                )
                db.add_force_join_prompt(uid, sent.message_id)
                logger.info(f"Sent force join message to user {uid}")
                return

            await delete_force_join_prompts(client, uid)
            user = db.get_user(uid) or {}
            if has_seen_post_welcome(user):
                if not db.unlock_dashboard_for_start(uid):
                    raise RuntimeError(f"Could not unlock dashboard for user {uid}")
                await edit_dashboard(m, uid)
                logger.info(f"Opened dashboard from /start for user {uid}")
                return

            await send_welcome_message(client, uid)
    except Exception as e:
        logger.error(f"Failed to send start or force-join message to {uid}: {e}")
        await client.send_message(uid, "Unable to load the dashboard. Please try again or contact support.")


@register_main_chat_member(
    ChatMemberUpdatedFilter(member_status_changed=LEAVE_TRANSITION)
)
@register_main_chat_member(
    ChatMemberUpdatedFilter(member_status_changed=JOIN_TRANSITION)
)
async def handle_force_join_chat_member(client: Bot, update: ChatMemberUpdated):
    if not config.ENABLE_FORCE_JOIN:
        return

    chat_kind = required_force_join_chat_kind(
        update.chat,
        config.MUST_JOIN_CHANNEL_ID,
        config.MUSTJOIN_GROUP_ID,
    )
    member = update.new_chat_member
    if not chat_kind:
        return

    user_id = member.user.id
    if not chat_member_is_joined(member):
        async with force_join_lock(user_id):
            user = db.get_user(user_id)
            if user:
                db.mark_force_join_membership_lost(user_id)
                logger.info(
                    f"User {user_id} left required {chat_kind}; reset force-join welcome state"
                )
        return

    async with force_join_lock(user_id):
        user = db.get_user(user_id)
        if not user or not user.get("force_join_pending", False):
            return

        other_chat_id = (
            config.MUSTJOIN_GROUP_ID
            if chat_kind == "channel"
            else config.MUST_JOIN_CHANNEL_ID
        )
        if not await is_joined(client, user_id, other_chat_id):
            return

        await delete_force_join_prompts(client, user_id)
        try:
            await send_welcome_message(client, user_id)
        except TelegramAPIError as error:
            logger.error(f"Failed to send post-join welcome to {user_id}: {error}")
            return
        logger.info(f"User {user_id} joined both required chats; sent welcome message")


@register_main_message(F.document, F.chat.type == ChatType.PRIVATE)
async def handle_joiner_document(client, m):
    uid = m.from_user.id
    if not db.get_user_state(uid).startswith("waiting_group_join_links"):
        return
    filename = (m.document.file_name or "").casefold()
    if not filename.endswith(".txt"):
        await reply_with_input_prompt(
            m,
            uid,
            "Please send the group list as a .txt file.",
            reply_markup=joiner_prompt_keyboard(),
        )
        return
    if m.document.file_size and m.document.file_size > 2 * 1024 * 1024:
        await reply_with_input_prompt(
            m,
            uid,
            "The .txt file is too large. Send a file no larger than 2 MB.",
            reply_markup=joiner_prompt_keyboard(),
        )
        return

    try:
        downloaded = await client.download(m.document)
        if downloaded is None:
            raise RuntimeError("Aiogram returned no file content.")
        try:
            downloaded.seek(0)
            content = downloaded.read().decode("utf-8-sig")
        finally:
            downloaded.close()
    except UnicodeDecodeError:
        await reply_with_input_prompt(
            m,
            uid,
            "The .txt file must use UTF-8 encoding.",
            reply_markup=joiner_prompt_keyboard(),
        )
        return
    except TelegramAPIError as error:
        logger.error("Could not download group-link file for %s: %s", uid, error)
        await reply_with_input_prompt(
            m,
            uid,
            "Telegram could not download that file. Please try again.",
            reply_markup=joiner_prompt_keyboard(),
        )
        return
    except RuntimeError as error:
        logger.error("Could not read group-link file for %s: %s", uid, error)
        await reply_with_input_prompt(
            m,
            uid,
            "The file could not be read. Please try again.",
            reply_markup=joiner_prompt_keyboard(),
        )
        return
    except OSError as error:
        logger.error("Could not read downloaded group-link file for %s: %s", uid, error)
        await reply_with_input_prompt(
            m,
            uid,
            "The file could not be read. Please try again.",
            reply_markup=joiner_prompt_keyboard(),
        )
        return

    await start_joiner_from_input(uid, m, content, "group")


@register_main_message(F.text, F.chat.type == ChatType.PRIVATE)
async def handle_text_message(client, m):
    uid = m.from_user.id
    state = db.get_user_state(uid)
    text = m.text or ""

    if state.startswith("processing_"):
        if uid in joiner_tasks:
            await m.answer("A join task is already processing. Use its Stop joining button or wait for the summary.")
        else:
            db.set_user_state(uid, "")
            await m.answer(
                "The previous join task was interrupted. Open Auto Join and submit the links again.",
                reply_markup=kb([[danger_inline_button("Back", callback_data="auto_join")]]),
            )
        return
    elif (
        state == "waiting_group_join_links"
        or state.startswith("waiting_group_join_links:")
        or state == "waiting_folder_join_links"
        or state.startswith("waiting_folder_batch:")
        or state.startswith("waiting_folder_join_links:")
    ):
        mode = (
            "group"
            if state == "waiting_group_join_links" or state.startswith("waiting_group_join_links:")
            else "folder"
        )
        await start_joiner_from_input(uid, m, text, mode)
        return

    elif state == "waiting_group_link":
        tg_client = None
        try:
            tg_client = TelegramClient(StringSession(), config.API_ID, config.API_HASH)
            await tg_client.connect()
            chat = await tg_client.get_entity(text)
            db.add_target_group(uid, chat.id, chat.title)
            await m.answer(
                f"<b>✅ GROUP FILTER ADDED</b>\n\n<i>{html.escape(chat.title)}</i>",
                parse_mode=ParseMode.HTML,
            )
            await send_dm_log(uid, f"<b>🎯 Group added:</b> <i>{html.escape(chat.title)}</i> ✨")
            db.set_user_state(uid, "")
        except Exception as e:
            safe_error = safe_telegram_reason(e)
            await reply_with_input_prompt(
                m,
                uid,
                f"<b>❌ UNABLE TO ADD GROUP FILTER</b>\n\n{html.escape(safe_error)}",
                parse_mode=ParseMode.HTML,
            )
            await send_dm_log(uid, f"<b>❌ Failed to add group:</b> {html.escape(safe_error)} 😔")
            logger.error(f"Failed to add group filter for user {uid}: {type(e).__name__}")
        finally:
            if tg_client is not None and tg_client.is_connected():
                await tg_client.disconnect()
    elif state == "waiting_broadcast_msg":
        try:
            if not text.strip():
                await reply_with_input_prompt(
                    m,
                    uid,
                    "Send a non-empty advertisement message.",
                )
                return
            formatted_message = html_decoration.unparse(text, m.entities)
            db.add_user_ad_message(
                uid,
                formatted_message,
                datetime.now(),
                message_format="html",
            )
            db.set_user_state(uid, "")
            await m.answer(
                page_header("✅ ADVERTISEMENT SAVED") +
                f"<b>Preview:</b>\n{html.escape(text)}\n\n"
                f"Your advertisement is ready to use.",
                parse_mode=ParseMode.HTML,
                reply_markup=kb([[InlineKeyboardButton("Open Dashboard", callback_data="menu_main")]])
            )
            await send_dm_log(
                uid,
                '<tg-emoji emoji-id="5451636889717062286">📝</tg-emoji> '
                "Your Ad message has been updated",
            )
            logger.info(f"Ad message set for user {uid}: {text[:50]}...")
        except Exception as e:
            logger.error(f"Failed to add ad message for user {uid}: {e}")
            db.set_user_state(uid, "")
            await m.answer(
                page_header("❌ ADVERTISEMENT NOT SAVED") +
                f"Error: {str(e)}\n"
                f"Contact @{config.ADMIN_USERNAME} for help.",
                parse_mode=ParseMode.HTML,
                reply_markup=kb([[InlineKeyboardButton("← Dashboard", callback_data="menu_main")]])
            )
            await send_dm_log(uid, f"<b>❌ Failed to set ad message:</b> {str(e)}")
    elif state == "waiting_broadcast_delay":
        try:
            delay = int(text)
            if delay < 120:
                await reply_with_input_prompt(
                    m,
                    uid,
                    page_header("❌ INTERVAL TOO SHORT")
                    + "Minimum interval: 120 seconds.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb([[InlineKeyboardButton("← Dashboard", callback_data="menu_main")]])
                )
                return
            if delay > 86400:
                await reply_with_input_prompt(
                    m,
                    uid,
                    page_header("❌ INTERVAL TOO LONG")
                    + "Maximum interval: 86400 seconds (24 hours).",
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb([[InlineKeyboardButton("← Dashboard", callback_data="menu_main")]])
                )
                return
            db.set_user_ad_delay(uid, delay)
            db.set_user_state(uid, "")
            mode = "Custom" if delay not in (300, 600, 1200) else "Preset"
            await m.answer(
                page_header("✅ INTERVAL SAVED") +
                f"<b>New interval:</b> {format_interval(delay)} ({delay}s)\n"
                f"<b>Configuration:</b> {mode}",
                parse_mode=ParseMode.HTML,
                reply_markup=kb([[InlineKeyboardButton("Open Dashboard", callback_data="menu_main")]])
            )
            await send_dm_log(
                uid,
                '<tg-emoji emoji-id="5373236586760651455">⏱️</tg-emoji> '
                f"Cycle interval updated: {format_interval(delay).lower()}",
            )
            logger.info(f"Broadcast delay set for user {uid}: {delay}s")
        except ValueError:
            await reply_with_input_prompt(
                m,
                uid,
                page_header("❌ INVALID INTERVAL")
                + "Enter a number of seconds, for example <code>300</code> for 5 minutes.",
                parse_mode=ParseMode.HTML,
                reply_markup=kb([[InlineKeyboardButton("← Dashboard", callback_data="menu_main")]])
            )
        except Exception as e:
            logger.error(f"Failed to set broadcast delay for user {uid}: {e}")
            db.set_user_state(uid, "")
            await m.answer(
                page_header("❌ INTERVAL NOT SAVED") +
                f"Error: {str(e)}\n"
                f"Contact @{config.ADMIN_USERNAME} for support.",
                parse_mode=ParseMode.HTML,
                reply_markup=kb([[InlineKeyboardButton("← Dashboard", callback_data="menu_main")]])
            )
            await send_dm_log(uid, f"<b>❌ Failed to set broadcast interval:</b> {str(e)}")
    elif state == "telethon_wait_phone":
        if not validate_phone_number(text):
            await reply_with_input_prompt(
                m,
                uid,
                page_header("❌ INVALID PHONE NUMBER")
                + "Use international format, for example <code>+1234567890</code>.",
                parse_mode=ParseMode.HTML,
                reply_markup=kb([
                    [danger_inline_button("✖ Cancel", callback_data="menu_main")]
                ]),
            )
            return
        status_msg = await m.answer(
            f"⏳ Hold! We're trying to OTP...\n\nPhone: {text}\nPlease wait a moment."
        )
        input_prompt_refs[uid] = (status_msg.chat.id, status_msg.message_id)
        tg = None
        try:
            tg = TelegramClient(StringSession(), config.API_ID, config.API_HASH)
            await tg.connect()
            sent_code = await tg.send_code_request(text)
            session_str = tg.session.save()

            temp_dict = {
                "phone": text,
                "session_str": session_str,
                "phone_code_hash": sent_code.phone_code_hash,
                "otp": ""
            }

            temp_json = json.dumps(temp_dict)
            temp_encrypted = cipher_suite.encrypt(temp_json.encode()).decode()
            db.set_temp_data(uid, temp_encrypted)
            db.set_user_state(uid, "telethon_wait_otp")

            base_caption = (
                page_header("🔐 ACCOUNT VERIFICATION")
                +
                f"Code sent to <code>{text}</code> ✅\n"
                "Enter the code using the keypad below.\n"
                "<b>Current:</b> <code>_____</code>\n"
                "<b>Format:</b> <code>12345</code>\n"
                f"<b>Expires in:</b> {config.OTP_EXPIRY // 60} minutes"
            )

            await edit_message_or_reply(
                status_msg,
                base_caption,
                parse_mode=ParseMode.HTML,
                reply_markup=otp_keyboard(),
            )
            await send_dm_log(
                uid,
                '<tg-emoji emoji-id="5258368309199387233">📩</tg-emoji> '
                f"OTP requested for phone number: {text}",
            )
        except PhoneNumberInvalidError:
            await edit_message_or_reply(
                status_msg,
                page_header("❌ INVALID PHONE NUMBER")
                + "Check the number and try again.",
                parse_mode=ParseMode.HTML,
                reply_markup=kb([
                    [danger_inline_button("✖ Cancel", callback_data="menu_main")]
                ]),
            )
        except Exception as e:
            logger.error(f"Failed to send OTP for {uid}: {e}")
            db.set_user_state(uid, "")
            await edit_message_or_reply(
                status_msg,
                page_header("❌ LOGIN CODE NOT SENT")
                + "Telegram could not send a login code. Check the phone number and try again.",
                parse_mode=ParseMode.HTML,
                reply_markup=kb([
                    [danger_inline_button("← Dashboard", callback_data="menu_main")]
                ]),
            )
            await send_dm_log(uid, f"<b>❌ Failed to send OTP for phone:</b> {str(e)}")
        finally:
            if tg is not None:
                await tg.disconnect()
    elif state == "telethon_wait_password":
        temp_encrypted = db.get_temp_data(uid)
        if not temp_encrypted:
            await m.answer(
                page_header("❌ VERIFICATION SESSION EXPIRED")
                + "Restart account verification to continue.",
                parse_mode=ParseMode.HTML,
                reply_markup=kb([
                    [danger_inline_button("← Dashboard", callback_data="menu_main")]
                ]),
            )
            db.set_user_state(uid, "")
            return

        try:
            temp_json = cipher_suite.decrypt(temp_encrypted.encode()).decode()
            temp_dict = json.loads(temp_json)
            phone = temp_dict["phone"]
            session_str = temp_dict["session_str"]
        except (json.JSONDecodeError, Fernet.InvalidToken) as e:
            logger.error(f"Invalid temp data for user {uid} in 2FA: {e}")
            await m.answer(
                page_header("❌ VERIFICATION DATA INVALID")
                + "Restart account verification to continue.",
                parse_mode=ParseMode.HTML,
                reply_markup=kb([
                    [danger_inline_button("← Dashboard", callback_data="menu_main")]
                ]),
            )
            db.set_user_state(uid, "")
            db.set_temp_data(uid, None)
            return

        tg = TelegramClient(StringSession(session_str), config.API_ID, config.API_HASH)
        try:
            await tg.connect()
            await tg.sign_in(password=text)
            session_encrypted = cipher_suite.encrypt(session_str.encode()).decode()
            db.add_user_account(uid, phone, session_encrypted)
            await m.answer(
                account_success_message(phone),
                parse_mode=ParseMode.HTML,
                reply_markup=kb([
                    [danger_inline_button("Back", callback_data="menu_main")]
                ]),
            )
            await send_dm_log(
                uid,
                f'Account added successfully: {phone} '
                '<tg-emoji emoji-id="5195100606250889609">✅</tg-emoji>',
            )
            db.set_user_state(uid, "")
            db.set_temp_data(uid, None)
        except PasswordHashInvalidError:
            await reply_with_input_prompt(
                m,
                uid,
                page_header("❌ INCORRECT PASSWORD")
                + "Check your Telegram cloud password and try again.",
                parse_mode=ParseMode.HTML,
                reply_markup=kb([
                    [danger_inline_button("Cancel", callback_data="menu_main")]
                ]),
            )
        except Exception as e:
            logger.error(f"Failed to sign in with password for {uid}: {e}")
            db.set_user_state(uid, "")
            db.set_temp_data(uid, None)
            await m.answer(
                page_header("❌ ACCOUNT VERIFICATION FAILED")
                +
                f"Error: {str(e)}\n"
                f"Contact @{config.ADMIN_USERNAME} for help.",
                parse_mode=ParseMode.HTML,
                reply_markup=kb([
                    [danger_inline_button("← Dashboard", callback_data="menu_main")]
                ]),
            )
            await send_dm_log(uid, f"<b>╰_╯Account login failed:❌</b> {str(e)}")
        finally:
            await tg.disconnect()

async def main():
    polling_tasks = [
        asyncio.create_task(main_dispatcher.start_polling(main_bot, close_bot_session=False)),
        asyncio.create_task(logger_dispatcher.start_polling(logger_bot, close_bot_session=False)),
    ]
    try:
        await asyncio.gather(*polling_tasks)
    finally:
        for task in polling_tasks:
            task.cancel()
        background_tasks = list(user_tasks.values())
        for task in background_tasks:
            task.cancel()
        await asyncio.gather(
            *polling_tasks,
            *background_tasks,
            return_exceptions=True,
        )
        db.close()
        await main_bot.session.close()
        await logger_bot.session.close()
        logger.info("Bot stopped gracefully")

if __name__ == "__main__":
    asyncio.run(main())
