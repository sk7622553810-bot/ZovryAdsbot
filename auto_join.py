import asyncio
import re
from urllib.parse import urlsplit

from telethon import functions, types
from telethon.errors import (
    FloodWaitError,
    InviteRequestSentError,
    UserAlreadyParticipantError,
)


JOINED = "joined"
REQUEST_SENT = "request_sent"
ALREADY_JOINED = "already_joined"
FAILED = "failed"
PENDING = "pending"
MAX_GROUP_JOIN_LINKS = 500
MAX_ADDLIST_LINKS = 6

_GROUP_USERNAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")
_INVITE_HASH = re.compile(r"^[A-Za-z0-9_-]+$")
_FOLDER_SLUG = re.compile(r"^[A-Za-z0-9_-]+$")
_TELEGRAM_LINK = re.compile(r"(?i)(?:https?://)?(?:www\.)?t\.me/\S+")


def _parse_telegram_url(value):
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return None
    if (
        parsed.scheme.lower() != "https"
        or parsed.netloc.lower() not in ("t.me", "www.t.me")
        or parsed.query
        or parsed.fragment
    ):
        return None
    return [part for part in parsed.path.strip("/").split("/") if part]


def parse_group_link(value):
    """Return a normalized group link descriptor, or None for unsupported input."""
    username = value.strip().lstrip("@")
    if _GROUP_USERNAME.fullmatch(username):
        return {
            "kind": "public",
            "target": username,
            "key": ("public", username.lower()),
            "label": f"@{username}",
        }

    parts = _parse_telegram_url(value)
    if not parts:
        return None

    if len(parts) == 1 and _GROUP_USERNAME.fullmatch(parts[0]):
        username = parts[0]
        return {
            "kind": "public",
            "target": username,
            "key": ("public", username.lower()),
            "label": f"@{username}",
        }

    if len(parts) == 1 and parts[0].startswith("+"):
        invite_hash = parts[0][1:]
        if _INVITE_HASH.fullmatch(invite_hash):
            return {
                "kind": "invite",
                "target": invite_hash,
                "key": ("invite", invite_hash),
            }

    if len(parts) == 2 and parts[0].lower() == "joinchat":
        invite_hash = parts[1]
        if _INVITE_HASH.fullmatch(invite_hash):
            return {
                "kind": "invite",
                "target": invite_hash,
                "key": ("invite", invite_hash),
            }

    return None


def parse_folder_link(value):
    """Return an addlist slug, or None for unsupported input."""
    parts = _parse_telegram_url(value)
    if len(parts or []) == 2 and parts[0].lower() == "addlist":
        slug = parts[1]
        if _FOLDER_SLUG.fullmatch(slug):
            return slug
    return None


def parse_joiner_items(text, mode):
    if mode == "group":
        maximum = MAX_GROUP_JOIN_LINKS
    elif mode == "folder":
        maximum = MAX_ADDLIST_LINKS
    else:
        raise ValueError(f"Unsupported join mode: {mode}")

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    items = []
    for line_number, line in enumerate(lines[:maximum], 1):
        parsed = parse_group_link(line) if mode == "group" else parse_folder_link(line)
        items.append({
            "parsed": parsed,
            "label": (
                parsed.get("label", f"Link {line_number}")
                if mode == "group" and parsed
                else f"Add-list {line_number}" if parsed
                else f"Input {line_number}"
            ),
        })
    return items, maximum


def safe_telegram_reason(error, secret=None):
    """Keep actionable Telegram error text without exposing invite hashes."""
    reason = str(error).strip() or type(error).__name__
    if secret:
        reason = re.sub(re.escape(secret), "[Telegram invite]", reason, flags=re.IGNORECASE)
    reason = _TELEGRAM_LINK.sub("[Telegram link]", reason)
    reason = re.sub(r"(?<!\w)\+[A-Za-z0-9_-]{5,}", "[private invite]", reason)
    return reason[:300]


async def telegram_request(client, request, on_flood_wait):
    while True:
        try:
            return await client(request)
        except FloodWaitError as error:
            seconds = int(error.seconds)
            await on_flood_wait(seconds)
            await asyncio.sleep(seconds)


