"""Response headers: the local UI is not drawn in another site's frame; files keep their type.

Any website can try to put http://127.0.0.1:<port>/ in a frame. Inside the frame the page is its own
origin, so its requests pass the origin check; an overlay on top could then lead a click onto
"archive" or "send". frame-ancestors (and X-Frame-Options for older browsers) makes the browser
refuse to draw the page in a frame at all. The hosted review deployment may be shown inside a
reviewer's portal, so the frame rule applies to the local server only; the other headers apply
everywhere.
"""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from thoth.adapters.http.hosted_review import hosted_mode

_ALWAYS = (("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer"))
_LOCAL_ONLY = (("Content-Security-Policy", "frame-ancestors 'none'"), ("X-Frame-Options", "DENY"))


class SecurityHeaders:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        extra = _ALWAYS if hosted_mode() else _ALWAYS + _LOCAL_ONLY

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in extra:
                    headers.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_with_headers)
