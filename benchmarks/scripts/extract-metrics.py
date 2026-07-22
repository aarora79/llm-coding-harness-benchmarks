#!/usr/bin/env python3
"""Extract metrics from Claude Code session JSONL files.

Parses session transcripts to compute per-run metrics:
- Input/output/thinking tokens
- Cache read/write tokens
- Cost (computed from wall-clock x instance cost for self-hosted)
- Tool call count
- Error count
- Prompt/generation throughput (tokens/sec)

Usage:
    python3 extract-metrics.py <session-jsonl> <metrics-json-to-update>
    python3 extract-metrics.py --session-id <id> <metrics-json-to-update>
    python3 extract-metrics.py --latest <metrics-json-to-update>

The script updates an existing metrics.json with token/cache/throughput fields.
"""

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)
logger = logging.getLogger(__name__)


def _resolve_paths(argv: list[str]) -> tuple[Path | None, str | None]:
    """Resolve the session JSONL path and metrics path from raw argv.

    Preserves the original CLI contract: supports the --latest and
    --session-id flags as well as a positional session-JSONL path.

    Args:
        argv: The full process argument vector (including argv[0]).

    Returns:
        A tuple of (jsonl_path, metrics_path). Either element may be None
        when it cannot be resolved or was not supplied.
    """
    args = argv[1:]

    if args[0] == "--latest":
        jsonl_path = find_session_jsonl(latest=True)
        metrics_path = args[1] if len(args) > 1 else None
    elif args[0] == "--session-id":
        jsonl_path = find_session_jsonl(session_id=args[1])
        metrics_path = args[2] if len(args) > 2 else None
    else:
        jsonl_path = Path(args[0])
        metrics_path = args[1] if len(args) > 1 else None

    return jsonl_path, metrics_path


def _log_extracted_metrics(extracted: dict[str, Any]) -> None:
    """Log a human-readable summary of the extracted metrics.

    Args:
        extracted: The metrics dictionary produced by extract_from_jsonl.
    """
    logger.info("  API calls: %s", extracted["api_calls"])
    logger.info("  Input tokens: %s", f"{extracted['input_tokens']:,}")
    logger.info("  Output tokens: %s", f"{extracted['output_tokens']:,}")
    logger.info("  Cache read: %s", f"{extracted['cache_read_tokens']:,}")
    logger.info("  Cache write: %s", f"{extracted['cache_write_tokens']:,}")
    logger.info("  Tool calls: %s", extracted["tool_calls"])
    logger.info("  Errors: %s", extracted["errors"])


def _report_result(metrics_path: str | None, extracted: dict[str, Any]) -> None:
    """Persist and report metrics, or emit them as JSON to stdout.

    When a metrics path is supplied the extracted values are merged into the
    existing metrics.json and a summary is logged. Otherwise the extracted
    metrics are printed as JSON (product output).

    Args:
        metrics_path: Path to the metrics.json to update, or None.
        extracted: The metrics dictionary produced by extract_from_jsonl.
    """
    if not metrics_path:
        print(json.dumps(extracted, indent=2))
        return

    updated = update_metrics_json(metrics_path, extracted)
    logger.info("Updated: %s", metrics_path)
    if "task_cost_usd" in updated:
        logger.info("  Task cost: $%.4f", updated["task_cost_usd"])
    if "cache_hit_rate" in updated:
        logger.info("  Cache hit rate: %.1f%%", updated["cache_hit_rate"] * 100)


def find_session_jsonl(
    session_id: str | None = None, latest: bool = False
) -> Path | None:
    """Find a session JSONL file under the Claude projects directory.

    Args:
        session_id: If given, locate the JSONL file for this session id.
        latest: If True, return the most recently modified JSONL file.

    Returns:
        The resolved JSONL path, or None if no match is found.
    """
    base = Path.home() / ".claude" / "projects"

    if session_id:
        for jsonl in base.rglob(f"{session_id}.jsonl"):
            return jsonl

    if latest:
        all_jsonls = list(base.rglob("*.jsonl"))
        if all_jsonls:
            return max(all_jsonls, key=lambda p: p.stat().st_mtime)

    return None


