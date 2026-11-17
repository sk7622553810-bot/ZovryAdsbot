from typing import Any, Optional

from aiogram.enums import ChatMemberStatus


def required_force_join_chat_kind(
    chat: Any,
    channel_chat_id: Any,
    group_chat_id: Any,
) -> Optional[str]:
    for kind, configured_id in (
        ("channel", channel_chat_id),
        ("group", group_chat_id),
    ):
        identifier = str(configured_id).strip()
        try:
            numeric_id = int(identifier)
        except ValueError:
            if (chat.username or "").casefold() == identifier.lstrip("@").casefold():
                return kind
        else:
            if chat.id == numeric_id:
                return kind
    return None


def chat_member_is_joined(member: Any) -> bool:
    return member.status in {
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.CREATOR,
    } or bool(getattr(member, "is_member", False))


def has_seen_post_welcome(user: dict[str, Any]) -> bool:
    if "force_join_welcomed" in user:
        return bool(user["force_join_welcomed"])
    return bool(user.get("welcome_message_id") or user.get("dashboard_unlocked", False))


def dashboard_access_allowed(user: dict[str, Any], message_id: Optional[int]) -> bool:
    return bool(
        user.get("dashboard_unlocked", False)
        or (
            message_id is not None
            and user.get("welcome_message_id") == message_id
        )
    )
