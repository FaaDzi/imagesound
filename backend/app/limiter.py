from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def client_ip(request: Request) -> str:
    """Rate-limit key: the real visitor's IP, not the proxy's.

    With `run.py --tunnel`, every request reaches the backend via cloudflared
    -> Vite's /api proxy, so the socket peer is always 127.0.0.1. Keying on
    that would make each limit global: one stranger hammering /auth/login
    would lock the owner out too. Cloudflare sets CF-Connecting-IP to the
    visitor's address (overwriting any client-supplied value), so it is
    trusted -- but only when the peer is loopback, since the backend itself
    only listens locally and a non-loopback peer never came through the tunnel.
    """
    peer = get_remote_address(request)
    if peer in _LOOPBACK:
        cf_ip = request.headers.get("cf-connecting-ip")
        if cf_ip:
            return cf_ip.strip()
    return peer


limiter = Limiter(key_func=client_ip)
