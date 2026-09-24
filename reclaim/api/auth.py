"""Who may change the rules, disarm the agent, or reset the batch.

Thirteen POST endpoints used to answer anyone. The sandbox ones are meant to
- a visitor is supposed to hand the agent a record - but the admin ones were
the same door: any browser on the internet could raise the contact cap, flip
the kill switch off, or reset the deployment mid-demo.

The rule is two lines. A configured `ADMIN_TOKEN` is required in the
`X-Admin-Token` header. With no token configured, only the machine running the
server may make an admin write - so `reclaim serve` on a laptop needs no
setup, and a deployment that forgot the variable is locked rather than open.
Render's proxy is never loopback; a forgotten token fails closed.

The token is never in the UI bundle. The dashboard asks the operator for it
once and keeps it in sessionStorage, which is the only place a secret can live
in a public static site.
"""

from fastapi import HTTPException, Request

from reclaim.config import settings

HEADER = "X-Admin-Token"
_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})


def is_locked(request: Request) -> bool:
    """True when this request would need a token to make an admin write."""
    if settings.admin_token:
        return True
    host = request.client.host if request.client else None
    return host not in _LOOPBACK


def require_admin(request: Request) -> None:
    supplied = request.headers.get(HEADER, "")
    if settings.admin_token:
        if not _same(supplied, settings.admin_token):
            raise HTTPException(
                status_code=401,
                detail=f"admin write: {HEADER} header required",
                headers={"WWW-Authenticate": HEADER},
            )
        return
    if is_locked(request):
        raise HTTPException(
            status_code=403,
            detail="admin writes are locked: no ADMIN_TOKEN is configured and "
                   "this request is not from the machine running the server",
        )


def _same(a: str, b: str) -> bool:
    import hmac

    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
