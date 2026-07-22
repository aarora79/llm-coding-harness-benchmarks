#!/usr/bin/env python3
"""Replay a conversation JSONL against a vLLM endpoint for performance benchmarking.

Reads a replay.jsonl file (produced by extract-metrics.py or the session parser),
sends each call's messages to the vLLM Anthropic Messages API, and records
per-call metrics: input tokens, output tokens, latency, prompt tok/s, generation tok/s.

Usage:
    python3 replay-benchmark.py <replay.jsonl> <endpoint> [lines]

    replay.jsonl  - path to the replay file (each line = one API call with messages array)
    endpoint      - vLLM base URL (e.g. http://127.0.0.1:8000)
    lines         - number of lines to replay (default: 1, 0 = full file)

Example:
    python3 replay-benchmark.py replay.jsonl http://127.0.0.1:8000 0
    python3 replay-benchmark.py replay.jsonl http://127.0.0.1:8000 5

Output:
    Prints per-call metrics and a summary table to stdout.
    Saves results to replay-results.json in the same directory as the input file.
"""

import argparse
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)
logger = logging.getLogger(__name__)

DEFAULT_MODEL = "kimi-k2.7-code"
DEFAULT_MAX_TOKENS = 16000
DEFAULT_CONCURRENCY = 1
API_KEY = "local"
ANTHROPIC_VERSION = "2023-06-01"
REQUEST_TIMEOUT_SECONDS = 600
DETECT_TIMEOUT_SECONDS = 5
TABLE_WIDTH = 90
SUMMARY_WIDTH = 80


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Parsed argument namespace.
    """
    parser = argparse.ArgumentParser(
        description="Replay a conversation JSONL against a vLLM endpoint for benchmarking.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("jsonl", help="Path to the replay.jsonl file")
    parser.add_argument("endpoint", help="vLLM base URL (e.g. http://127.0.0.1:8000)")
    parser.add_argument(
        "lines",
        nargs="?",
        type=int,
        default=1,
        help="Number of lines to replay (default: 1, 0 = full file)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model name to use (auto-detected if not set)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=DEFAULT_MAX_TOKENS,
        help="Max output tokens per call",
    )
    parser.add_argument(
        "--concurrency",
        "-c",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help="Number of concurrent requests (default: 1)",
    )
    return parser.parse_args()


def _load_lines(path: str) -> list[str]:
    """Load non-empty, stripped lines from a replay JSONL file.

    Args:
        path: Path to the replay.jsonl file.

    Returns:
        List of non-empty line strings.
    """
    with open(path, encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def _log_config(
    args: argparse.Namespace, model: str, num_lines: int, total_available: int
) -> None:
    """Log the run configuration to stdout via the logger.

    Args:
        args: Parsed command-line arguments.
        model: Resolved model name.
        num_lines: Number of lines that will be replayed.
        total_available: Total number of lines available in the file.
    """
    logger.info("Replay benchmark")
    logger.info("  File: %s", args.jsonl)
    logger.info("  Endpoint: %s", args.endpoint)
    logger.info("  Model: %s", model)
    logger.info("  Lines to replay: %s / %s", num_lines, total_available)
    logger.info("  Concurrency: %s", args.concurrency)
    logger.info("  Max output tokens: %s", args.max_tokens)


def _print_table_header() -> None:
    """Print the aligned per-call metrics table header to stdout."""
    header = (
        f"{'Call':>4} | {'Input tok':>10} | {'Output tok':>10} | "
        f"{'Latency ms':>10} | {'Prompt t/s':>10} | {'Gen t/s':>10} | Status"
    )
    print("=" * TABLE_WIDTH)
    print(header)
    print("-" * TABLE_WIDTH)


def _print_error_row(i: int, result: dict[str, Any]) -> None:
    """Print a table row for a failed call.

    Args:
        i: Call index.
        result: Error result dict for the call.
    """
    print(
        f"{i:4d} | {'ERROR':>10} | {'-':>10} | {result['latency_ms']:>10.0f} | "
        f"{'-':>10} | {'-':>10} | "
        f"{result.get('status_code', '?')}: {result.get('body', '')[:40]}"
    )


def _print_success_row(i: int, result: dict[str, Any]) -> None:
    """Compute derived throughput metrics and print a table row for a successful call.

    Args:
        i: Call index.
        result: Successful result dict for the call (mutated with throughput fields).
    """
    input_tok = result["input_tokens"]
    output_tok = result["output_tokens"]
    latency = result["latency_ms"]

    prompt_tps = round(input_tok / (latency / 1000), 1) if latency > 0 else 0
    gen_tps = round(output_tok / (latency / 1000), 1) if latency > 0 else 0

    result["prompt_tokens_per_sec"] = prompt_tps
    result["generation_tokens_per_sec"] = gen_tps

    print(
        f"{i:4d} | {input_tok:>10,} | {output_tok:>10,} | {latency:>10,.0f} | "
        f"{prompt_tps:>10,.1f} | {gen_tps:>10,.1f} | {result['stop_reason']}"
    )


def _run_replay(
    endpoint: str,
    lines: list[str],
    num_lines: int,
    model: str,
    max_tokens: int,
    concurrency: int,
) -> tuple[list[dict[str, Any] | None], float]:
    """Replay calls concurrently, printing a per-call metrics row for each result.

    Args:
        endpoint: vLLM base URL.
        lines: All replay line strings.
        num_lines: Number of leading lines to replay.
        model: Model name to send.
        max_tokens: Max output tokens per call.
        concurrency: Number of concurrent worker threads.

    Returns:
        Tuple of (per-call results indexed by call number, total wall-clock seconds).
    """
    _print_table_header()

    results: list[dict[str, Any] | None] = [None] * num_lines
    total_start = time.time()

    def run_call(i: int) -> tuple[int, dict[str, Any]]:
        call_data = json.loads(lines[i])
        messages = call_data.get("messages", [])
        result = send_request(endpoint, messages, model=model, max_tokens=max_tokens)
        result["call_number"] = i
        return i, result

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {executor.submit(run_call, i): i for i in range(num_lines)}

        for future in as_completed(futures):
            i, result = future.result()
            results[i] = result

            if result["error"]:
                _print_error_row(i, result)
                continue

            _print_success_row(i, result)

    total_elapsed = time.time() - total_start
    return results, total_elapsed


def _build_summary(
    args: argparse.Namespace,
    model: str,
    num_lines: int,
    results: list[dict[str, Any] | None],
    successful: list[dict[str, Any]],
    total_elapsed: float,
) -> dict[str, Any]:
    """Build the aggregate summary dict for the run.

    Args:
        args: Parsed command-line arguments.
        model: Resolved model name.
        num_lines: Total number of calls attempted.
        results: Per-call results indexed by call number.
        successful: Subset of results that succeeded.
        total_elapsed: Total wall-clock seconds for the run.

    Returns:
        Summary dict suitable for printing and serialization.
    """
    total_input = sum(r["input_tokens"] for r in successful)
    total_output = sum(r["output_tokens"] for r in successful)
    total_latency = sum(r["latency_ms"] for r in successful)
    avg_prompt_tps = (
        round(total_input / (total_latency / 1000), 1) if total_latency > 0 else 0
    )
    avg_gen_tps = (
        round(total_output / (total_latency / 1000), 1) if total_latency > 0 else 0
    )

    return {
        "model": model,
        "endpoint": args.endpoint,
        "replay_file": args.jsonl,
        "concurrency": args.concurrency,
        "calls_total": num_lines,
        "calls_successful": len(successful),
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_latency_ms": round(total_latency, 1),
        "wall_clock_seconds": round(total_elapsed, 1),
        "avg_prompt_tokens_per_sec": avg_prompt_tps,
        "avg_generation_tokens_per_sec": avg_gen_tps,
        "per_call_results": results,
    }


def _print_summary(summary: dict[str, Any], num_lines: int) -> None:
    """Print the aggregate summary block to stdout.

    Args:
        summary: Summary dict built by _build_summary.
        num_lines: Total number of calls attempted.
    """
    total_latency = summary["total_latency_ms"]
    successful = summary["calls_successful"]

    print(f"\n{'=' * SUMMARY_WIDTH}")
    print(f"Summary ({successful} successful / {num_lines} total calls)")
    print(f"  Total input tokens:    {summary['total_input_tokens']:>12,}")
    print(f"  Total output tokens:   {summary['total_output_tokens']:>12,}")
    print(
        f"  Total latency:         {total_latency:>12,.0f} ms ({total_latency / 1000:.1f}s)"
    )
    print(f"  Wall clock:            {summary['wall_clock_seconds']:>12,.1f}s")
    print(f"  Avg prompt tok/s:      {summary['avg_prompt_tokens_per_sec']:>12,.1f}")
    print(
        f"  Avg generation tok/s:  {summary['avg_generation_tokens_per_sec']:>12,.1f}"
    )
    print(f"  Errors:                {num_lines - successful:>12}")


def _save_summary(summary: dict[str, Any], jsonl_path: str) -> None:
    """Save the summary dict to replay-results.json next to the input file.

    Args:
        summary: Summary dict built by _build_summary.
        jsonl_path: Path to the input replay file (used to derive the output directory).
    """
    output_path = os.path.join(os.path.dirname(jsonl_path), "replay-results.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    logger.info("Results saved: %s", output_path)


def send_request(
    endpoint: str,
    messages: list[dict[str, Any]],
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> dict[str, Any]:
    """Send a single request to the vLLM Anthropic Messages API.

    Args:
        endpoint: vLLM base URL.
        messages: Anthropic Messages API messages array.
        model: Model name to send.
        max_tokens: Max output tokens for the call.

    Returns:
        Result dict with either metrics (on success) or error details (on failure).
    """
    url = f"{endpoint}/v1/messages"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": API_KEY,
        "anthropic-version": ANTHROPIC_VERSION,
    }
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
    }

    start_ms = time.time() * 1000
    try:
        response = requests.post(
            url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT_SECONDS
        )
    except requests.RequestException as e:
        latency_ms = time.time() * 1000 - start_ms
        logger.warning("Request to %s failed: %s", url, e)
        return {
            "error": True,
            "status_code": "EXC",
            "body": str(e)[:500],
            "latency_ms": latency_ms,
        }
    end_ms = time.time() * 1000
    latency_ms = end_ms - start_ms

    if response.status_code != 200:
        return {
            "error": True,
            "status_code": response.status_code,
            "body": response.text[:500],
            "latency_ms": latency_ms,
        }

    data = response.json()
    usage = data.get("usage", {})

    return {
        "error": False,
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "latency_ms": round(latency_ms, 1),
        "model": data.get("model", ""),
        "stop_reason": data.get("stop_reason", ""),
    }


def detect_model(endpoint: str) -> str:
    """Auto-detect the served model name from the endpoint.

    Args:
        endpoint: vLLM base URL.

    Returns:
        Detected model id, or the default model name on failure.
    """
    try:
        resp = requests.get(f"{endpoint}/v1/models", timeout=DETECT_TIMEOUT_SECONDS)
        if resp.status_code == 200:
            models = resp.json().get("data", [])
            real = [m["id"] for m in models if "claude" not in m["id"].lower()]
            if real:
                return real[0]
            if models:
                return models[0]["id"]
    except requests.RequestException:  # nosec B110 - best-effort detection, default fallback below
        return DEFAULT_MODEL
    return DEFAULT_MODEL


def main() -> None:
    """Parse arguments and orchestrate the replay benchmark run."""
    args = _parse_args()

    if not os.path.exists(args.jsonl):
        logger.error("file not found: %s", args.jsonl)
        sys.exit(1)

    all_lines = _load_lines(args.jsonl)
    total_available = len(all_lines)
    num_lines = total_available if args.lines == 0 else min(args.lines, total_available)

    model = args.model or detect_model(args.endpoint)

    _log_config(args, model, num_lines, total_available)

    results, total_elapsed = _run_replay(
        args.endpoint, all_lines, num_lines, model, args.max_tokens, args.concurrency
    )

    successful = [r for r in results if r is not None and not r.get("error")]
    if successful:
        summary = _build_summary(
            args, model, num_lines, results, successful, total_elapsed
        )
        _print_summary(summary, num_lines)
        _save_summary(summary, args.jsonl)


if __name__ == "__main__":
    main()
