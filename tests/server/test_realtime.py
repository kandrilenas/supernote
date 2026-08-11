"""Tests for legacy EIO3/SIO2 real-time socket.io endpoint."""

import json

import jwt
import pytest
from aiohttp.test_utils import TestClient
from pytest_aiohttp import AiohttpClient

from supernote.server.app import create_app
from supernote.server.config import ServerConfig
from supernote.server.services.user import JWT_ALGORITHM


@pytest.fixture
async def realtime_client(
    aiohttp_client: AiohttpClient,
    server_config: ServerConfig,
    create_test_user: None,
) -> TestClient:
    """Fixture providing a running TestClient with realtime route enabled."""
    app = create_app(server_config)
    return await aiohttp_client(app)


@pytest.mark.asyncio
async def test_realtime_non_websocket_rejected(realtime_client: TestClient) -> None:
    """HTTP requests without Upgrade: websocket header return 400."""
    resp = await realtime_client.get("/socket.io/?EIO=3&transport=polling")
    assert resp.status == 400
    data = await resp.json()
    assert data["error"] == "websocket transport required"


@pytest.mark.asyncio
async def test_realtime_missing_token_rejected(realtime_client: TestClient) -> None:
    """WebSocket requests without token query param return 401."""
    resp = await realtime_client.get(
        "/socket.io/?EIO=3&transport=websocket",
        headers={"Upgrade": "websocket"},
    )
    assert resp.status == 401
    data = await resp.json()
    assert data["error"] == "token required"


@pytest.mark.asyncio
async def test_realtime_invalid_token_rejected(realtime_client: TestClient) -> None:
    """WebSocket requests with an unauthenticated JWT token return 401."""
    resp = await realtime_client.get(
        "/socket.io/?EIO=3&transport=websocket&token=invalid-jwt-token",
        headers={"Upgrade": "websocket"},
    )
    assert resp.status == 401
    data = await resp.json()
    assert data["error"] == "invalid token"


@pytest.mark.asyncio
async def test_realtime_websocket_handshake_and_ping_pong(
    realtime_client: TestClient,
    server_config: ServerConfig,
) -> None:
    """Test full EIO3/SIO2 WebSocket handshake, OPEN frame, CONNECT frame, and PING/PONG keepalive."""
    secret = server_config.auth.secret_key
    token = jwt.encode({"sub": "test@example.com"}, secret, algorithm=JWT_ALGORITHM)

    # Establish session in coordination service
    session_val = "test@example.com|SN078C10001550"
    await realtime_client.app["coordination_service"].set_value(
        f"session:{token}", session_val, ttl=3600
    )

    url = f"/socket.io/?EIO=3&transport=websocket&type=SN078C10001550&token={token}"

    ws = await realtime_client.ws_connect(url)

    # 1. Receive Engine.IO OPEN packet ("0{...}")
    msg1 = await ws.receive_str()
    assert msg1.startswith("0")
    payload1 = json.loads(msg1[1:])
    assert "sid" in payload1
    assert payload1["pingInterval"] == 25000
    assert payload1["pingTimeout"] == 60000

    # 2. Receive Socket.IO v2 CONNECT packet ("40")
    msg2 = await ws.receive_str()
    assert msg2 == "40"

    # 3. Send Engine.IO PING ("2probe") and receive Engine.IO PONG ("3probe")
    await ws.send_str("2probe")
    msg3 = await ws.receive_str()
    assert msg3 == "3probe"

    # 4. Client opens sub-namespace "40/chat," -> server echoes "40/chat,"
    await ws.send_str("40/chat,")
    msg4 = await ws.receive_str()
    assert msg4 == "40/chat,"

    await ws.close()
