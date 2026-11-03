"""Retell's request signature: `X-Retell-Signature: v=<ms>,d=<hex>`, where the hex is
HMAC-SHA256 over the raw body followed by the timestamp, keyed with the Retell API key
(docs.retellai.com/features/secure-webhook). Custom functions, call webhooks and the inbound
webhook are all signed this way."""

import hashlib
import hmac
import re

MAX_AGE_MS = 5 * 60 * 1000
HEADER = re.compile(r"^v=(\d+),d=([0-9a-f]+)$")


def sign(body: bytes, key: str, now_ms: int) -> str:
    digest = hmac.new(key.encode(), body + str(now_ms).encode(), hashlib.sha256).hexdigest()
    return f"v={now_ms},d={digest}"


def verify(body: bytes, key: str, header: str | None, now_ms: int) -> bool:
    if not key or not header:
        return False
    match = HEADER.match(header.strip())
    if not match:
        return False
    sent_ms = int(match.group(1))
    if abs(now_ms - sent_ms) > MAX_AGE_MS:
        return False
    return hmac.compare_digest(sign(body, key, sent_ms), header.strip())
