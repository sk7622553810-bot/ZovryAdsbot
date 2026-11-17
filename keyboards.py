import html
import re
from typing import Any

from aiogram.types import (
    InlineKeyboardButton as AiogramInlineKeyboardButton,
    InlineKeyboardMarkup,
)
from urllib.parse import urlparse

import config

QUICK_DELAY_MINUTES = (1, 3, 7, 9, 10, 11, 13, 15, 20)
QUICK_DELAY_SECONDS = frozenset(minutes * 60 for minutes in QUICK_DELAY_MINUTES)


def primary_inline_button(text: str, **kwargs: Any) -> AiogramInlineKeyboardButton:
    kwargs["style"] = "primary"
    return AiogramInlineKeyboardButton(text=text, **kwargs)


def danger_inline_button(text: str, **kwargs: Any) -> AiogramInlineKeyboardButton:
    kwargs["style"] = "danger"
    return AiogramInlineKeyboardButton(text=text, **kwargs)


def success_inline_button(text: str, **kwargs: Any) -> AiogramInlineKeyboardButton:
    kwargs["style"] = "success"
    return AiogramInlineKeyboardButton(text=text, **kwargs)


def _is_danger_navigation_text(label: str) -> bool:
    text = label.casefold().strip()
    words = set(re.findall(r"[a-z]+", text))
    return (
        bool(words & {"back", "cancel", "dashboard"})
        or "stop joining" in text
        or text.startswith("←")
    )


def kb(buttons: list[list[Any]]) -> InlineKeyboardMarkup:
    if not isinstance(buttons, list) or not all(isinstance(row, list) for row in buttons):
        raise ValueError("Rows must be a list of lists")

    keyboard = []
    for row in buttons:
        row_buttons = []
        for button in row:
            if isinstance(button, dict):
                button_factory = (
                    danger_inline_button
                    if _is_danger_navigation_text(button["text"])
                    else primary_inline_button
                )
                if "url" in button:
                    button = button_factory(button["text"], url=button["url"])
                else:
                    button = button_factory(
                        button["text"],
                        callback_data=button["callback_data"],
                    )
            elif isinstance(button, AiogramInlineKeyboardButton):
                style = (
                    "danger"
                    if _is_danger_navigation_text(button.text)
                    else button.style or "primary"
                )
                button = button.model_copy(
                    update={"style": style}
                )
            else:
                raise TypeError("Inline keyboard rows must contain inline buttons or button dictionaries")
            row_buttons.append(button)
        keyboard.append(row_buttons)
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def configured_chat_username(chat_id: Any, url: str) -> str:
    identifier = str(chat_id).strip()
    if identifier.startswith("@"):
        return identifier
    if identifier and not identifier.lstrip("-").isdigit():
        return f"@{identifier}"

    parsed_url = urlparse(url)
    if (parsed_url.hostname or "").lower() not in {"t.me", "www.t.me"}:
        raise ValueError("Forced-subscription links must use a public Telegram username")

    path_parts = [part for part in parsed_url.path.strip("/").split("/") if part]
    if path_parts and path_parts[0] == "s":
        path_parts = path_parts[1:]
    username = path_parts[0] if path_parts else ""
    if not username or username.startswith("+") or username == "joinchat":
        raise ValueError("Forced-subscription links must use a public Telegram username")
    return f"@{username.lstrip('@')}"


def force_join_inline_keyboard(
    channel_chat_id: Any,
    channel_url: str,
    group_chat_id: Any,
    group_url: str,
) -> InlineKeyboardMarkup:
    channel_username = configured_chat_username(channel_chat_id, channel_url)
    group_username = configured_chat_username(group_chat_id, group_url)
    return kb([
        [danger_inline_button(f"Join {channel_username}", url=channel_url)],
        [danger_inline_button(f"Join {group_username}", url=group_url)],
    ])


def forced_subscription_message(channel_url: str, group_url: str) -> str:
    return (
        "⚠️ Access denied - You need to join our "
        f'<a href="{html.escape(channel_url, quote=True)}">channel</a> and '
        f'<a href="{html.escape(group_url, quote=True)}">group 1</a> to use this bot.'
    )


