#!/usr/bin/env python3
"""Extract and sanitize request/response trace logs into test fixtures.

Usage:
    ./script/extract_trace_fixture.py --help
    ./script/extract_trace_fixture.py --endpoint "/api/file/schedule/task" --output tests/fixtures/device_traces/task_create_hex_id.json
    ./script/extract_trace_fixture.py --status 400 --limit 5 --output tests/fixtures/device_traces/status_400_samples.json
    ./script/extract_trace_fixture.py --endpoint "/socket.io/" --output tests/fixtures/device_traces/socketio_handshake.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

DEFAULT_TRACE_LOG = Path("/srv/containers/data/supernote/system/trace.log")


def sanitize_value(val: Any) -> Any:
    """Recursively sanitize sensitive values such as tokens and passwords."""
    if isinstance(val, dict):
        sanitized = {}
        for k, v in val.items():
            lower_k = k.lower().replace("-", "_")
            if any(
                secret in lower_k
                for secret in ("token", "secret", "password", "key", "auth")
            ):
                sanitized[k] = "***SANITIZED***"
            else:
                sanitized[k] = sanitize_value(v)
        return sanitized
    elif isinstance(val, list):
        return [sanitize_value(item) for item in val]
    elif isinstance(val, str):
        # Sanitize JWT strings
        if re.match(
            r"^ey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}$", val
        ):
            return "***SANITIZED_JWT***"
        # Sanitize token query parameters in URLs
        val = re.sub(r"token=[^&]+", "token=***SANITIZED_TOKEN***", val)
        val = re.sub(r"sign=[^&]+", "sign=***SANITIZED_SIGN***", val)
        return val
    return val


def parse_trace_log(trace_path: Path) -> list[dict[str, Any]]:
    """Parse JSON objects from a trace log file."""
    if not trace_path.exists():
        print(f"Error: Trace log file not found at {trace_path}", file=sys.stderr)
        return []

    content = trace_path.read_text(encoding="utf-8", errors="replace")
    raw_entries = re.findall(r"\{\s*\"timestamp\":.*?\n\}", content, re.DOTALL)
    entries: list[dict[str, Any]] = []

    for raw in raw_entries:
        try:
            entry = json.loads(raw)
            entries.append(entry)
        except json.JSONDecodeError:
            continue
    return entries


def extract_fixtures(
    trace_path: Path,
    output_path: Path,
    endpoint: str | None = None,
    status: int | None = None,
    limit: int = 10,
) -> None:
    """Extract, filter, sanitize, and save trace fixtures."""
    entries = parse_trace_log(trace_path)
    matched: list[dict[str, Any]] = []

    for entry in entries:
        req = entry.get("request", {})
        resp = entry.get("response", {})
        url = req.get("url", "")
        entry_status = resp.get("status")

        if endpoint and endpoint not in url:
            continue
        if status is not None and entry_status != status:
            continue

        sanitized_entry = sanitize_value(entry)
        matched.append(sanitized_entry)
        if len(matched) >= limit:
            break

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(matched, indent=2), encoding="utf-8")
    print(f"Extracted {len(matched)} trace entries into {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract and sanitize trace.log entries into test fixtures."
    )
    parser.add_argument(
        "--trace-log",
        type=Path,
        default=DEFAULT_TRACE_LOG,
        help="Path to source trace.log (default: /srv/containers/data/supernote/system/trace.log)",
    )
    parser.add_argument(
        "--endpoint",
        type=str,
        default=None,
        help="Filter traces by URL substring (e.g. /api/file/schedule/task)",
    )
    parser.add_argument(
        "--status",
        type=int,
        default=None,
        help="Filter traces by HTTP response status code (e.g. 400, 404)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of entries to extract (default: 10)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output fixture file path (JSON)",
    )

    args = parser.parse_args()
    extract_fixtures(
        trace_path=args.trace_log,
        output_path=args.output,
        endpoint=args.endpoint,
        status=args.status,
        limit=args.limit,
    )


if __name__ == "__main__":
    main()
