"""Lightweight CSRF protection for admin dashboard POST requests.

Validates the ``Origin`` or ``Referer`` header matches the expected host on
unsafe HTTP methods (POST, PUT, DELETE, PATCH) targeting ``/admin*`` paths.
This prevents cross-site form submissions without changing SQLAdmin internals.

SQLAdmin uses Starlette's SessionMiddleware for auth. CSRF protection is
added here as a separate middleware layer that runs before the route
handlers, so no SQLAdmin internals need to be touched.

(Item 77, v0.25.0)
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# HTTP methods that mutate server state — CSRF attacks only apply to these.
_UNSAFE_METHODS = frozenset({"POST", "PUT", "DELETE", "PATCH"})


class CSRFMiddleware(BaseHTTPMiddleware):
    """Validate Origin/Referer header on unsafe admin requests.

    For every unsafe-method request whose path starts with ``/admin``,
    checks that at least one of ``Origin`` or ``Referer`` contains the
    value of the ``Host`` header.  Browsers that enforce SameSite=Strict
    cookies already block cross-site submissions, but this layer provides
    defense-in-depth for older browsers and tools that set custom headers.

    Non-admin paths and safe methods (GET, HEAD, OPTIONS, TRACE) are
    passed through without inspection.

    Returns HTTP 403 with a plain-text error body when validation fails.
    """

    async def dispatch(self, request: Request, call_next):
        """Check CSRF headers for unsafe admin requests."""
        if (
            request.method in _UNSAFE_METHODS
            and request.url.path.startswith("/admin")
        ):
            origin = request.headers.get("origin", "")
            referer = request.headers.get("referer", "")
            host = request.headers.get("host", "")

            # Accept if either Origin or Referer contains the Host value.
            # Using ``in`` rather than exact equality because Referer includes
            # the full path (e.g. "http://localhost:8000/admin/login") and
            # Origin may include the scheme (e.g. "http://localhost:8000").
            origin_ok = bool(origin and host and host in origin)
            referer_ok = bool(referer and host and host in referer)

            if not (origin_ok or referer_ok):
                return Response(
                    content="CSRF validation failed",
                    status_code=403,
                    media_type="text/plain",
                )

        return await call_next(request)