async def run_with_concurrency(items, worker, max_concurrency):
    if max_concurrency < 1:
        raise ValueError("max_concurrency must be at least one")

    semaphore = asyncio.Semaphore(max_concurrency)

    async def guarded_worker(item):
        async with semaphore:
            return await worker(item)

    tasks = [
        asyncio.create_task(guarded_worker(item))
        for item in items
    ]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


async def join_group_link(client, parsed_link, on_flood_wait):
    try:
        if parsed_link["kind"] == "public":
            entity = await client.get_entity(parsed_link["target"])
            if not isinstance(entity, types.Channel):
                return {
                    "status": FAILED,
                    "label": parsed_link["label"],
                    "reason": "The public link does not point to a Telegram group or channel.",
                }
            await telegram_request(
                client,
                functions.channels.JoinChannelRequest(entity),
                on_flood_wait,
            )
        else:
            await telegram_request(
                client,
                functions.messages.ImportChatInviteRequest(parsed_link["target"]),
                on_flood_wait,
            )
        return {"status": JOINED, "label": parsed_link.get("label", "Private invite")}
    except UserAlreadyParticipantError:
        return {"status": ALREADY_JOINED, "label": parsed_link.get("label", "Private invite")}
    except InviteRequestSentError:
        return {"status": REQUEST_SENT, "label": parsed_link.get("label", "Private invite")}
    except Exception as error:
        return {
            "status": FAILED,
            "label": parsed_link.get("label", "Private invite"),
            "reason": safe_telegram_reason(
                error,
                parsed_link["target"] if parsed_link["kind"] == "invite" else None,
            ),
        }


def _peer_id(peer):
    if isinstance(peer, types.PeerChannel):
        return peer.channel_id
    if isinstance(peer, types.PeerChat):
        return peer.chat_id
    return None


def _chat_is_forbidden(chat):
    return isinstance(chat, (types.ChannelForbidden, types.ChatForbidden))


