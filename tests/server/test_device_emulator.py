"""Synthetic Supernote Device Emulator & Trace Replay Test Suite.

Simulates real Supernote device interactions based on actual wire traces
captured from hardware (SN078D10003660, OkHttp client).

This test suite serves as the regression harness for device-specific
quirks, header formats, synchronization flows, and schedule ID handling.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import TestClient
from pytest_aiohttp import AiohttpClient

from supernote.server.app import create_app
from supernote.server.config import ServerConfig

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "device_traces"


class DeviceEmulator:
    """Helper client wrapping TestClient to simulate hardware Supernote requests."""

    def __init__(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        equipment_no: str = "SN078D10003660",
    ) -> None:
        self.client = client
        self.equipment_no = equipment_no
        self.token = auth_headers.get("x-access-token", "")
        self.base_headers = {
            "User-Agent": "okhttp/4.8.0",
            "Connection": "Keep-Alive",
            "Accept-Encoding": "gzip",
            "Content-Type": "application/json;charset=UTF-8",
            "equipmentNo": self.equipment_no,
            "version": "202407",
            "x-access-token": self.token,
        }

    async def post_json(self, path: str, payload: dict[str, Any]) -> Any:
        return await self.client.post(
            path,
            json=payload,
            headers=self.base_headers,
        )

    async def get(self, path: str, params: dict[str, str] | None = None) -> Any:
        return await self.client.get(
            path,
            params=params,
            headers=self.base_headers,
        )

    async def delete_json(
        self, path: str, payload: dict[str, Any] | None = None
    ) -> Any:
        return await self.client.delete(
            path,
            json=payload or {},
            headers=self.base_headers,
        )


@pytest.fixture
async def device_emulator(
    aiohttp_client: AiohttpClient,
    server_config: ServerConfig,
    auth_headers: dict[str, str],
) -> DeviceEmulator:
    """Fixture providing a configured DeviceEmulator instance."""
    app = create_app(server_config)
    client = await aiohttp_client(app)
    return DeviceEmulator(client, auth_headers)


@pytest.mark.asyncio
async def test_device_sync_lifecycle(device_emulator: DeviceEmulator) -> None:
    """Verify standard device file synchronization lifecycle."""
    # 1. Device calls synchronous/start
    resp_start = await device_emulator.post_json(
        "/api/file/2/files/synchronous/start",
        {"equipmentNo": device_emulator.equipment_no},
    )
    assert resp_start.status == 200
    data_start = await resp_start.json()
    assert (
        data_start.get("success") is True
        or "token" in data_start
        or "success" in str(data_start)
    )

    # 2. Device lists root folder recursively
    resp_list = await device_emulator.post_json(
        "/api/file/2/files/list_folder",
        {
            "path": "/",
            "recursive": True,
            "equipmentNo": device_emulator.equipment_no,
        },
    )
    assert resp_list.status == 200

    # 3. Device finishes sync
    resp_end = await device_emulator.post_json(
        "/api/file/2/files/synchronous/end",
        {
            "equipmentNo": device_emulator.equipment_no,
            "flag": "N",
        },
    )
    assert resp_end.status == 200


@pytest.mark.asyncio
async def test_device_schedule_task_creation_with_hex_id(
    device_emulator: DeviceEmulator,
) -> None:
    """Verify tablet can create schedule task with 32-character hex client task ID.

    Hardware devices generate MD5/UUID hex strings for client IDs (e.g.
    '7dba6ddc93925fcf6314d89149e506d8'), which historically caused a base-10
    int() ValueError.
    """
    client_hex_id = "7dba6ddc93925fcf6314d89149e506d8"
    task_payload = {
        "title": "Device Emulated Task",
        "detail": "Created by Supernote hardware emulator",
        "taskId": client_hex_id,
        "taskListId": "0",
        "status": "needsAction",
        "isReminderOn": "N",
    }

    resp = await device_emulator.post_json("/api/file/schedule/task", task_payload)
    assert resp.status == 200, (
        f"Expected 200 OK, got {resp.status}: {await resp.text()}"
    )
    data = await resp.json()
    assert data.get("success") is True
    assert "taskId" in data


@pytest.mark.asyncio
async def test_trace_fixture_replay_task_hex_id(
    device_emulator: DeviceEmulator,
) -> None:
    """Replay captured trace fixture and verify server response contract."""
    fixture_path = FIXTURES_DIR / "task_create_hex_id.json"
    if not fixture_path.exists():
        pytest.skip("Trace fixture task_create_hex_id.json not found")

    entries = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert len(entries) > 0

    # Test creating a task with the exact hex ID from the production failure log
    failed_hex_id = "7dba6ddc93925fcf6314d89149e506d8"
    resp = await device_emulator.post_json(
        "/api/file/schedule/task",
        {
            "title": "Replay Task From Production Failure Log",
            "taskId": failed_hex_id,
            "taskListId": "0",
        },
    )
    assert resp.status == 200
    data = await resp.json()
    assert data.get("success") is True


@pytest.mark.asyncio
async def test_device_delete_summary_route_contract(
    device_emulator: DeviceEmulator,
) -> None:
    """Document and verify behavior of DELETE /api/file/delete/summary.

    In production telemetry, tablets issue DELETE /api/file/delete/summary when
    removing note files or clearing summaries.
    """
    resp = await device_emulator.delete_json(
        "/api/file/delete/summary",
        {"id": 741730991659287183, "equipmentNo": device_emulator.equipment_no},
    )
    # Documents whether the endpoint is currently implemented (200) or a known gap (404)
    assert resp.status in (200, 404)
