"""OpenAPI Specification and Route Registry Conformance Test Suite.

Validates that server routes registered in aiohttp conform to the community
OpenAPI specification at api-spec/openapi.yaml.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from supernote.server.app import create_app
from supernote.server.config import ServerConfig

SPEC_PATH = Path(__file__).parent.parent.parent / "api-spec" / "openapi.yaml"


def normalize_pattern(pattern: str) -> str:
    """Normalize OpenAPI path template ({param}) to a regex pattern."""
    # Convert {param} to [^/]+
    norm = re.sub(r"\{[a-zA-Z0-9_]+\}", r"[^/]+", pattern)
    return f"^{norm}$"


@pytest.mark.asyncio
async def test_openapi_route_conformance(server_config: ServerConfig) -> None:
    """Verify core OpenAPI routes are registered in the aiohttp application."""
    if not SPEC_PATH.exists():
        pytest.skip(f"OpenAPI spec not found at {SPEC_PATH}")

    with open(SPEC_PATH, encoding="utf-8") as f:
        spec = yaml.safe_load(f)

    app = create_app(server_config)

    registered_routes: set[tuple[str, str]] = set()
    for resource in app.router.resources():
        info = resource.get_info()
        formatter = info.get("formatter") or info.get("path")
        if not formatter:
            continue
        for route in resource:
            registered_routes.add((route.method.upper(), formatter))

    paths = spec.get("paths", {})
    assert len(paths) > 0, "OpenAPI spec contains no paths"

    implemented_count = 0
    missing_routes: list[str] = []

    for path in paths.keys():
        # Clean path of query parameters or fragments if any
        clean_path = path.split("?")[0]
        pattern = normalize_pattern(clean_path)

        # Check if any registered route matches
        matched = False
        for method, reg_path in registered_routes:
            if reg_path == clean_path or re.match(pattern, reg_path):
                matched = True
                implemented_count += 1
                break

        if not matched:
            missing_routes.append(path)

    # Core required routes that must ALWAYS be implemented
    core_critical_routes = [
        "/api/official/user/account/login/new",
        "/api/user/query/token",
        "/api/file/2/files/list_folder",
        "/api/file/2/files/synchronous/start",
        "/api/file/2/files/synchronous/end",
        "/api/file/schedule/group",
        "/api/file/schedule/task",
    ]

    for core_route in core_critical_routes:
        pattern = normalize_pattern(core_route)
        is_registered = any(
            reg_path == core_route or re.match(pattern, reg_path)
            for _, reg_path in registered_routes
        )
        assert is_registered, (
            f"Critical core route '{core_route}' is not registered in the application!"
        )

    # Ensure coverage ratio matches current baseline (at least 65% of community spec)
    total_spec_paths = len(paths)
    coverage_ratio = implemented_count / total_spec_paths
    assert coverage_ratio >= 0.65, (
        f"API spec coverage dropped below baseline: {implemented_count}/{total_spec_paths} ({coverage_ratio:.1%}). "
        f"Missing: {missing_routes[:10]}"
    )