async def process_folder_link(client, slug, folder_number, on_flood_wait):
    """Import every peer from one shared folder and remove the folder afterward."""
    result = {
        "status": FAILED,
        "title": f"Folder {folder_number}",
        "groups_found": 0,
        "groups": [],
        "reason": "",
        "cancelled": False,
        "folder_removed": False,
    }

    try:
        invite = await telegram_request(
            client,
            functions.chatlists.CheckChatlistInviteRequest(slug),
            on_flood_wait,
        )
    except asyncio.CancelledError:
        result.update(status=PENDING, reason="Stopped before the folder could be inspected.", cancelled=True)
        return result
    except Exception as error:
        result["reason"] = safe_telegram_reason(error, slug)
        return result

    if isinstance(invite, types.chatlists.ChatlistInviteAlready):
        selected_peers = list(invite.missing_peers)
        already_peers = list(invite.already_peers)
        filter_id = invite.filter_id
        folder_title = f"Folder {folder_number}"
    elif isinstance(invite, types.chatlists.ChatlistInvite):
        selected_peers = list(invite.peers)
        already_peers = []
        filter_id = None
        title = getattr(invite.title, "text", invite.title)
        folder_title = str(title or f"Folder {folder_number}")
    else:
        result["reason"] = "Telegram returned an unsupported folder invite response."
        return result

    chats = list(getattr(invite, "chats", []))
    result["groups_found"] = len(chats)
    title_by_id = {getattr(chat, "id", None): getattr(chat, "title", None) for chat in chats}
    result["title"] = folder_title
    result["groups"] = [
        {
            "status": ALREADY_JOINED,
            "label": str(title_by_id.get(_peer_id(peer)) or "Group"),
        }
        for peer in already_peers
    ]

    input_peers = []
    peers_to_join = []
    for peer in selected_peers:
        chat = next(
            (item for item in chats if getattr(item, "id", None) == _peer_id(peer)),
            None,
        )
        if chat is not None and _chat_is_forbidden(chat):
            result["groups"].append({
                "status": FAILED,
                "label": str(getattr(chat, "title", None) or "Group"),
                "reason": "Telegram marks this group or channel as inaccessible.",
            })
            continue
        try:
            input_peers.append(await client.get_input_entity(peer))
            peers_to_join.append(peer)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            result["groups"].append({
                "status": FAILED,
                "label": str(title_by_id.get(_peer_id(peer)) or "Group"),
                "reason": safe_telegram_reason(error, slug),
            })

    join_succeeded = not input_peers and bool(already_peers)
    if input_peers:
        try:
            join_updates = await telegram_request(
                client,
                functions.chatlists.JoinChatlistInviteRequest(slug, input_peers),
                on_flood_wait,
            )
            join_succeeded = True
            result["groups"].extend(
                {
                    "status": JOINED,
                    "label": str(title_by_id.get(_peer_id(peer)) or "Group"),
                }
                for peer in peers_to_join
            )
            join_updates_list = list(getattr(join_updates, "updates", []))
            update = getattr(join_updates, "update", None)
            if update is not None:
                join_updates_list.append(update)
            for update in join_updates_list:
                chatlist = getattr(update, "filter", None)
                if isinstance(chatlist, types.DialogFilterChatlist):
                    filter_id = chatlist.id
                    break
        except asyncio.CancelledError:
            result.update(
                status=PENDING,
                reason="Folder processing was stopped before Telegram confirmed the join.",
                cancelled=True,
            )
            return result
        except InviteRequestSentError:
            join_succeeded = True
            result["groups"].extend(
                {
                    "status": REQUEST_SENT,
                    "label": str(title_by_id.get(_peer_id(peer)) or "Group"),
                }
                for peer in peers_to_join
            )
        except Exception as error:
            reason = safe_telegram_reason(error, slug)
            result["groups"].extend(
                {
                    "status": FAILED,
                    "label": str(title_by_id.get(_peer_id(peer)) or "Group"),
                    "reason": reason,
                }
                for peer in peers_to_join
            )
            result["reason"] = reason
            return result
    elif not already_peers:
        result["reason"] = "Telegram did not provide any groups or channels in this folder."
        return result

    if filter_id is None and join_succeeded and isinstance(invite, types.chatlists.ChatlistInvite):
        try:
            filters_response = await telegram_request(
                client,
                functions.messages.GetDialogFiltersRequest(),
                on_flood_wait,
            )
            expected_title = str(getattr(invite.title, "text", invite.title) or "")
            for chatlist in getattr(filters_response, "filters", []):
                if (
                    isinstance(chatlist, types.DialogFilterChatlist)
                    and str(getattr(chatlist.title, "text", chatlist.title)) == expected_title
                ):
                    filter_id = chatlist.id
                    break
        except asyncio.CancelledError:
            result.update(
                status=PENDING,
                reason="The chats were joined, but the folder could not be removed before stopping.",
                cancelled=True,
            )
            return result
        except Exception as error:
            result["reason"] = (
                "The chats were joined, but the folder ID could not be retrieved: "
                + safe_telegram_reason(error, slug)
            )

    if filter_id is not None:
        try:
            await telegram_request(
                client,
                functions.chatlists.LeaveChatlistRequest(
                    types.InputChatlistDialogFilter(filter_id),
                    [],
                ),
                on_flood_wait,
            )
            result["folder_removed"] = True
        except asyncio.CancelledError:
            result.update(status=PENDING, reason="The folder was joined but removal was stopped.", cancelled=True)
            return result
        except Exception as error:
            result["reason"] = (
                "Groups were processed, but Telegram could not remove the folder: "
                + safe_telegram_reason(error, slug)
            )
            result["status"] = FAILED
            return result

    if filter_id is None:
        result["status"] = FAILED
        result["reason"] = (
            "The chats were processed, but Telegram did not return the folder ID needed to remove the add-list."
        )
        return result

    if result["groups"] or result["folder_removed"]:
        result["status"] = "processed"
    else:
        result["reason"] = "No group in this folder could be imported or confirmed as already joined."
    return result


def count_statuses(results):
    return {
        status: sum(1 for result in results if result.get("status") == status)
        for status in (JOINED, REQUEST_SENT, ALREADY_JOINED, FAILED, PENDING)
    }
