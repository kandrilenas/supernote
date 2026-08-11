"""Realtime socket.io endpoint for the Supernote device's app-data channel.

The device opens a realtime channel on every sync with:

    GET /socket.io/?sign=..&random=..&EIO=3&transport=websocket&type=<deviceId>&token=<jwt>

i.e. **Engine.IO protocol v3 / Socket.IO protocol v2**, going straight to
``transport=websocket`` (no polling-first upgrade).

This hand-rolls just enough EIO3/SIO2 to make the device **connect cleanly** and keep the
channel alive.

It is dependency-free on purpose: modern ``python-socketio`` / ``python-engineio``
(v5 / v4) hard-reject ``EIO=3`` with a 400 and have removed the EIO3 encoding paths, so
the library route would need an old (<5 / <4) pin. Hand-rolling over aiohttp's native
``WebSocketResponse`` avoids the dependency.

Requests with ``EIO=4`` (or modern Socket.IO v4 clients) are delegated to the
standard python-socketio server manager if attached.
"""

from __future__ import annotations

import json
import logging
import secrets

from aiohttp import WSMsgType, web

from .routes.decorators import public_route

logger = logging.getLogger(__name__)

# EIO3 handshake timings advertised to the client (milliseconds).
_PING_INTERVAL_MS = 25000
_PING_TIMEOUT_MS = 60000


@public_route  # bypass jwt_auth_middleware; the device carries its token as a query param
async def handle_socketio(request: web.Request) -> web.StreamResponse:
    """Serve the device's Engine.IO-v3 / Socket.IO-v2 realtime channel (connect-only)."""
    # If the client requests Engine.IO v4 or something other than EIO=3, delegate
    # to python-socketio manager if available.
    if request.query.get("EIO") != "3":
        manager = request.app.get("socketio_manager")
        if manager and manager.sio:
            return await manager.sio.handle_request(request)

    if request.headers.get("Upgrade", "").lower() != "websocket":
        # The device only ever uses transport=websocket; nothing else to serve.
        return web.json_response({"error": "websocket transport required"}, status=400)

    # The device authenticates this channel with its JWT as a query param (not the
    # x-access-token header the REST middleware reads), so verify it here and reject
    # unauthenticated connections.
    token = request.query.get("token")
    if not token:
        return web.json_response({"error": "token required"}, status=401)
    try:
        session = await request.app["user_service"].verify_token(token)
    except Exception:
        session = None
    if not session:
        return web.json_response({"error": "invalid token"}, status=401)

    ws = web.WebSocketResponse(protocols=("websocket",), heartbeat=None)
    await ws.prepare(request)

    sid = secrets.token_hex(12)
    device = request.query.get("type")
    logger.info("socket.io EIO3 channel open: device=%s sid=%s", device, sid)

    # 1) Engine.IO OPEN handshake.
    await ws.send_str(
        "0"
        + json.dumps(
            {
                "sid": sid,
                "upgrades": [],
                "pingInterval": _PING_INTERVAL_MS,
                "pingTimeout": _PING_TIMEOUT_MS,
            }
        )
    )

    # 2) Socket.IO v2 (EIO3): the SERVER initiates the CONNECT for the default namespace;
    # the client fires its 'connect' event on receipt and only then proceeds.
    connected_namespaces = {"/"}
    await ws.send_str("40")

    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                if msg.type in (
                    WSMsgType.CLOSE,
                    WSMsgType.CLOSING,
                    WSMsgType.CLOSED,
                ):
                    break
                if msg.type == WSMsgType.ERROR:
                    logger.warning("socket.io ws error sid=%s: %s", sid, ws.exception())
                    break
                continue

            data = msg.data
            eio_type = data[:1]
            if eio_type == "2":  # engineio PING -> reply PONG (keepalive)
                await ws.send_str("3" + data[1:])
            elif eio_type == "4":  # engineio MESSAGE -> socket.io packet
                await _maybe_reply_connect(ws, data[1:], connected_namespaces)
    finally:
        logger.debug("socket.io channel closed: sid=%s", sid)
    return ws


async def _maybe_reply_connect(
    ws: web.WebSocketResponse, sio_payload: str, connected_namespaces: set[str]
) -> None:
    """Echo a namespace CONNECT ("40[/nsp,]") when the client opens a new namespace."""
    if sio_payload[:1] != "0":
        return
    rest = sio_payload[1:]
    namespace = "/"
    if rest.startswith("/"):
        comma = rest.find(",")
        namespace = rest if comma == -1 else rest[:comma]
    if namespace not in connected_namespaces:
        connected_namespaces.add(namespace)
        await ws.send_str("40" if namespace == "/" else f"40{namespace},")


def register(app: web.Application) -> None:
    """Register the realtime socket.io route on the main app."""
    app.router.add_get("/socket.io/", handle_socketio)
    app.router.add_get("/socket.io", handle_socketio)
