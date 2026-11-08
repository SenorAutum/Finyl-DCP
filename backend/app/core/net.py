"""Who is calling: the client IP address behind trusted proxies (WP7, review 7.7).

Every proxy in front of the app (nginx on a VM, the AWS load balancer) APPENDS
the address it saw to ``X-Forwarded-For``. Anything to the left of those
entries was written by the caller and can be anything, so the old code, which
took the FIRST entry, let a caller choose their own IP: dodging the login rate
limit, polluting the audit trail, and passing the Safaricom callback allowlist.

``TRUSTED_PROXY_HOPS`` says how many proxies sit in front of the app (default 1:
nginx, or the AWS load balancer). The client is the entry that many places from
the RIGHT. Set it to 0 if the app is ever exposed directly (then the header is
ignored) and 2 behind CloudFront + load balancer.
"""
from __future__ import annotations

from app.core.config import settings


def client_ip(request) -> str | None:
    if request is None:
        return None
    client = getattr(request, "client", None)
    peer = getattr(client, "host", None) if client else None
    hops = int(getattr(settings, "TRUSTED_PROXY_HOPS", 1) or 0)
    if hops <= 0:
        return peer
    headers = request.headers
    xff = headers.get("x-forwarded-for")
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            return parts[-hops] if len(parts) >= hops else parts[0]
    xri = headers.get("x-real-ip")          # set by nginx from the real peer
    if xri and xri.strip():
        return xri.strip()
    return peer