def welcome_message() -> str:
    return (
        "Welcome to ZovryAds! New era of Advertisement on telegram.\n\n"
        "<blockquote expandable>"
        "Unlike other Adsbot services we provide you full Automation so you can turn your "
        "multiple alt accounts into Ad-bots and Advertise with full control and proper "
        "visibility. ZovryAds also have free tier for you try our service before "
        "purchasing premium plan."
        "</blockquote>\n\n"
        'Read our <a href="https://telegram.org/faq">Terms of Service</a> '
        "Before going further in the bot.\n\n"
        "By continuing in this bot you agree to our "
        '<a href="https://telegram.org/faq">Terms of Service</a>. '
        "Go to dashboard to start your automated advertisement journey with @ZovryAds."
    )


def welcome_keyboard() -> InlineKeyboardMarkup:
    return kb([[primary_inline_button("Dashboard", callback_data="menu_main")]])


def dashboard_caption_text(
    hosted_accounts: int,
    account_limit: str,
    advertisements: int,
    advertisement_limit: int,
    running: bool,
    interval: str,
    current_plan: str,
    developer_username: str,
) -> str:
    return (
        "Powered by @ZovryOrg\n\n"
        f"• Hosted Accounts: {hosted_accounts}/{html.escape(str(account_limit))}\n"
        f"• Advertisements: {advertisements}/{advertisement_limit}\n"
        f"• Advertisement status: {'running 🚀' if running else 'stopped 🛑'}\n"
        f"• Interval: {html.escape(interval.lower())}\n"
        f"• Current plan: {html.escape(str(current_plan)).lower()}\n\n"
        "<blockquote>Developed and managed by: "
        f"@{html.escape(developer_username.lstrip('@'))}</blockquote>"
    )


def dashboard_keyboard(
    running: bool,
    active_accounts_count: int = 0,
) -> InlineKeyboardMarkup:
    action_button = (
        danger_inline_button("Stop Ads ⏹", callback_data="stop_broadcast")
        if running
        else success_inline_button("Run Ads ▷", callback_data="start_broadcast")
    )
    account_button = (
        primary_inline_button("Manage Account", callback_data="view_accounts")
        if active_accounts_count > 0
        else primary_inline_button("Add Account", callback_data="host_account")
    )
    return kb([
        [account_button],
        [
            primary_inline_button("Set Ads", callback_data="set_msg"),
            primary_inline_button("Interval & Delays", callback_data="set_delay"),
        ],
        [action_button],
        [
            primary_inline_button("Auto reply", callback_data="auto_reply"),
            primary_inline_button("Auto join", callback_data="auto_join"),
        ],
        [primary_inline_button("Go Premium", url=config.SUPPORT_GROUP_URL)],
        [
            primary_inline_button("About bot", url=config.ABOUT_BOT_URL),
            primary_inline_button("Powered by", url=config.POWERED_BY_URL),
        ],
    ])


def hosted_accounts_keyboard(
    accounts: list[dict[str, Any]],
    can_add_account: bool,
) -> InlineKeyboardMarkup:
    buttons = []
    for account in accounts:
        status = "Active" if account.get("is_active", True) else "Inactive"
        phone_number = str(account["phone_number"])
        account_id = str(account["_id"])
        buttons.append([
            primary_inline_button(
                f"{phone_number} ({status})",
                callback_data=f"view_acc_{account_id}",
            ),
            danger_inline_button(
                "Delete",
                callback_data=f"delete_acc_{account_id}",
            ),
        ])

    if can_add_account:
        buttons.append([
            primary_inline_button("Add Account", callback_data="host_account")
        ])
    buttons.append([
        danger_inline_button("Back", callback_data="menu_main")
    ])
    return kb(buttons)


def quick_delay_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    for minutes in QUICK_DELAY_MINUTES:
        text = f"{minutes} Minute" if minutes == 1 else f"{minutes} Minutes"
        callback_data = f"quick_delay_{minutes * 60}"
        if minutes == 5:
            button = danger_inline_button(text, callback_data=callback_data)
        elif minutes == 20:
            button = success_inline_button(text, callback_data=callback_data)
        else:
            button = primary_inline_button(text, callback_data=callback_data)
        buttons.append(button)

    rows = [buttons[index:index + 3] for index in range(0, len(buttons), 3)]
    rows.append([danger_inline_button("Back 🔙", callback_data="menu_main")])
    return kb(rows)


