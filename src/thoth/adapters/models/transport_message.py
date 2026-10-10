"""Conservative exception excerpts: retain transport vocabulary, never arbitrary payload text."""

import re

_WORDS = frozenset(
    [
        "peer",
        "closed",
        "connection",
        "without",
        "sending",
        "complete",
        "message",
        "body",
        "incomplete",
        "chunked",
        "read",
        "server",
        "disconnected",
        "reset",
        "by",
        "remote",
        "protocol",
        "error",
        "local",
        "network",
        "transport",
        "stream",
        "unexpected",
        "end",
        "of",
        "file",
        "eof",
        "received",
        "receiving",
        "send",
        "sending",
        "response",
        "request",
        "failed",
        "failure",
        "timed",
        "out",
        "timeout",
        "connect",
        "connecting",
        "broken",
        "pipe",
        "aborted",
        "refused",
        "terminated",
        "invalid",
        "malformed",
        "chunk",
        "header",
        "headers",
        "encoding",
        "decoding",
        "compression",
        "compressed",
        "data",
        "decompressing",
        "content",
        "length",
        "expected",
        "bytes",
        "byte",
        "read",
        "write",
        "socket",
        "ssl",
        "tls",
        "certificate",
        "verify",
        "verification",
        "handshake",
        "alert",
        "reason",
        "the",
        "a",
        "an",
        "is",
        "was",
        "has",
        "been",
        "not",
        "no",
        "and",
        "or",
        "in",
        "on",
        "at",
        "from",
        "to",
        "for",
        "with",
        "during",
        "while",
        "before",
        "after",
        "exceeded",
        "maximum",
        "allowed",
        "connectionreseterror",
        "remoteprotocolerror",
        "readerror",
        "writeerror",
        "connecterror",
        "readtimeout",
        "http",
        "https",
        "httpx",
        "httpcore",
        "h11",
    ]
)
_UNSAFE_SUFFIX = re.compile(
    r"(?i)\b(?:authorization|proxy-authorization|cookie|set-cookie|bearer|basic|"
    r"[\w-]*token[\w-]*|api[-_ ]?key|password|secret|credentials|headers|payload)\b"
    r"|\b[a-z][a-z0-9+.-]*://|[\"'{}<>]"
)
_TOKEN = re.compile(r"[A-Za-z0-9_./+=-]+|[^\x20-\x7e]+")


def safe_exception_prefix(error: BaseException, sensitive_values: tuple[str, ...]) -> str | None:
    """Keep the first 256 sanitized characters; unknown words/numbers are redacted too.

    Process known credentials before taking a prefix, so truncation never exposes part of
    a credential. Stop at structured/quoted data or authentication context. The remaining
    allowlist deliberately loses unfamiliar prose instead of guessing whether it is secret.
    """
    try:
        message = str(error)
    except Exception:
        return None
    for secret in sorted(set(sensitive_values), key=len, reverse=True):
        if secret:
            message = message.replace(secret, "[REDACTED]")
    if len(message) > 4096:
        # Drop the boundary token completely rather than accepting its truncated prefix.
        message = message[:4096].rpartition(" ")[0] + " [REDACTED]"
    unsafe = _UNSAFE_SUFFIX.search(message)
    if unsafe is not None:
        message = message[: unsafe.start()] + "[REDACTED]"
    message = _TOKEN.sub(
        lambda match: match.group() if match.group().lower() in _WORDS else "[REDACTED]",
        message,
    )
    message = re.sub(
        r"(?:\[\[REDACTED\]\]|\[REDACTED\])(?:\s*\[REDACTED\])*", "[REDACTED]", message
    )
    return message.strip()[:256] or None
