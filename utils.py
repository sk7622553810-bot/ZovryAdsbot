import re
import random
import string
from datetime import datetime, timedelta
from typing import List, Dict, Any
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup
from keyboards import kb as build_inline_keyboard
import config


def is_start_command(text: str | None) -> bool:
    if not text or not text.lstrip():
        return False
    command = text.lstrip().split(maxsplit=1)[0].split("@", 1)[0]
    return command.casefold() == "/start"


async def edit_message_or_reply(message: Any, text: str, **kwargs: Any) -> Any:
    is_media_message = (
        getattr(message, "content_type", None) == "photo"
        or getattr(message, "caption", None) is not None
        or any(
            getattr(message, attribute, None)
            for attribute in ("photo", "video", "animation", "audio", "document", "voice")
        )
    )
    try:
        if is_media_message:
            return await message.edit_caption(caption=text, **kwargs)
        return await message.edit_text(text=text, **kwargs)
    except TelegramBadRequest as error:
        if "message is not modified" in error.message.lower():
            return message
        raise


def validate_phone_number(phone: str) -> bool:
    """Validate phone number format"""
    cleaned = re.sub(r'[^\d+]', '', phone)
    pattern = r'^\+\d{10,15}$'
    return bool(re.match(pattern, cleaned))

def generate_progress_bar(completed: int, total: int, length: int = 10) -> str:
    """Generate visual progress bar"""
    if total == 0:
        return "▓" * length + " 0%"
    
    percentage = (completed / total) * 100
    filled = int((completed / total) * length)
    bar = "▓" * filled + "░" * (length - filled)
    
    return f"{bar} {percentage:.1f}%"

def format_duration(td: timedelta) -> str:
    """Format timedelta to human readable string"""
    total_seconds = int(td.total_seconds())
    
    if total_seconds < 60:
        return f"{total_seconds}s"
    elif total_seconds < 3600:
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        return f"{minutes}m {seconds}s"
    else:
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        return f"{hours}h {minutes}m"

def validate_delay(delay_str: str) -> tuple[bool, int]:
    """Validate and return delay value"""
    try:
        delay = int(delay_str)
        if delay < 10 or delay > 600:
            return False, 0
        return True, delay
    except ValueError:
        return False, 0

def calculate_success_rate(sent: int, failed: int) -> float:
    """Calculate success rate percentage"""
    total = sent + failed
    if total == 0:
        return 0.0
    return (sent / total) * 100

def format_broadcast_summary(sent: int, failed: int, duration: timedelta) -> str:
    """Format broadcast completion summary"""
    total = sent + failed
    success_rate = (sent / total * 100) if total > 0 else 0
    
    return (
        f"📊 <blockquote><b>BROADCAST SUMMARY</b></blockquote>\n\n"
        f"<blockquote>✅ <b>Sent:</b> {sent:,}</blockquote>\n"
        f"<blockquote>❌ <b>Failed:</b> {failed:,}</blockquote>\n"
        f"<blockquote>📈 <b>Success Rate:</b> {success_rate:.1f}%</blockquote>\n"
        f"<blockquote>⏰ <b>Duration:</b> {format_duration(duration)}</blockquote>\n"
        f"<blockquote>🎯 <b>Performance:</b> {generate_progress_bar(sent, total)}</blockquote>"
    )

def create_analytics_summary(analytics: Dict) -> str:
    """Create formatted analytics summary"""
    total_sent = analytics.get('total_sent', 0)
    total_failed = analytics.get('total_failed', 0)
    success_rate = calculate_success_rate(total_sent, total_failed)
    
    return (
        f"📊 <blockquote><b>PERFORMANCE ANALYTICS</b></blockquote>\n\n"
        f"<blockquote>📈 <b>Broadcasts:</b> {analytics.get('total_broadcasts', 0):,}</blockquote>\n"
        f"<blockquote>✅ <b>Sent:</b> {total_sent:,}</blockquote>\n"
        f"<blockquote>❌ <b>Failed:</b> {total_failed:,}</blockquote>\n"
        f"<blockquote>🎯 <b>Success Rate:</b> {success_rate:.1f}%</blockquote>\n"
        f"<blockquote>📱 <b>Accounts:</b> {analytics.get('total_accounts', 0)}</blockquote>"
    )

def format_error_message(error_type: str, context: str = "") -> str:
    """Format error messages consistently"""
    base_message = config.ERROR_MESSAGES.get(error_type, "❌ An error occurred")
    if context:
        return f"{base_message}\n\n<blockquote>🔍 <b>Context:</b> {context}</blockquote>"
    return base_message

def format_success_message(success_type: str, context: str = "") -> str:
    """Format success messages consistently"""
    base_message = config.SUCCESS_MESSAGES.get(success_type, "✅ Operation successful")
    if context:
        return f"{base_message}\n\n<blockquote>📋 <b>Details:</b> {context}</blockquote>"
    return base_message

def kb(buttons: List[List[Any]]) -> InlineKeyboardMarkup:
    return build_inline_keyboard(buttons)