def extract_from_jsonl(jsonl_path: Path) -> dict[str, Any]:
    """Parse a session JSONL and extract metrics.

    Args:
        jsonl_path: Path to the session transcript JSONL file.

    Returns:
        A metrics dictionary with token counts, tool/error/API-call counts,
        and derived wall-clock and throughput fields.
    """
    metrics: dict[str, Any] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "thinking_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "tool_calls": 0,
        "errors": 0,
        "api_calls": 0,
        "first_timestamp": None,
        "last_timestamp": None,
    }

    with open(jsonl_path, encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Track timestamps
            ts = entry.get("timestamp") or entry.get("ts")
            if ts:
                if metrics["first_timestamp"] is None:
                    metrics["first_timestamp"] = ts
                metrics["last_timestamp"] = ts

            # Extract usage from API responses
            usage = entry.get("usage") or {}
            if not usage:
                # Check nested in message
                msg = entry.get("message") or entry.get("response") or {}
                usage = msg.get("usage") or {}

            if usage:
                metrics["api_calls"] += 1
                metrics["input_tokens"] += usage.get("input_tokens", 0)
                metrics["output_tokens"] += usage.get("output_tokens", 0)
                metrics["cache_read_tokens"] += usage.get(
                    "cache_read_input_tokens", 0
                ) or usage.get("cache_read", 0)
                metrics["cache_write_tokens"] += usage.get(
                    "cache_creation_input_tokens", 0
                ) or usage.get("cache_write", 0)

            # Count tool uses
            entry_type = entry.get("type") or ""
            if entry_type == "tool_use" or entry.get("tool_use"):
                metrics["tool_calls"] += 1

            # Count content blocks with tool_use type
            content = entry.get("content") or []
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        metrics["tool_calls"] += 1

            # Count errors
            if entry.get("error") or entry_type == "error":
                metrics["errors"] += 1

    # Compute wall clock from timestamps
    if metrics["first_timestamp"] and metrics["last_timestamp"]:
        try:
            t1 = datetime.fromisoformat(
                metrics["first_timestamp"].replace("Z", "+00:00")
            )
            t2 = datetime.fromisoformat(
                metrics["last_timestamp"].replace("Z", "+00:00")
            )
            metrics["wall_clock_seconds"] = (t2 - t1).total_seconds()
        except (ValueError, TypeError):
            pass

    # Compute throughput
    wall = metrics.get("wall_clock_seconds", 0)
    if wall and wall > 0:
        metrics["generation_tokens_per_sec"] = round(metrics["output_tokens"] / wall, 1)
        metrics["prompt_tokens_per_sec"] = round(metrics["input_tokens"] / wall, 1)

    # Clean up internal fields
    del metrics["first_timestamp"]
    del metrics["last_timestamp"]

    return metrics


def update_metrics_json(metrics_path: str, extracted: dict[str, Any]) -> dict[str, Any]:
    """Update an existing metrics.json with extracted data.

    Non-null, non-zero extracted values are merged into the existing metrics.
    Derived task cost and cache-hit-rate fields are computed when the required
    inputs are present.

    Args:
        metrics_path: Path to the metrics.json to read and write.
        extracted: The metrics dictionary produced by extract_from_jsonl.

    Returns:
        The merged metrics dictionary that was written to disk.
    """
    if os.path.exists(metrics_path):
        with open(metrics_path, encoding="utf-8") as f:
            existing = json.load(f)
    else:
        existing = {}

    # Update with extracted values (don't overwrite non-null existing values)
    for key, value in extracted.items():
        if value is not None and value != 0:
            existing[key] = value

    # Compute task cost if we have wall_clock and instance cost
    wall = existing.get("wall_clock_seconds", 0)
    cost_per_hr = existing.get("instance_cost_per_hr", 0)
    if wall and cost_per_hr:
        existing["task_cost_usd"] = round((wall / 3600) * cost_per_hr, 4)

    # Cache hit rate
    cache_read = existing.get("cache_read_tokens", 0)
    total_input = existing.get("input_tokens", 0)
    if total_input > 0:
        existing["cache_hit_rate"] = round(cache_read / total_input, 3)

    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2)

    return existing


def main() -> None:
    """Parse arguments, extract metrics, and report or persist the result."""
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    jsonl_path, metrics_path = _resolve_paths(sys.argv)

    if not jsonl_path or not jsonl_path.exists():
        logger.error("Could not find session JSONL: %s", jsonl_path)
        sys.exit(1)

    logger.info("Extracting from: %s", jsonl_path)
    extracted = extract_from_jsonl(jsonl_path)

    _log_extracted_metrics(extracted)
    _report_result(metrics_path, extracted)


if __name__ == "__main__":
    main()
