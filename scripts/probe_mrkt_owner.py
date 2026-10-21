"""Read-only probe: what does Telegram show about the account that owns gifts listed on MRKT?

    PYTHONPATH=. python scripts/probe_mrkt_owner.py [SLUG]   # default: SnoopDogg-356923 (a gift owned by "MRKT")

It uses the service account from .env, makes three read-only MTProto calls (`payments.getUniqueStarGift`,
`payments.getSavedStarGifts` twice) and prints only public facts: who the owner is, how many gifts that
profile shows, and what the newest entries look like. No secrets are printed. The output tells us whether
"watch the MRKT account for newly arrived gifts" is possible at all.
"""

import asyncio
import sys

from telethon.tl import functions, types

from app.config import Settings
from app.domain import SourceError, SourceRateLimited, is_safe_slug
from app.sources.telegram_account import TelegramAccount

DEFAULT_SLUG = "SnoopDogg-356923"


def input_peer(peer, users, chats):
    if isinstance(peer, types.PeerUser) and peer.user_id in users:
        user = users[peer.user_id]
        return types.InputPeerUser(user_id=user.id, access_hash=user.access_hash)
    if isinstance(peer, types.PeerChannel) and peer.channel_id in chats:
        chat = chats[peer.channel_id]
        return types.InputPeerChannel(channel_id=chat.id, access_hash=getattr(chat, "access_hash", 0))
    return None


def describe(entry) -> str:
    gift = entry.gift
    if not isinstance(gift, types.StarGiftUnique):
        return f"{type(gift).__name__} (not an upgraded gift)"
    price = [f"{type(a).__name__}:{getattr(a, 'amount', '?')}" for a in (gift.resell_amount or [])]
    return f"{gift.title} #{gift.num}  slug={gift.slug}  saved_on={entry.date:%Y-%m-%d %H:%M}  resell={price or 'none'}"


async def main() -> None:
    slug = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SLUG
    if not is_safe_slug(slug):
        sys.exit("Bad slug")
    settings = Settings()
    if not (settings.tg_api_id and settings.tg_api_hash and settings.tg_session_string):
        sys.exit("TG_API_ID / TG_API_HASH / TG_SESSION_STRING are not set: run scripts/termux_login.sh first.")
    account = TelegramAccount(
        settings.tg_api_id, settings.tg_api_hash.get_secret_value(), settings.tg_session_string.get_secret_value()
    )
    await account.start()
    try:
        found = await account.call(functions.payments.GetUniqueStarGiftRequest(slug=slug))
        users = {u.id: u for u in found.users if isinstance(u, types.User)}
        chats = {c.id: c for c in found.chats}
        gift = found.gift
        peer = getattr(gift, "owner_id", None)
        print(f"gift: {gift.title} #{gift.num}")
        print(f"owner peer: {type(peer).__name__} {getattr(peer, 'user_id', None) or getattr(peer, 'channel_id', None)}")
        print(f"owner name: {getattr(gift, 'owner_name', None)!r}; owner address: {getattr(gift, 'owner_address', None) is not None}")
        owner = users.get(getattr(peer, "user_id", None)) or chats.get(getattr(peer, "channel_id", None))
        if owner is not None:
            print(f"owner entity: {type(owner).__name__} username={getattr(owner, 'username', None)!r} "
                  f"bot={getattr(owner, 'bot', None)} verified={getattr(owner, 'verified', None)}")
        target = input_peer(peer, users, chats)
        if target is None:
            print("Cannot build an input peer for the owner (hidden or address-only owner): nothing more to probe.")
            return

        for label, kwargs in (("newest first (default)", {}), ("sorted by value", {"sort_by_value": True})):
            page = await account.call(
                functions.payments.GetSavedStarGiftsRequest(peer=target, offset="", limit=20, **kwargs)
            )
            print(f"\n[{label}] total shown on the profile: {page.count}; next page: {bool(page.next_offset)}")
            for entry in page.gifts[:8]:
                print("  ", describe(entry))
    except (SourceError, SourceRateLimited) as e:
        print(f"Telegram refused: {e}")
    finally:
        await account.stop()


if __name__ == "__main__":
    asyncio.run(main())
