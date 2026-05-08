"""Shared slowapi Limiter instance.

Kept in its own module so routes.py and app.py can both import it without
creating a circular import through the FastAPI app factory.

Note on reverse proxies: `get_remote_address` reads `request.client.host`,
which is the direct peer. When the app sits behind ngrok or any reverse
proxy, all external clients will share a single bucket. This is acceptable
for the current deployment (single-worker Docker on a LAN server). If we
later promote slowapi to protect a public deployment, swap `key_func` for
one that respects `X-Forwarded-For` from a trusted proxy header.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
