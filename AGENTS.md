# Agent Development Guidelines for Supernote Private Cloud

Welcome to the **Supernote Agent Development Environment**. This repository is configured with an automated testing and emulation harness tailored for AI agents and human contributors to implement features, fix device sync issues, and verify protocol compatibility safely.

---

## 1. Safety & Environment Boundaries

> [!CRITICAL]
> **Production Isolation Policy**:
> - **NEVER** write to, alter, or connect to the production container (`supernote-server`) or host storage in `/srv/containers/data/supernote/`.
> - **NEVER** restart or stop the production stack (`/srv/containers/stacks/supernote-dev`).
> - All development, experimentation, and automated testing MUST run entirely within this build context (`/srv/containers/build_contexts/supernote-agent/`) using ephemeral in-memory databases and isolated temporary directories.

---

## 2. Fast-Start Execution Commands

All workflows follow standardized scripts in `script/`:

| Command | Purpose |
| :--- | :--- |
| `./script/bootstrap` | Bootstraps the Python 3.14 virtual environment with `uv`, installs all dependencies, and configures pre-commit hooks. |
| `./script/test` | Runs the full pytest test suite in parallel (`-n auto`). |
| `./script/test tests/server/test_device_emulator.py` | Runs the hardware emulation and trace replay test suite. |
| `./script/test tests/server/test_openapi_conformance.py` | Validates route registration against `api-spec/openapi.yaml`. |
| `./script/lint` | Runs all static analysis tools (`ruff check`, `ruff format`, `ty check`, `codespell`, `yamllint`). |
| `./script/server` | Launches an in-memory ephemeral test server at `http://127.0.0.1:8080`. |
| `./script/extract_trace_fixture.py` | Extracts and sanitizes real request/response traces from live server logs into test fixtures. |

---

## 3. The Synthetic Device & Trace Replay Harness

### 3.1 Device Emulator (`tests/server/test_device_emulator.py`)
Hardware Supernote tablets use the Android OkHttp client, specialized HTTP headers (`equipmentNo`, `version`, `channel`), and customized request shapes.

Use `DeviceEmulator` fixture in tests to simulate tablet behavior:
```python
from aiohttp.test_utils import TestClient
from tests.server.test_device_emulator import DeviceEmulator

async def test_my_feature(device_emulator: DeviceEmulator):
    # Sends requests with okhttp/4.8.0 headers and device tokens
    resp = await device_emulator.post_json(
        "/api/file/2/files/list_folder",
        {"path": "/", "recursive": True, "equipmentNo": device_emulator.equipment_no},
    )
    assert resp.status == 200
```

### 3.2 Extracting Live Traces into Regression Fixtures
When investigating a new tablet error or implementing an endpoint requested by the device:
1. Extract and sanitize the real wire trace:
   ```bash
   ./script/extract_trace_fixture.py \
       --endpoint "/api/target/endpoint" \
       --output tests/fixtures/device_traces/target_endpoint.json
   ```
2. Inspect the extracted fixture in `tests/fixtures/device_traces/` (tokens and passwords are automatically stripped).
3. Create a reproduction test in `tests/server/test_device_emulator.py` that replays the exact payload.

---

## 4. Live Server Testing (`ServerRunner`)

For tests requiring an out-of-process live HTTP/MCP server with full CLI integration, use `supernote.testing.server_runner.ServerRunner`:
```python
from pathlib import Path
import aiohttp
from supernote.testing import ServerRunner

async with ServerRunner(storage_dir=Path("/tmp/test_dir")) as server:
    assert await server.is_healthy()
    result = await server.run_cli("cloud", "ls")
    assert result.returncode == 0
```

---

## 5. Architectural Principles & Review Standards

Agents working on this codebase must adhere to the core principles enforced in `.agent/skills/code-review-style/`:
1. **1:1 Test Parity**: Every module in `supernote/` must have a corresponding test file in `tests/` matching its exact path and name (`supernote/path/module.py` $\rightarrow$ `tests/path/test_module.py`).
2. **Zero Leaky Abstractions**: Keep transport wire literals, protocol status strings, and raw tokens out of domain services. Methods should return domain models and raise typed exceptions.
3. **Linear Control Flow**: Use early return guards to avoid deep nesting.
4. **Deterministic DB Migrations**: Never modify existing migrations. Use `./script/db_revision "<description>"` when changing models in `supernote/server/db/models/`, and verify backward compatibility against fixtures in `tests/fixtures/`.
