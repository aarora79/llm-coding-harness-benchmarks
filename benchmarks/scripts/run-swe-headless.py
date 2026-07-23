#!/usr/bin/env python3
"""Run the SWE benchmark headless: drive `claude -p /swe` over a dataset.

Given a dataset YAML and a runner config (endpoint, model, claude flags), this
harness runs each task end to end:

  1. Clone the task's repo at its pinned ref into a temporary directory.
  2. Invoke `claude -p "/swe repo: ... problem: ... model: ... answers: ..."`
     non-interactively, letting the /swe skill produce the four design
     artifacts (github-issue.md, lld.md, review.md, testing.md).
  3. Parse the run's JSON result for the six benchmark metrics (input/output/
     cache tokens, latency, and the number of LLM turns the agent took) and
     write them to metrics.json next to the artifacts.

Routing and claude flags come from the runner config; any field may be
overridden on the command line (CLI wins).

Usage:
    uv run scripts/run-swe-headless.py --config config/runner.example.yaml
    uv run scripts/run-swe-headless.py --config config/runner.example.yaml \\
        --model qwen3-coder-30b --tasks remove-faiss
    uv run scripts/run-swe-headless.py --config config/runner.example.yaml --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess  # nosec B404 - used with list args, no shell, hardcoded command
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from dataset_loader import Dataset, DatasetError, Task, load_dataset
from runner_config import RunnerConfig, RunnerConfigError, load_runner_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ARTIFACT_FILENAMES = ("github-issue.md", "lld.md", "review.md", "testing.md")
GIT_CLONE_TIMEOUT_SECONDS = 300

# The harness scrapes vLLM's entire Prometheus /metrics surface (every family
# under this prefix) rather than a curated subset, so nothing is omitted and new
# vLLM metrics appear automatically. These series are SERVER-WIDE and CUMULATIVE:
# they aggregate every request from every client since the server started and
# carry no per-request or per-session label. See _snapshot_vllm_metrics for the
# loud single-tenant caveat that this implies. Note: in vLLM v1 a prefix-cache
# hit IS the KV-cache reuse signal -- there is no separate "KV cache hit"
# counter; the prefix-cache queries/hits counters are measured in tokens, not
# lookup events. The counters used to derive the headline hit rates:
VLLM_METRIC_PREFIX = "vllm:"
PREFIX_CACHE_QUERIES_METRIC = "vllm:prefix_cache_queries_total"
PREFIX_CACHE_HITS_METRIC = "vllm:prefix_cache_hits_total"
PROMPT_TOKENS_METRIC = "vllm:prompt_tokens_total"
PROMPT_TOKENS_CACHED_METRIC = "vllm:prompt_tokens_cached_total"
KV_CACHE_USAGE_METRIC = "vllm:kv_cache_usage_perc"
METRICS_SCRAPE_TIMEOUT_SECONDS = 10

# Gauges are point-in-time, so a before/after snapshot misses what happened
# DURING the run: KV-cache usage, for instance, reads its true value only while a
# request is in flight and drains back to 0 once the run ends. A background
# poller samples these while claude -p runs and reports the peak/mean instead.
# See _GaugePoller. These are the load/pressure gauges that actually vary.
SAMPLED_GAUGE_METRICS = (
    KV_CACHE_USAGE_METRIC,
    "vllm:num_requests_running",
    "vllm:num_requests_waiting",
)
GAUGE_POLL_INTERVAL_SECONDS = 1.0


def _repo_name(repo_url: str) -> str:
    """Derive the kebab-case repo name from a clone URL.

    Args:
        repo_url: The HTTPS clone URL (with or without a trailing .git).

    Returns:
        The repository basename, e.g. "mcp-gateway-registry".
    """
    return repo_url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")


def _clone_repo(task: Task, ref: str, clone_dir: str, log_prefix: str = "") -> Path:
    """Clone a task's repo at a ref into a temp dir named after the repo.

    The checkout lands at ``<clone_dir>/<mktemp>/<repo-name>`` so the /swe skill,
    which derives {repo-name} from the clone path's basename, gets the right name.

    Args:
        task: The task whose repo to clone.
        ref: The git ref (tag/branch/commit) to check out.
        clone_dir: Parent directory for the temporary clone.
        log_prefix: Optional label (e.g. ``[task=x] 3 of 12``) prepended to the
            clone log line so interleaved concurrent runs stay legible.

    Returns:
        Path to the cloned repository.

    Raises:
        RuntimeError: If the clone command fails or times out.
    """
    name = _repo_name(task.repo)
    parent = Path(tempfile.mkdtemp(prefix="swe-", dir=clone_dir))
    dest = parent / name
    prefix = f"{log_prefix} " if log_prefix else ""
    logger.info("  %sCloning %s @ %s into %s", prefix, task.repo, ref, dest)
    try:
        subprocess.run(  # nosec B603 B607 - hardcoded git, args are dataset values, no shell
            [
                "git",
                "clone",
                "--branch",
                ref,
                "--depth",
                "1",
                task.repo,
                str(dest),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=GIT_CLONE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        shutil.rmtree(parent, ignore_errors=True)
        raise RuntimeError(f"git clone timed out for {task.repo} @ {ref}") from exc
    except subprocess.CalledProcessError as exc:
        shutil.rmtree(parent, ignore_errors=True)
        raise RuntimeError(
            f"git clone failed for {task.repo} @ {ref}: {exc.stderr.strip()[:500]}"
        ) from exc
    return dest


def _build_prompt(task: Task, clone_path: Path, ref: str, model: str) -> str:
    """Build the non-interactive /swe prompt for a task.

    Includes the four keys the skill needs to enter non-interactive mode
    (repo, problem, model, answers) plus the full problem statement and, when
    present, the reference issue URL.

    Args:
        task: The task to run.
        clone_path: Local path to the cloned repo.
        ref: The git ref checked out.
        model: The model name (also the artifact subfolder name).

    Returns:
        The prompt string to pass to `claude -p`.
    """
    answers = task.clarifying_answers or (
        "No separate answers provided. Use your best judgment; all needed "
        "information is in the task description below."
    )
    lines = [
        f"/swe repo: {clone_path} problem: {task.id} model: {model} "
        f'tag: {ref} answers: "{answers.strip()}"',
        "",
        "Task description:",
        task.problem_statement or "(see reference issue)",
    ]
    if task.problem_issue_url:
        lines += ["", f"Reference issue: {task.problem_issue_url}"]
    return "\n".join(lines)


def _build_env(config: RunnerConfig) -> dict[str, str]:
    """Build the environment for the claude subprocess from the runner config.

    For provider=endpoint, routing pins ANTHROPIC_BASE_URL/API_KEY and disables
    Bedrock. For provider=bedrock, it flips CLAUDE_CODE_USE_BEDROCK=1 and sets
    AWS_REGION so claude talks to Amazon Bedrock natively, using the ambient AWS
    credentials; no base URL or api key is set.

    Args:
        config: The runner config.

    Returns:
        A copy of the current environment with routing overrides applied.
    """
    env = os.environ.copy()
    env["DISABLE_NON_ESSENTIAL_MODEL_CALLS"] = "1"
    env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(config.max_output_tokens)
    env["CLAUDE_CODE_SUBAGENT_MODEL"] = config.model
    if config.is_bedrock:
        env["CLAUDE_CODE_USE_BEDROCK"] = "1"
        region = config.resolved_region()
        if region:
            env["AWS_REGION"] = region
        # A stray ANTHROPIC_BASE_URL in the ambient env would otherwise redirect
        # the Bedrock-mode client away from Bedrock, so clear it.
        env.pop("ANTHROPIC_BASE_URL", None)
    else:
        env["ANTHROPIC_BASE_URL"] = config.endpoint
        env["ANTHROPIC_API_KEY"] = config.api_key
        env["CLAUDE_CODE_USE_BEDROCK"] = "0"
    return env


def _build_settings_arg(config: RunnerConfig) -> str:
    """Build the value for `claude --settings`.

    A settings file's ``env`` block takes precedence over process environment
    variables, so relying on _build_env alone is not enough: a user's global
    ``~/.claude/settings.json`` (e.g. one that pins CLAUDE_CODE_USE_BEDROCK=1)
    would override our routing and the request would hit Bedrock, which rejects
    the local model id with a 400. Passing --settings overrides that global
    file, so we always supply one.

    Uses the configured ``settings_file`` when set; otherwise synthesizes an
    inline JSON settings object that pins routing at the config's endpoint.

    Args:
        config: The runner config.

    Returns:
        Either a settings file path or an inline JSON settings string.
    """
    if config.settings_file:
        return str(REPO_ROOT / config.settings_file)
    if config.is_bedrock:
        # Bedrock mode authenticates with ambient AWS credentials, so no token
        # source is needed. Pin CLAUDE_CODE_USE_BEDROCK=1 (and the region) here
        # too, so a global settings file cannot flip routing back off Bedrock.
        env: dict[str, str] = {
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "DISABLE_NON_ESSENTIAL_MODEL_CALLS": "1",
            "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(config.max_output_tokens),
            "CLAUDE_CODE_SUBAGENT_MODEL": config.model,
        }
        region = config.resolved_region()
        if region:
            env["AWS_REGION"] = region
        return json.dumps({"env": env})
    settings = {
        # Claude Code requires a token source even against a local endpoint that
        # ignores the value; without it the run fails with "Not logged in".
        "apiKeyHelper": f"echo {config.api_key}",
        "env": {
            "CLAUDE_CODE_USE_BEDROCK": "0",
            "ANTHROPIC_BASE_URL": config.endpoint,
            "ANTHROPIC_API_KEY": config.api_key,
            "DISABLE_NON_ESSENTIAL_MODEL_CALLS": "1",
            "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(config.max_output_tokens),
            "CLAUDE_CODE_SUBAGENT_MODEL": config.model,
        },
    }
    return json.dumps(settings)


def _build_claude_cmd(
    config: RunnerConfig,
    prompt: str,
    stream: bool = False,
    clone_path: Path | None = None,
) -> list[str]:
    """Assemble the `claude -p` argument vector from the runner config.

    Args:
        config: The runner config.
        prompt: The /swe prompt to run.
        stream: If True, emit newline-delimited JSON events as the run
            progresses (``--output-format stream-json``, which requires
            ``--verbose``) instead of a single buffered JSON result.
        clone_path: The task's cloned repo directory. When set, it is added as
            an allowed working directory with ``--add-dir`` so Bash commands can
            operate inside the clone. Read/Glob/Grep already reach absolute paths
            regardless; without this, Bash (ls/cd/find/grep into the clone) is
            blocked because it is sandboxed to the harness's own working dir.

    Returns:
        The command as a list of arguments (never a shell string).
    """
    output_format = "stream-json" if stream else "json"
    cmd = [
        "claude",
        "-p",
        prompt,
        "--model",
        config.model,
        "--output-format",
        output_format,
        "--permission-mode",
        config.permission_mode,
        "--allowedTools",
        ",".join(config.allowed_tools),
        "--max-turns",
        str(config.max_turns),
        "--settings",
        _build_settings_arg(config),
    ]
    if clone_path is not None:
        cmd += ["--add-dir", str(clone_path)]
    if stream:
        # stream-json in -p mode requires --verbose to emit per-event objects.
        cmd.append("--verbose")
    return cmd


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string with a trailing Z.

    Returns:
        The timestamp, e.g. ``2026-07-22T20:41:03.512874Z``.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _metrics_from_result(result: dict[str, Any], elapsed: float) -> dict[str, Any]:
    """Extract the six benchmark metrics from a claude -p JSON result.

    Args:
        result: The parsed JSON result object from `claude -p`.
        elapsed: Wall-clock seconds measured around the subprocess call.

    Returns:
        A metrics dictionary keyed by the dataset's metric names.
    """
    usage = result.get("usage") or {}
    duration_ms = result.get("duration_ms")
    latency = round(duration_ms / 1000, 1) if duration_ms else round(elapsed, 1)
    is_error = result.get("is_error", False)
    metrics = {
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "latency_seconds": latency,
        "num_turns": result.get("num_turns", 0),
        "total_cost_usd": result.get("total_cost_usd"),
        "is_error": is_error,
        "session_id": result.get("session_id"),
    }
    # Only report cache-token fields the backend actually returned. vLLM's
    # Anthropic-compatible route does not emit these, so against vLLM they are
    # absent here rather than a misleading 0. (Real prefix-cache utilization is
    # captured separately, from vLLM's Prometheus /metrics; see the
    # "vllm_prometheus" block in the saved record.) They ARE populated against
    # backends that report them, such as Amazon Bedrock or the Anthropic API.
    if "cache_read_input_tokens" in usage:
        metrics["cache_read_tokens"] = usage["cache_read_input_tokens"]
    if "cache_creation_input_tokens" in usage:
        metrics["cache_creation_tokens"] = usage["cache_creation_input_tokens"]
    # Streaming-only: peak running estimate of extended-thinking tokens for
    # reasoning models. output_tokens already includes these; this records the
    # thinking portion the model streamed as system/thinking_tokens events.
    thinking = result.get("_thinking_tokens_estimate")
    if thinking:
        metrics["thinking_tokens_estimate"] = thinking
    # Capture the error message so failures are diagnosable from metrics.json
    # without re-running the task by hand.
    if is_error:
        metrics["error"] = str(result.get("result", ""))[:1000]
        metrics["api_error_status"] = result.get("api_error_status")
    return metrics


def _parse_prometheus_counter(text: str, metric: str) -> float | None:
    """Sum the values of a Prometheus counter across all its label sets.

    vLLM exposes one series per (engine, model_name); we sum them so a
    multi-engine server still yields a single total.

    Args:
        text: The raw text body of a Prometheus /metrics scrape.
        metric: The metric name to sum (e.g. "vllm:prefix_cache_hits_total").

    Returns:
        The summed counter value, or None if the metric is absent.
    """
    return _parse_prometheus_metrics(text)[1].get(metric)


def _parse_prometheus_metrics(
    text: str,
) -> tuple[dict[str, str], dict[str, float]]:
    """Parse a Prometheus scrape into family types and summed sample values.

    Handles the whole ``vllm:`` surface, not one metric: it reads the ``# TYPE``
    declarations to learn each family's type (counter/gauge/histogram) and sums
    every concrete sample across its label sets, so a multi-engine server still
    yields one total per sample. ``_bucket`` lines are skipped -- histogram means
    are derived from ``_sum``/``_count``, and raw buckets would only bloat output.

    Args:
        text: The raw text body of a Prometheus /metrics scrape.

    Returns:
        A ``(types, samples)`` pair. ``types`` maps each ``vllm:`` family name to
        its declared type. ``samples`` maps each concrete sample name (including
        ``_sum``/``_count`` suffixes) to its value summed across all label sets.
    """
    types: dict[str, str] = {}
    samples: dict[str, float] = {}
    for line in text.splitlines():
        if line.startswith("# TYPE "):
            parts = line.split()
            if len(parts) >= 4 and parts[2].startswith(VLLM_METRIC_PREFIX):
                types[parts[2]] = parts[3]
            continue
        if line.startswith("#") or not line.startswith(VLLM_METRIC_PREFIX):
            continue
        name = line.partition("{")[0].split()[0]
        if name.endswith("_bucket"):
            continue  # Raw histogram buckets; means come from _sum/_count.
        try:
            value = float(line.rsplit(None, 1)[-1])
        except ValueError:
            continue
        samples[name] = samples.get(name, 0.0) + value
    return types, samples


def _snapshot_vllm_metrics(endpoint: str) -> dict[str, Any] | None:
    """Scrape vLLM's full Prometheus /metrics surface into a comparable snapshot.

    LOUD CAVEAT -- read before trusting the numbers this feeds into metrics.json:
    these vLLM metrics are SERVER-WIDE and CUMULATIVE. They aggregate every
    request from every client since the server started and carry no per-request
    or per-session label. The per-run figures the harness derives are the DELTA
    of these across a single task's window, so they are correct ONLY when that
    benchmark task is the sole traffic hitting the endpoint during its run. Any
    concurrent request (another task, a manual curl, a second claude -p, a
    dashboard) is wrongly attributed to this run. The harness runs tasks
    serially, so a run does not contend with itself, but do not run anything else
    against the endpoint while a benchmark is going.

    Args:
        endpoint: The base URL of the vLLM server (e.g. http://127.0.0.1:8000).

    Returns:
        A ``{"types": ..., "samples": ...}`` snapshot, or None if the endpoint is
        unreachable or exposes no ``vllm:`` metrics (e.g. a non-vLLM backend).
    """
    url = endpoint.rstrip("/") + "/metrics"
    try:
        response = requests.get(url, timeout=METRICS_SCRAPE_TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.debug("Could not scrape %s for vLLM metrics: %s", url, exc)
        return None
    types, samples = _parse_prometheus_metrics(response.text)
    if not samples:
        logger.debug("Endpoint %s did not expose any vLLM metrics", url)
        return None
    return {"types": types, "samples": samples}


class _GaugePoller:
    """Poll vLLM gauges in a background thread while a run is in flight.

    Gauges (e.g. ``kv_cache_usage_perc``) are point-in-time readings: a
    before/after snapshot taken around the run reads them idle, because the KV
    cache drains once the request completes. This poller samples them every
    ``GAUGE_POLL_INTERVAL_SECONDS`` while claude -p runs, so peak and mean reflect
    what actually happened DURING the run -- matching what the vLLM server log
    prints for an in-flight request.

    Use as a context manager around the claude -p call:

        with _GaugePoller(endpoint) as poller:
            result = _run_claude(...)
        summary = poller.summary()

    The peak still carries the single-tenant caveat: on a shared endpoint it
    reflects total server load, not this run alone.
    """

    def __init__(self, endpoint: str | None) -> None:
        # A None endpoint (e.g. provider=bedrock) disables polling: the thread
        # never starts and summary() reports the gauges as unavailable.
        self._url = endpoint.rstrip("/") + "/metrics" if endpoint else None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        # Per-metric list of sampled values (only successful scrapes recorded).
        self._samples: dict[str, list[float]] = {m: [] for m in SAMPLED_GAUGE_METRICS}

    def __enter__(self) -> _GaugePoller:
        if self._url:
            self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._url:
            self._thread.join(timeout=METRICS_SCRAPE_TIMEOUT_SECONDS)

    def _run(self) -> None:
        # Sample immediately, then every interval until stopped. wait() returns
        # True when the stop event is set, giving a prompt, drift-free exit.
        while True:
            self._sample_once()
            if self._stop.wait(GAUGE_POLL_INTERVAL_SECONDS):
                return

    def _sample_once(self) -> None:
        try:
            response = requests.get(self._url, timeout=METRICS_SCRAPE_TIMEOUT_SECONDS)
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.debug("Gauge poll of %s failed: %s", self._url, exc)
            return
        _, samples = _parse_prometheus_metrics(response.text)
        for metric in SAMPLED_GAUGE_METRICS:
            if metric in samples:
                self._samples[metric].append(samples[metric])

    def summary(self) -> dict[str, Any]:
        """Return per-gauge peak/mean/sample-count, or an unavailable marker.

        Returns:
            A dict describing the sampled gauges. ``available`` is False when no
            gauge was ever successfully sampled (endpoint unreachable or not
            vLLM). Otherwise each sampled gauge maps to ``{"peak", "mean",
            "samples"}``, with peak/mean None for a gauge the endpoint did not
            expose.
        """
        if not any(self._samples.values()):
            return {
                "available": False,
                "source": "vllm_prometheus_poll",
                "note": (
                    "No vLLM gauges were sampled during the run; endpoint "
                    "unreachable or not a vLLM server."
                ),
                "interval_seconds": GAUGE_POLL_INTERVAL_SECONDS,
                "gauges": {},
            }
        gauges: dict[str, Any] = {}
        for metric, values in self._samples.items():
            if values:
                gauges[metric] = {
                    "peak": round(max(values), 4),
                    "mean": round(sum(values) / len(values), 4),
                    "samples": len(values),
                }
            else:
                gauges[metric] = {"peak": None, "mean": None, "samples": 0}
        return {
            "available": True,
            "source": "vllm_prometheus_poll",
            "note": (
                "Peak/mean of gauges sampled every "
                f"{GAUGE_POLL_INTERVAL_SECONDS}s while the run was in flight. "
                "Peak still reflects total server load under the single-tenant "
                "assumption, not this run in isolation."
            ),
            "interval_seconds": GAUGE_POLL_INTERVAL_SECONDS,
            "gauges": gauges,
        }


def _sample_delta(
    before: dict[str, float], after: dict[str, float], name: str
) -> float | None:
    """Return the non-negative window delta of one sample, or None if absent.

    A sample missing from either snapshot yields None rather than 0, so the block
    distinguishes "the endpoint does not expose this" from "it happened zero
    times during the run".
    """
    if name not in before or name not in after:
        return None
    return max(0.0, after[name] - before[name])


def _num(value: float | None) -> int | float | None:
    """Render a metric value as an int when whole, else rounded, preserving None."""
    if value is None:
        return None
    return int(value) if float(value).is_integer() else round(value, 4)


def _rate(numerator: float | None, denominator: float | None) -> float | None:
    """Return numerator/denominator rounded to 4 dp, or None if not computable."""
    if numerator is None or not denominator:
        return None
    return round(numerator / denominator, 4)


def _vllm_metrics(
    before: dict[str, Any] | None, after: dict[str, Any] | None
) -> dict[str, Any]:
    """Derive the nested vLLM Prometheus block for a run -- the FULL surface.

    This block is kept SEPARATE from the top-level metrics on purpose: the
    top-level fields report only what the model API returned per request,
    whereas these numbers come from a different source and a different method --
    a window delta of vLLM's SERVER-WIDE, CUMULATIVE Prometheus metrics. See
    _snapshot_vllm_metrics for the single-tenant caveat: each delta equals this
    run's activity only when the run is the sole traffic on the endpoint.

    Every ``vllm:`` metric is reported (duplicates of the top-level API numbers
    included) under its own type-named group, so the source is unambiguous:

    - ``counters``: window delta of each counter (e.g. generation/prompt tokens,
      prefix-cache queries/hits, preemptions, request successes).
    - ``histograms``: per-family ``count``/``sum`` window deltas plus the derived
      window ``mean`` (e.g. mean TTFT, mean end-to-end latency).
    - ``gauges``: an instantaneous post-run reading. Gauges are point-in-time, so
      between serial tasks they typically read idle (0); continuous sampling
      (the DuckDB collector) is the way to capture peaks.
    - ``derived``: the headline cache hit rates computed from the counters. In
      vLLM v1 a prefix-cache hit IS a KV-cache hit -- there is no separate KV-hit
      counter, and vLLM does not publish the rate itself.

    ``_created`` timestamp series are dropped as noise; histogram ``_bucket``
    lines are omitted (means come from ``_sum``/``_count``).

    Args:
        before: Snapshot taken immediately before the claude -p call.
        after: Snapshot taken immediately after it.

    Returns:
        A nested dict describing the run's vLLM-side activity. When snapshots are
        missing (endpoint does not expose /metrics), ``available`` is False and
        the groups are empty.
    """
    unavailable_note = (
        "vLLM metrics were not reachable; run against a vLLM endpoint exposing "
        "/metrics to populate this block."
    )
    available_note = (
        "Window delta of server-wide vLLM metrics; accurate only if this run was "
        "the sole traffic on the endpoint during its execution. Gauges are an "
        "instantaneous post-run reading and typically read idle between tasks."
    )
    if before is None or after is None:
        return {
            "available": False,
            "source": "vllm_prometheus_window",
            "note": unavailable_note,
            "derived": {
                "prefix_cache_hit_rate": None,
                "prompt_tokens_cached_rate": None,
            },
            "counters": {},
            "histograms": {},
            "gauges": {},
        }
    types: dict[str, str] = after["types"]
    before_s: dict[str, float] = before["samples"]
    after_s: dict[str, float] = after["samples"]
    counters: dict[str, Any] = {}
    histograms: dict[str, Any] = {}
    gauges: dict[str, Any] = {}
    for family, mtype in sorted(types.items()):
        if family.endswith("_created"):
            continue  # Prometheus per-series creation timestamps; pure noise.
        if mtype == "counter":
            counters[family] = _num(_sample_delta(before_s, after_s, family))
        elif mtype == "histogram":
            dcount = _sample_delta(before_s, after_s, family + "_count")
            dsum = _sample_delta(before_s, after_s, family + "_sum")
            histograms[family] = {
                "count": _num(dcount),
                "sum": _num(dsum),
                "mean": round(dsum / dcount, 6)
                if dcount and dsum is not None
                else None,
            }
        elif mtype == "gauge":
            gauges[family] = _num(after_s.get(family))
    return {
        "available": True,
        # Windowed deltas of server-wide metrics (single-tenant assumption), NOT
        # per-request accounting from the model API response.
        "source": "vllm_prometheus_window",
        "note": available_note,
        "derived": {
            "prefix_cache_hit_rate": _rate(
                _sample_delta(before_s, after_s, PREFIX_CACHE_HITS_METRIC),
                _sample_delta(before_s, after_s, PREFIX_CACHE_QUERIES_METRIC),
            ),
            "prompt_tokens_cached_rate": _rate(
                _sample_delta(before_s, after_s, PROMPT_TOKENS_CACHED_METRIC),
                _sample_delta(before_s, after_s, PROMPT_TOKENS_METRIC),
            ),
        },
        "counters": counters,
        "histograms": histograms,
        "gauges": gauges,
    }


def _mark_aggregate(vllm_block: dict[str, Any]) -> None:
    """Annotate a vLLM block in place as a concurrency-aggregated measurement.

    Called only when the run overlapped other tasks (concurrency > 1). The window
    deltas still measure REAL server/GPU activity -- they are not junk -- but they
    are server-wide aggregates over a window shared by every concurrent task, not
    this run in isolation: ratios (hit rates) become the aggregate rate across all
    overlapping runs, while absolute counts (token/request deltas) are summed
    across them, so each of the N overlapping files carries a near-identical,
    inflated figure. The per-run API fields in metrics_that_matter are unaffected
    and stay correct. No-op when the block is unavailable.

    Args:
        vllm_block: The dict returned by _vllm_metrics, mutated in place.
    """
    if not vllm_block.get("available"):
        return
    vllm_block["single_tenant"] = False
    vllm_block["note"] = (
        "AGGREGATE (concurrency > 1): server-wide window deltas over a period "
        "shared by other in-flight tasks. Ratios are the real aggregate rate "
        "across all overlapping runs; absolute counts are summed across them "
        "(inflated and near-duplicated per file). Not isolated to this run. Use "
        "concurrency 1 for per-run vLLM metrics. " + vllm_block["note"]
    )


def _run_claude(cmd: list[str], env: dict[str, str], timeout: int) -> dict[str, Any]:
    """Run `claude -p` and parse its JSON result.

    Args:
        cmd: The command argument vector.
        env: Environment for the subprocess.
        timeout: Wall-clock timeout in seconds.

    Returns:
        The parsed JSON result object.

    Raises:
        RuntimeError: If claude times out, exits nonzero, or emits no JSON.
    """
    start = time.time()
    try:
        proc = subprocess.run(  # nosec B603 - hardcoded 'claude', list args, no shell
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"claude -p timed out after {timeout}s") from exc
    elapsed = time.time() - start

    if not proc.stdout.strip():
        raise RuntimeError(
            f"claude -p produced no output (exit {proc.returncode}): "
            f"{proc.stderr.strip()[:500]}"
        )
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"claude -p output was not JSON: {proc.stdout.strip()[:500]}"
        ) from exc
    result["_elapsed_seconds"] = round(elapsed, 1)
    return result


TOOL_RESULT_PREVIEW_CHARS = 500


def _tool_result_text(content: Any) -> str:
    """Flatten a tool_result block's content into plain text.

    The Anthropic message format allows a tool_result's ``content`` to be either
    a plain string or a list of content blocks (each a ``{"type": "text",
    "text": ...}`` mapping, though other block types may appear). This joins the
    text it can find so the trace can show what a tool actually returned.

    Args:
        content: The ``content`` field of a tool_result block.

    Returns:
        The extracted text, stripped. Empty when no text could be found.
    """
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        texts = [
            block["text"]
            for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        ]
        return "\n".join(texts).strip()
    return ""


def _truncate(text: str, limit: int, verbose: bool) -> str:
    """Return text as-is when verbose, else truncated to limit with a marker.

    Args:
        text: The text to (maybe) truncate.
        limit: Max characters to keep when not verbose.
        verbose: When True, never truncate.

    Returns:
        The full text (verbose) or a truncated preview with a "+N chars" tail.
    """
    if verbose or len(text) <= limit:
        return text
    return f"{text[:limit]}... (+{len(text) - limit} chars)"


def _format_stream_event(event: dict[str, Any], verbose: bool = False) -> str | None:
    """Render one stream-json event as a human-readable trace line.

    Args:
        event: A single parsed event object from `--output-format stream-json`.
        verbose: When True, print assistant text and tool results in full
            instead of truncating them (thinking is always shown in full).

    Returns:
        A summary to print, or None for events not worth showing.
    """
    etype = event.get("type")
    if etype == "system":
        subtype = event.get("subtype", "")
        # Reasoning models (e.g. Kimi K2 Thinking) stream a running estimate of
        # extended-thinking tokens as system/thinking_tokens events. Surface the
        # count instead of a bare, repeated subtype line.
        if subtype == "thinking_tokens":
            est = event.get("estimated_tokens")
            return f"[system] thinking ~{est:,} tokens" if est is not None else None
        return f"[system] {subtype}".rstrip()
    if etype == "result":
        return None  # The caller logs the final result separately.
    if etype not in ("assistant", "user"):
        return None
    blocks = (event.get("message") or {}).get("content") or []
    parts: list[str] = []
    for block in blocks:
        btype = block.get("type")
        if btype == "text" and block.get("text", "").strip():
            parts.append(f"[{etype}] {_truncate(block['text'].strip(), 200, verbose)}")
        elif btype == "thinking" and block.get("thinking", "").strip():
            # Print the full reasoning trace, not a preview: for reasoning models
            # the thinking is the interesting signal, and truncating it hides why
            # a run stalled or how it reached a decision.
            parts.append(f"[{etype}:thinking] {block['thinking'].strip()}")
        elif btype == "tool_use":
            # In verbose mode also show the tool's input arguments, so a blocked
            # or surprising command is fully visible in the trace.
            line = f"[tool] {block.get('name', '?')}"
            if verbose and block.get("input"):
                line += f" {json.dumps(block['input'], default=str)}"
            parts.append(line)
        elif btype == "tool_result":
            text = _tool_result_text(block.get("content"))
            preview = _truncate(text, TOOL_RESULT_PREVIEW_CHARS, verbose)
            marker = "[tool_result:error]" if block.get("is_error") else "[tool_result]"
            parts.append(f"{marker} {preview}" if preview else marker)
    return "\n".join(parts) if parts else None


def _run_claude_streaming(
    cmd: list[str], env: dict[str, str], timeout: int, verbose: bool = False
) -> dict[str, Any]:
    """Run `claude -p` in streaming mode, printing a live trace.

    Reads newline-delimited JSON events as they arrive, prints a short summary
    of each, and returns the final ``result`` event (the same shape
    _metrics_from_result consumes).

    Args:
        cmd: The command argument vector (must include stream-json/--verbose).
        env: Environment for the subprocess.
        timeout: Wall-clock timeout in seconds.
        verbose: When True, print assistant text and tool results in full
            instead of truncating them in the live trace.

    Returns:
        The parsed final result event.

    Raises:
        RuntimeError: If claude times out or never emits a result event.
    """
    start = time.time()
    proc = subprocess.Popen(  # nosec B603 - hardcoded 'claude', list args, no shell
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    final: dict[str, Any] | None = None
    thinking_tokens = 0
    if proc.stdout is None:  # pragma: no cover - stdout is always a pipe here
        raise RuntimeError("claude -p produced no stdout stream")
    try:
        for line in proc.stdout:
            if time.time() - start > timeout:
                proc.kill()
                raise RuntimeError(f"claude -p timed out after {timeout}s")
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue  # Non-JSON progress noise; skip.
            if event.get("type") == "result":
                final = event
            else:
                # Track the peak running estimate of extended-thinking tokens
                # emitted by reasoning models; the result event does not report
                # it separately, so we carry it over from the stream.
                if event.get("subtype") == "thinking_tokens":
                    est = event.get("estimated_tokens")
                    if isinstance(est, int):
                        thinking_tokens = max(thinking_tokens, est)
                trace = _format_stream_event(event, verbose=verbose)
                if trace:
                    logger.info("  %s", trace)
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        raise RuntimeError(f"claude -p timed out after {timeout}s") from exc

    if final is None:
        stderr = (proc.stderr.read() if proc.stderr else "").strip()
        raise RuntimeError(
            f"claude -p emitted no result event (exit {proc.returncode}): "
            f"{stderr[:500]}"
        )
    final["_elapsed_seconds"] = round(time.time() - start, 1)
    # Only present when the model streamed thinking_tokens events; buffered
    # (non-streaming) runs never see these, so the field stays absent there.
    if thinking_tokens:
        final["_thinking_tokens_estimate"] = thinking_tokens
    return final


def _artifact_dir(config: RunnerConfig, task: Task) -> Path:
    """Return the directory where /swe writes a task's artifacts.

    Mirrors the skill's convention:
    ``benchmarks/<output_dir>/<model>/<repo-name>/<task-id>/``.

    Args:
        config: The runner config.
        task: The task being run.

    Returns:
        The absolute artifact directory path.
    """
    return (
        REPO_ROOT
        / "benchmarks"
        / config.output_dir
        / config.model_slug
        / _repo_name(task.repo)
        / task.id
    )


def _summary_metrics(
    metrics: dict[str, Any],
    vllm_prometheus: dict[str, Any],
    generation_tokens_per_sec: float,
    include_vllm: bool = True,
) -> dict[str, Any]:
    """Build the headline "metrics that matter" block for a run.

    This is a curated, source-resolved summary: for each metric it picks the best
    available number and records where it came from, so a reader never has to
    know whether a value lives in the top-level API fields or the nested
    ``vllm_prometheus`` block. Cache tokens prefer the model API when it reports
    them (Amazon Bedrock, the Anthropic API) and fall back to vLLM's server-side
    counters when it does not (vLLM's Anthropic route omits per-request cache
    fields). Everything sourced from ``vllm_prometheus`` inherits its single-
    tenant caveat.

    KV-cache utilization is intentionally NOT a headline metric: on a serial,
    single-tenant benchmark it barely varies (it tracks one request's working set
    as a fraction of the pool, not anything the benchmark controls), so it has no
    power to discriminate between runs. The sampled peak/mean still lives in
    ``vllm_prometheus.gauges_sampled`` as capacity telemetry -- useful alongside
    ``num_preemptions`` to judge whether a run was memory-clean, and it becomes a
    headline concern only under concurrent load.

    Args:
        metrics: The API-reported metrics from _metrics_from_result.
        vllm_prometheus: The nested vLLM Prometheus block from _vllm_metrics.
        generation_tokens_per_sec: Output-token throughput (output_tokens /
            latency_seconds), computed once by the caller so it matches the
            top-level field.
        include_vllm: When False (e.g. provider=bedrock, which has no vLLM
            server), the vLLM-derived cache fallbacks and the prefix-cache-hit
            headline are omitted, so the summary reports only what the model API
            returned.

    Returns:
        A flat summary dict of headline numbers plus a ``sources`` map naming the
        provenance of each. Values are None when no source could supply them.
    """
    counters = vllm_prometheus.get("counters", {})
    derived = vllm_prometheus.get("derived", {})
    prompt_total = counters.get(PROMPT_TOKENS_METRIC)
    prompt_cached = counters.get(PROMPT_TOKENS_CACHED_METRIC)

    # Cache-read: the API number if the backend reported it, else (only when a
    # vLLM server backs the run) vLLM's cached prompt tokens. Cache-write has no
    # direct vLLM counter; the freshly computed (uncached) prefill tokens are the
    # closest equivalent.
    if "cache_read_tokens" in metrics:
        cache_read: int | None = metrics["cache_read_tokens"]
        cache_read_src = "claude_api.usage.cache_read_input_tokens"
    elif include_vllm:
        cache_read = prompt_cached
        cache_read_src = f"vllm_prometheus.counters.{PROMPT_TOKENS_CACHED_METRIC}"
    else:
        cache_read = None
        cache_read_src = "claude_api.usage.cache_read_input_tokens (not reported)"
    if "cache_creation_tokens" in metrics:
        cache_write: int | None = metrics["cache_creation_tokens"]
        cache_write_src = "claude_api.usage.cache_creation_input_tokens"
    elif include_vllm and prompt_total is not None and prompt_cached is not None:
        cache_write = prompt_total - prompt_cached
        cache_write_src = (
            f"vllm_prometheus derived: {PROMPT_TOKENS_METRIC} - "
            f"{PROMPT_TOKENS_CACHED_METRIC} (uncached prefill tokens)"
        )
    else:
        cache_write = None
        cache_write_src = "unavailable (backend reports no cache-write signal)"

    note = (
        "Headline metrics resolved to the best available source for each; see "
        "'sources'. Values drawn from vllm_prometheus carry its single-tenant, "
        "server-wide caveat."
        if include_vllm
        else (
            "Headline metrics as reported by the model API (claude -p); there is "
            "no vLLM server for this provider, so no server-side cache telemetry."
        )
    )
    summary = {
        "note": note,
        "input_tokens": metrics.get("input_tokens"),
        "output_tokens": metrics.get("output_tokens"),
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "latency_seconds": metrics.get("latency_seconds"),
        "num_turns": metrics.get("num_turns"),
        "generation_tokens_per_sec": generation_tokens_per_sec,
    }
    sources = {
        "input_tokens": "claude_api.usage.input_tokens",
        "output_tokens": "claude_api.usage.output_tokens",
        "cache_read_tokens": cache_read_src,
        "cache_write_tokens": cache_write_src,
        "latency_seconds": "harness wall-clock (or claude_api.duration_ms)",
        "num_turns": "claude_api.num_turns",
        "generation_tokens_per_sec": "derived: output_tokens / latency_seconds",
    }
    # The prefix-cache hit rate is a vLLM-only signal; omit it entirely rather
    # than emit a permanently-null field for a provider that has no vLLM server.
    if include_vllm:
        summary["prefix_cache_hit_rate"] = derived.get("prefix_cache_hit_rate")
        sources["prefix_cache_hit_rate"] = (
            "vllm_prometheus.derived.prefix_cache_hit_rate"
        )
    summary["sources"] = sources
    return summary


def _save_metrics(
    config: RunnerConfig,
    task: Task,
    ref: str,
    metrics: dict[str, Any],
    vllm_prometheus: dict[str, Any],
) -> Path:
    """Write the run metrics to metrics.json in the artifact directory.

    The top-level fields report what the model API returned for the run
    (tokens, latency, turns, cost) plus the harness-observed UTC wall-clock
    bounds (``run_started_at`` / ``run_ended_at``). For provider=endpoint, cache
    utilization measured out-of-band from vLLM's Prometheus /metrics is kept in
    its own ``vllm_prometheus`` block, so the two sources -- and their different
    accuracy assumptions -- never mix. For provider=bedrock there is no vLLM
    server, so that block is omitted entirely and the run is limited to what
    claude -p itself reports.

    Args:
        config: The runner config.
        task: The task that was run.
        ref: The git ref used.
        metrics: The API-reported metrics from _metrics_from_result.
        vllm_prometheus: The nested vLLM Prometheus block from _vllm_metrics.

    Returns:
        Path to the written metrics.json.
    """
    out_dir = _artifact_dir(config, task)
    out_dir.mkdir(parents=True, exist_ok=True)
    produced = [f for f in ARTIFACT_FILENAMES if (out_dir / f).exists()]
    latency = metrics["latency_seconds"] or 0
    generation_tokens_per_sec = (
        round(metrics["output_tokens"] / latency, 1) if latency > 0 else 0
    )
    include_vllm = not config.is_bedrock
    record = {
        "task": task.id,
        "repo": task.repo,
        "ref": ref,
        "complexity": task.complexity,
        "tags": task.tags,
        "model": config.model,
        "model_slug": config.model_slug,
        "provider": config.provider,
        "endpoint": config.endpoint if not config.is_bedrock else None,
        "aws_region": config.resolved_region() if config.is_bedrock else None,
        "artifacts_produced": len(produced),
        "artifacts_expected": len(ARTIFACT_FILENAMES),
        "generation_tokens_per_sec": generation_tokens_per_sec,
        "metrics_that_matter": _summary_metrics(
            metrics, vllm_prometheus, generation_tokens_per_sec, include_vllm
        ),
        **metrics,
    }
    # vLLM Prometheus telemetry only exists for an HTTP endpoint; omit the block
    # for Amazon Bedrock rather than write a permanently-unavailable stub.
    if include_vllm:
        record["vllm_prometheus"] = vllm_prometheus
    path = out_dir / "metrics.json"
    path.write_text(json.dumps(record, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def _run_task(
    config: RunnerConfig,
    dataset: Dataset,
    task: Task,
    stream: bool = False,
    concurrent: bool = False,
    position: int = 1,
    total: int = 1,
    verbose: bool = False,
) -> dict[str, Any]:
    """Run a single task end to end and return its outcome summary.

    Args:
        config: The runner config.
        dataset: The loaded dataset (for default-ref resolution).
        task: The task to run.
        stream: If True, print a live event trace while claude -p runs.
        concurrent: True when other tasks may run on the endpoint at the same
            time (concurrency > 1). The single-tenant assumption behind the
            window-delta metrics no longer holds, so the vLLM block is annotated
            as a server-wide aggregate (ratios blended, absolute counts summed
            across the overlapping runs) rather than passed off as per-run.
        position: This task's 1-based position in the run (for legible logs).
        total: Total number of tasks in the run.
        verbose: When True (and streaming), print assistant text and tool
            results in full instead of truncating them in the live trace.

    Returns:
        A summary dict: task id, ok flag, artifacts produced, and metrics.
    """
    ref = dataset.resolved_ref(task)
    label = f"[task={task.id}] {position} of {total}"
    logger.info("=== %s [%s] ref=%s ===", label, task.complexity, ref)

    clone_path = _clone_repo(task, ref, config.clone_dir, log_prefix=label)
    clone_parent = clone_path.parent
    try:
        prompt = _build_prompt(task, clone_path, ref, config.model_slug)
        cmd = _build_claude_cmd(config, prompt, stream=stream, clone_path=clone_path)
        env = _build_env(config)
        logger.info("  %s Running claude -p (max_turns=%s)...", label, config.max_turns)
        # vLLM Prometheus scraping only applies to an HTTP endpoint; Amazon
        # Bedrock exposes no such surface, so skip it and leave the block marked
        # unavailable. metrics_endpoint is None for provider=bedrock.
        metrics_endpoint = config.endpoint if not config.is_bedrock else None
        # Snapshot vLLM's full server-wide metrics surface as tightly around the
        # claude -p call as possible. Each delta is this run's activity ONLY if
        # the run is the sole traffic on the endpoint (see _snapshot_vllm_metrics).
        # Keep the reads adjacent to the call to minimize the window in which
        # other traffic could be misattributed.
        vllm_before = (
            _snapshot_vllm_metrics(metrics_endpoint) if metrics_endpoint else None
        )
        # Gauges (KV-cache usage, running/waiting requests) drain to idle the
        # moment a request finishes, so a before/after snapshot always reads them
        # at ~0. Sample them in a background thread WHILE claude -p runs to capture
        # the in-flight peak/mean instead.
        # Wall-clock UTC bounds of the run, captured as tightly around the
        # claude -p call as the metric snapshots. ISO 8601 with a trailing Z.
        run_started_at = _utc_now_iso()
        with _GaugePoller(metrics_endpoint) as poller:
            if stream:
                result = _run_claude_streaming(
                    cmd, env, config.timeout_seconds, verbose=verbose
                )
            else:
                result = _run_claude(cmd, env, config.timeout_seconds)
        run_ended_at = _utc_now_iso()
        vllm_after = (
            _snapshot_vllm_metrics(metrics_endpoint) if metrics_endpoint else None
        )
        metrics = _metrics_from_result(result, result.get("_elapsed_seconds", 0))
        metrics["run_started_at"] = run_started_at
        metrics["run_ended_at"] = run_ended_at
        vllm_block = _vllm_metrics(vllm_before, vllm_after)
        vllm_block["gauges_sampled"] = poller.summary()
        if concurrent:
            _mark_aggregate(vllm_block)
    finally:
        shutil.rmtree(clone_parent, ignore_errors=True)

    metrics_path = _save_metrics(config, task, ref, metrics, vllm_block)
    out_dir = metrics_path.parent
    produced = [f for f in ARTIFACT_FILENAMES if (out_dir / f).exists()]
    ok = len(produced) == len(ARTIFACT_FILENAMES) and not metrics["is_error"]

    # One-line outcome banner: artifacts, turns, tokens, latency, and (when
    # available) the vLLM prefix-cache hit rate for the run's window.
    cache_suffix = ""
    hit_rate = vllm_block.get("derived", {}).get("prefix_cache_hit_rate")
    if hit_rate is not None:
        queries = vllm_block["counters"].get(PREFIX_CACHE_QUERIES_METRIC)
        hits = vllm_block["counters"].get(PREFIX_CACHE_HITS_METRIC)
        scope = "aggregate" if concurrent else "single-tenant"
        cache_suffix = (
            f", prefix cache {hit_rate * 100:.1f}% hit "
            f"({hits:,}/{queries:,} tokens, {scope})"
            if hits is not None and queries is not None
            else f", prefix cache {hit_rate * 100:.1f}% hit ({scope})"
        )
    thinking_suffix = ""
    if metrics.get("thinking_tokens_estimate"):
        thinking_suffix = f" (~{metrics['thinking_tokens_estimate']:,} thinking)"
    summary = (
        f"{label} | {'OK' if ok else 'INCOMPLETE'}: "
        f"{len(produced)}/{len(ARTIFACT_FILENAMES)} artifacts, "
        f"{metrics['num_turns']} turns, "
        f"{metrics['input_tokens']:,} in / {metrics['output_tokens']:,} out{thinking_suffix} tokens, "
        f"{metrics['latency_seconds']}s{cache_suffix}"
    )
    banner = "=" * len(summary)
    logger.info(banner)
    logger.info(summary)
    logger.info(banner)
    if metrics["is_error"]:
        logger.error(
            "  claude -p reported an error (status %s): %s",
            metrics.get("api_error_status"),
            metrics.get("error"),
        )
    logger.info("  Metrics: %s", metrics_path)
    return {"task": task.id, "ok": ok, "artifacts": len(produced), "metrics": metrics}


def _select_tasks(dataset: Dataset, task_ids: list[str], count: int = 0) -> list[Task]:
    """Select tasks to run, preserving dataset order.

    Args:
        dataset: The loaded dataset.
        task_ids: Task ids to run; empty means all tasks.
        count: Keep only the first ``count`` selected tasks; 0 means no limit.

    Returns:
        The tasks to run.

    Raises:
        DatasetError: If a requested id is not in the dataset or count is negative.
    """
    if count < 0:
        raise DatasetError(
            f"--count must be 0 (all) or a positive integer, got {count}"
        )
    if not task_ids:
        selected = dataset.tasks
    else:
        known = {t.id for t in dataset.tasks}
        missing = [tid for tid in task_ids if tid not in known]
        if missing:
            raise DatasetError(
                f"Unknown task ids: {missing}. Available: {sorted(known)}"
            )
        selected = [t for t in dataset.tasks if t.id in set(task_ids)]
    return selected[:count] if count else selected


def _dry_run(config: RunnerConfig, dataset: Dataset, tasks: list[Task]) -> None:
    """Print the prompt and command for each task without executing anything."""
    for task in tasks:
        ref = dataset.resolved_ref(task)
        placeholder = Path(config.clone_dir) / "<tmp>" / _repo_name(task.repo)
        prompt = _build_prompt(task, placeholder, ref, config.model_slug)
        cmd = _build_claude_cmd(config, prompt, clone_path=placeholder)
        print(f"\n=== {task.id} [{task.complexity}] ref={ref} ===")
        print("PROMPT:")
        print(prompt)
        print("\nCOMMAND:")
        print(" ".join(cmd))


def _run_task_safe(
    config: RunnerConfig,
    dataset: Dataset,
    task: Task,
    stream: bool,
    concurrent: bool,
    position: int,
    total: int,
    verbose: bool = False,
) -> dict[str, Any]:
    """Run one task, converting a RuntimeError into a failed-summary dict.

    Used as the unit of work for both the serial loop and the thread pool so a
    single task's failure never aborts the whole run.
    """
    try:
        return _run_task(
            config,
            dataset,
            task,
            stream=stream,
            concurrent=concurrent,
            position=position,
            total=total,
            verbose=verbose,
        )
    except RuntimeError:
        logger.exception("[task=%s] %s of %s failed", task.id, position, total)
        return {"task": task.id, "ok": False, "artifacts": 0}


def _run(
    config: RunnerConfig,
    dataset: Dataset,
    tasks: list[Task],
    stream: bool = False,
    verbose: bool = False,
) -> None:
    """Run every selected task and log a final pass/fail summary.

    Tasks run serially when ``config.concurrency`` is 1 (the default) and in a
    thread pool of that width otherwise. Concurrency > 1 overlaps runs on the
    endpoint, which invalidates the single-tenant vLLM window-delta metrics; the
    per-run blocks are flagged unreliable and a warning is logged here.
    """
    concurrency = max(1, min(config.concurrency, len(tasks)))
    target = (
        f"Amazon Bedrock ({config.resolved_region()})"
        if config.is_bedrock
        else config.endpoint
    )
    logger.info(
        "Running %s task(s) with model=%s against %s (concurrency=%s)",
        len(tasks),
        config.model,
        target,
        concurrency,
    )
    if concurrency > 1:
        logger.warning(
            "Concurrency is %s: per-run API metrics (tokens, latency, turns) stay "
            "correct, but the vllm_prometheus block becomes a server-wide "
            "AGGREGATE over the shared window (ratios blended across runs, "
            "absolute counts summed). Use concurrency 1 for per-run vLLM metrics.",
            concurrency,
        )

    total = len(tasks)
    if concurrency == 1:
        summaries = [
            _run_task_safe(
                config,
                dataset,
                task,
                stream,
                False,
                position=i,
                total=total,
                verbose=verbose,
            )
            for i, task in enumerate(tasks, start=1)
        ]
    else:
        summaries = _run_concurrent(config, dataset, tasks, stream, concurrency)

    passed = sum(1 for s in summaries if s["ok"])
    logger.info("=" * 60)
    logger.info("Done: %s/%s tasks produced all artifacts.", passed, len(summaries))
    for s in summaries:
        logger.info(
            "  %s %s (%s artifacts)",
            "OK " if s["ok"] else "FAIL",
            s["task"],
            s["artifacts"],
        )


def _run_concurrent(
    config: RunnerConfig,
    dataset: Dataset,
    tasks: list[Task],
    stream: bool,
    concurrency: int,
) -> list[dict[str, Any]]:
    """Run tasks in a thread pool of the given width, preserving task order.

    Each task clones into its own temp dir and writes to a distinct artifact
    dir, and claude -p runs as an independent subprocess, so the work is safe to
    parallelize. Streaming is disabled here because interleaved event traces from
    concurrent tasks are unreadable.

    Returns:
        Summary dicts in the same order as ``tasks``.
    """
    if stream:
        logger.warning("Disabling --stream under concurrency; traces would interleave.")
    total = len(tasks)
    results: list[dict[str, Any]] = [{} for _ in tasks]
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        future_to_index = {
            executor.submit(
                _run_task_safe, config, dataset, task, False, True, index + 1, total
            ): index
            for index, task in enumerate(tasks)
        }
        for future in as_completed(future_to_index):
            index = future_to_index[future]
            results[index] = future.result()
    return results


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    CLI flags override the corresponding runner-config fields.
    """
    parser = argparse.ArgumentParser(
        description="Run the SWE benchmark headless via claude -p and the /swe skill.",
        epilog=(
            "Examples:\n"
            "  uv run scripts/run-swe-headless.py --config config/runner.example.yaml\n"
            "  uv run scripts/run-swe-headless.py --config config/runner.example.yaml "
            "--model qwen3-coder-30b --tasks remove-faiss,remove-efs-from-terraform-aws-ecs\n"
            "  uv run scripts/run-swe-headless.py --config config/runner.example.yaml --dry-run"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", help="Path to the runner config YAML file")
    parser.add_argument(
        "--provider",
        help="Override: routing provider ('endpoint' for a base URL, 'bedrock' "
        "for native Amazon Bedrock)",
    )
    parser.add_argument("--endpoint", help="Override: API endpoint base URL")
    parser.add_argument("--model", help="Override: model name")
    parser.add_argument(
        "--aws-region",
        help="Override: AWS region for provider=bedrock (e.g. us-east-1)",
    )
    parser.add_argument("--dataset", help="Override: dataset YAML path")
    parser.add_argument(
        "--tasks", help="Override: comma-separated task ids to run (default: all)"
    )
    parser.add_argument(
        "--count",
        type=int,
        default=0,
        help="Run only the first N selected tasks (default: 0 = all)",
    )
    parser.add_argument("--max-turns", type=int, help="Override: cap on the agent loop")
    parser.add_argument(
        "--concurrency",
        type=int,
        help="Override: how many tasks to run at once (default 1 = serial). "
        "Values above 1 invalidate the single-tenant vLLM metrics.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print prompts/commands without running"
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Print a live event trace as each task runs (uses stream-json)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="With --stream, print assistant text and tool results in full "
        "instead of truncating them in the live trace",
    )
    return parser.parse_args()


def main() -> None:
    """Parse arguments, load config and dataset, and run the benchmark."""
    args = _parse_args()
    overrides: dict[str, Any] = {
        "provider": args.provider,
        "endpoint": args.endpoint,
        "model": args.model,
        "aws_region": args.aws_region,
        "dataset": args.dataset,
        "max_turns": args.max_turns,
        "concurrency": args.concurrency,
    }
    if args.tasks:
        overrides["tasks"] = [t.strip() for t in args.tasks.split(",") if t.strip()]

    try:
        config = load_runner_config(args.config, overrides)
    except RunnerConfigError as exc:
        logger.error("Config error: %s", exc)
        sys.exit(1)

    dataset_path = config.dataset
    if not Path(dataset_path).is_absolute():
        dataset_path = str(Path(__file__).resolve().parent.parent / dataset_path)
    try:
        dataset = load_dataset(dataset_path)
        tasks = _select_tasks(dataset, config.tasks, args.count)
    except DatasetError as exc:
        logger.error("Dataset error: %s", exc)
        sys.exit(1)

    if args.dry_run:
        _dry_run(config, dataset, tasks)
        return
    if args.verbose and not args.stream:
        logger.warning("--verbose has no effect without --stream; ignoring it.")
    _run(config, dataset, tasks, stream=args.stream, verbose=args.verbose)


if __name__ == "__main__":
    main()
