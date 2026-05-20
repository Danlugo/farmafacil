"""Shared slowapi Limiter instance.

Kept in its own module so routes.py and app.py can both import it without
creating a circular import through the FastAPI app factory.

Reverse-proxy support (Item 80, v0.25.0):
``_get_real_ip`` reads the first entry of ``X-Forwarded-For`` when the
header is present.  ngrok and other reverse proxies set this header to the
real client IP.  Falls back to ``request.client.host`` for direct connections.

Note: trusting ``X-Forwarded-For`` blindly is safe here because the only
ingress path is the ngrok tunnel (controlled by us).  If the deployment
topology changes (multiple proxies, public load balancer), the number of
hops to trust should be made configurable.
"""

from fastapi import Request
from slowapi import Limiter


def _get_real_ip(request: Request) -> str:
    """Extract the real client IP from X-Forwarded-For if present.

    ngrok and other reverse proxies set ``X-Forwarded-For`` to the real
    client IP.  Falls back to ``request.client.host`` when the header is
    absent (direct connection, local dev, or unit tests).

    ``X-Forwarded-For`` can be a comma-separated list
    ``"client, proxy1, proxy2"`` — the first entry is the original client.

    Args:
        request: The incoming HTTP request.

    Returns:
        The real client IP address string.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # Take the first (leftmost) address — that is the original client.
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"


limiter = Limiter(key_func=_get_real_ip)
