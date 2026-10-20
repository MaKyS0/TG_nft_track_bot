"""Validation of Telegram Mini App ``initData`` (https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app).

The Mini App sends the raw ``Telegram.WebApp.initData`` string; we recompute its HMAC with a key
derived from the bot token. Only the bot owner (and Telegram) can produce a valid signature, so a
valid ``initData`` proves who the user is, with no passwords or separate login.
"""

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl

CLOCK_SKEW = 60  # tolerate a slightly fast clock on the sender's side


class InitDataError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class WebAppUser:
    id: int
    first_name: str | None
    username: str | None


def validate_init_data(
    init_data: str, bot_token: str, max_age: int = 86400, now: float | None = None
) -> WebAppUser:
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError as e:
        raise InitDataError("malformed initData") from e

    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise InitDataError("hash is missing")

    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        raise InitDataError("bad signature")

    try:
        auth_date = int(pairs["auth_date"])
    except (KeyError, ValueError) as e:
        raise InitDataError("auth_date is missing") from e
    current = time.time() if now is None else now
    if auth_date > current + CLOCK_SKEW or current - auth_date > max_age:
        raise InitDataError("initData expired")

    try:
        user = json.loads(pairs["user"])
        user_id = int(user["id"])
    except (KeyError, ValueError, TypeError) as e:
        raise InitDataError("user is missing") from e
    return WebAppUser(id=user_id, first_name=user.get("first_name"), username=user.get("username"))