def otp_keyboard() -> InlineKeyboardMarkup:
    return kb([
        [
            primary_inline_button("1", callback_data="otp_1"),
            primary_inline_button("2", callback_data="otp_2"),
            primary_inline_button("3", callback_data="otp_3"),
        ],
        [
            primary_inline_button("4", callback_data="otp_4"),
            primary_inline_button("5", callback_data="otp_5"),
            primary_inline_button("6", callback_data="otp_6"),
        ],
        [
            primary_inline_button("7", callback_data="otp_7"),
            primary_inline_button("8", callback_data="otp_8"),
            primary_inline_button("9", callback_data="otp_9"),
        ],
        [
            primary_inline_button("⌫", callback_data="otp_back"),
            primary_inline_button("0", callback_data="otp_0"),
            danger_inline_button("Cancel", callback_data="otp_cancel"),
        ],
        [
            primary_inline_button(
                "Show Code ↗",
                url="tg://openmessage?user_id=777000",
            ),
        ],
    ])


def human_behaviour_keyboard(active: bool) -> InlineKeyboardMarkup:
    toggle_label = (
        "Deactivate Human behaviour ◻"
        if active
        else "Activate Human behaviour 🛟"
    )
    return kb([
        [primary_inline_button(toggle_label, callback_data="toggle_human_behaviour")],
        [danger_inline_button("Back", callback_data="menu_main")],
    ])


def auto_join_text() -> str:
    return (
        "<b>Auto Join</b>\n\n"
        "<blockquote><b>Instructions for Auto join:</b>\n\n"
        "<b>Join groups:</b> In join groups you can send Group links or usernames and all of your "
        "hosted acc will join those groups. Maximum group links limit is 500. It takes too long "
        "to join all the groups because of rate limits.\n\n"
        "<b>Join Add list:</b> In join add list you can send links of addlists also called chat "
        "folders and bot will join all the chats in the folder with your hosted acc and then bot "
        "will delete all the folders but groups will still be joined in the account. Maximum "
        "addlist links limit is 6. It is way faster than join groups.\n\n"
        "<b>Note:</b>\n"
        "Telegram has a limit of 500 groups for accounts, if accounts reach the limit they won't join."
        "</blockquote>"
    )


def auto_join_keyboard() -> InlineKeyboardMarkup:
    return kb([
        [
            primary_inline_button("Join groups", callback_data="joiner_select_group"),
            primary_inline_button("Join Add list", callback_data="joiner_select_folder"),
        ],
        [danger_inline_button("Back", callback_data="menu_main")],
    ])


def joiner_prompt_text(mode: str) -> str:
    if mode == "group":
        return (
            "Send links of all the Groups you want your adbots to join at once.\n\n"
            "<blockquote>You can send group usernames, links, both in message or in .txt file</blockquote>\n\n"
            "⚠️ Maximum link limit: 500"
        )
    return (
        "Send the add-list links you want your adbots to join at once.\n\n"
        "<blockquote>You can send t.me/addlist/... links</blockquote>\n\n"
        "⚠️ Maximum add-list limit: 6"
    )


def joiner_prompt_keyboard() -> InlineKeyboardMarkup:
    return kb([[danger_inline_button("Back", callback_data="auto_join")]])


def joiner_status_keyboard() -> InlineKeyboardMarkup:
    return kb([
        [danger_inline_button("Stop joining", callback_data="cancel_joiner")],
        [danger_inline_button("Back", callback_data="auto_join")],
    ])


def joiner_summary_text(
    total_completed: int,
    total_links: int,
    successful_count: int,
    failed_count: int,
    requests_count: int,
) -> str:
    success_rate = (
        (successful_count + requests_count) / total_completed * 100
        if total_completed
        else 0
    )
    progress = int(success_rate // 10)
    progress_bar = "▓" * progress + "░" * (10 - progress)
    return (
        "Auto joining has been completed! ✅\n\n"
        f"Success Rate: {progress_bar} {success_rate:.1f}% 💹\n\n"
        f"Completed: {total_completed}/{total_links}\n"
        f"Successful: {successful_count}\n"
        f"Failed: {failed_count}\n"
        f"Sent join requests: {requests_count}"
    )


def joiner_summary_keyboard() -> InlineKeyboardMarkup:
    return kb([[primary_inline_button("OK", callback_data="delete_joiner_summary")]])
