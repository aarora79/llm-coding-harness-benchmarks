#!/usr/bin/env python3
"""
HumanEval Benchmark Runner for Multi-Model Claude Code

Runs OpenAI's HumanEval (the most widely-cited code-generation benchmark, 164
tasks) through Claude Code backed by different models via the LiteLLM proxy,
then scores each completion with the standard pass@1 method: concatenate the
prompt + model completion + the task's unit tests, execute, and check it passes.

Unlike SWE-bench, there is no Docker, no repo cloning, and no patch application
-- each task is a single self-contained Python function, so the harness is fast
and deterministic.

Usage:
    python3 humaneval_runner.py --models us.anthropic.claude-sonnet-4-6,qwen.qwen3-coder-30b-a3b-instruct --tasks 20
    python3 humaneval_runner.py --models us.anthropic.claude-sonnet-4-6 --all
"""

import argparse
import csv
import json
import os
# Used to invoke `claude` and `python3` for the benchmark; shell=True is never used.
import subprocess  # nosec B404
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

try:
    from datasets import load_dataset
except ImportError:
    print("[error] datasets required: pip install datasets")
    sys.exit(1)


MODELS = [
    "us.anthropic.claude-sonnet-4-6",
    "qwen.qwen3-coder-next",
    "deepseek.v3.2",
    "moonshotai.kimi-k2.5",
    "qwen.qwen3-coder-30b-a3b-instruct",
]
PROXY_PORT = 4000
TIMEOUT_PER_TASK = 180        # 3 min: an agent loop on one small function
TEST_TIMEOUT = 30            # seconds to run the generated code + unit tests
RESULTS_DIR = Path(__file__).parent / "results" / f"humaneval_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
# Clean, empty Claude config dir so user-level settings.json can't hijack
# routing. Created lazily under a per-user temp dir on first use (see
# `_get_clean_config_dir`).
CLEAN_CONFIG_DIR: "str | None" = None
# Pin the HumanEval dataset to a known revision so the benchmark is reproducible.
HUMANEVAL_DATASET = "openai_humaneval"
HUMANEVAL_REVISION = "7dce6050a7d6d172f3cc5c32aa97f52fa1a2e544"
# Native Bedrock IDs always start with `anthropic.` (or `us.anthropic.` for
# cross-region inference profiles). Anything else routes through the proxy.
NATIVE_PREFIXES = ("anthropic.", "us.anthropic.")


def _get_clean_config_dir():
    """Return a per-user, isolated Claude config dir. Created lazily so we don't
    use a fixed world-writable /tmp path (which Bandit B108 flags)."""
    global CLEAN_CONFIG_DIR
    if CLEAN_CONFIG_DIR is None:
        CLEAN_CONFIG_DIR = tempfile.mkdtemp(prefix="claude_bench_config_")
    return CLEAN_CONFIG_DIR


def load_tasks(num_tasks=20, all_tasks=False):
    """Load HumanEval tasks pinned to a known revision so the benchmark is
    reproducible (a deterministic prefix is taken so every model sees the same
    set)."""
    ds = load_dataset(HUMANEVAL_DATASET, split="test", revision=HUMANEVAL_REVISION)
    tasks = list(ds)
    if all_tasks:
        return tasks
    return tasks[:num_tasks]


def build_prompt(task):
    """Ask Claude Code to complete the function stub. We require the answer in a
    fenced block so extraction is unambiguous."""
    return f"""Complete the following Python function. Implement the body so it
satisfies the docstring. Return ONLY the complete function (signature + body) in
a single ```python code block. Do not add explanations, examples, or tests.

Do NOT use any tools. Do NOT write to any file. Output the code as text only,
directly in your reply.

```python
{task['prompt']}```"""


def extract_code(output, entry_point):
    """Pull the function definition out of Claude Code's text output.

    Prefer a fenced ```python block; fall back to scanning for the def line."""
    code = None
    if "```" in output:
        # take the largest fenced block (most likely the full function)
        blocks = []
        parts = output.split("```")
        for i in range(1, len(parts), 2):
            block = parts[i]
            if block.startswith("python"):
                block = block[len("python"):]
            blocks.append(block.strip())
        # choose a block that defines the entry point if possible
        for b in blocks:
            if f"def {entry_point}" in b:
                code = b
                break
        if code is None and blocks:
            code = max(blocks, key=len)
    if code is None and "<parameter=content>" in output:
        # Some budget models emit a malformed tool call instead of a fenced
        # block (e.g. `<parameter=content> ...code... </parameter>`). Recover
        # the code payload so we score the model's logic, not its tool syntax.
        seg = output.split("<parameter=content>", 1)[1]
        seg = seg.split("</parameter>", 1)[0]
        if f"def {entry_point}" in seg:
            code = seg.strip()
    if code is None:
        # last resort: from the def line to end of output
        idx = output.find(f"def {entry_point}")
        if idx != -1:
            code = output[idx:]
    return code


def run_claude_code(task, model):
    """Run Claude Code (backed by `model`) on one HumanEval task in an isolated
    temp dir. Returns (completion_text, elapsed, status)."""
    prompt = build_prompt(task)
    is_native = model.startswith(NATIVE_PREFIXES)

    env = os.environ.copy()
    # Use a clean Claude config dir so any user-level settings.json (which may
    # pin ANTHROPIC_BASE_URL to a local Ollama, CLAUDE_CODE_USE_BEDROCK=0, etc.)
    # does NOT override the per-model routing below.
    env["CLAUDE_CONFIG_DIR"] = _get_clean_config_dir()
    # Drop any inherited overrides that would hijack routing.
    for k in ("ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
              "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL",
              "CLAUDE_CODE_SUBAGENT_MODEL"):
        env.pop(k, None)

    if is_native:
        env["CLAUDE_CODE_USE_BEDROCK"] = "1"
        env["AWS_REGION"] = "us-east-1"
        env.pop("ANTHROPIC_BASE_URL", None)
        env.pop("ANTHROPIC_API_KEY", None)
        env["ANTHROPIC_MODEL"] = model
    else:
        env["ANTHROPIC_BASE_URL"] = f"http://localhost:{PROXY_PORT}"
        env["ANTHROPIC_API_KEY"] = "bedrock-proxy"
        env["CLAUDE_CODE_USE_BEDROCK"] = "0"

    cmd = ["claude"]
    if not is_native:
        cmd.extend(["--model", model])
    # Read-only task: the model just needs to emit code as text. No file tools
    # required, so we keep the tool surface minimal and deterministic.
    cmd.extend(["-p", prompt])

    with tempfile.TemporaryDirectory(prefix="humaneval_") as work_dir:
        start = time.time()
        try:
            # `cmd` is built from constants and the model alias, not user
            # input; shell=True is intentionally NOT used.
            result = subprocess.run(  # nosec B603
                cmd, cwd=work_dir, env=env,
                capture_output=True, text=True, timeout=TIMEOUT_PER_TASK,
                check=False,
            )
            elapsed = time.time() - start
        except subprocess.TimeoutExpired:
            return None, TIMEOUT_PER_TASK, "timeout"

    output = result.stdout
    if not output.strip():
        return None, elapsed, "empty_output"
    return output, elapsed, "success"


def prompt_preamble(task):
    """Everything in the task prompt BEFORE the target function def: imports and
    any helper functions HumanEval defines above the entry point. We prepend
    this so the executed program has the same context the model was given (some
    tasks rely on a helper or `from typing import List`)."""
    prompt = task["prompt"]
    idx = prompt.find(f"def {task['entry_point']}")
    return prompt[:idx] if idx != -1 else ""


def evaluate(task, completion_code):
    """Standard HumanEval pass@1: run the completion + the task's test harness.

    We prepend the prompt preamble (imports + helper defs) so extraction that
    captured only the target function still has its required context."""
    if not completion_code:
        return False, "no_code"

    program = (
        prompt_preamble(task)
        + "\n\n"
        + completion_code
        + "\n\n"
        + task["test"]
        + "\n\n"
        + f"check({task['entry_point']})\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(program)
        path = f.name
    try:
        # `python3` resolved from PATH, args are a temp file path we just
        # wrote. shell=True is intentionally NOT used.
        proc = subprocess.run(  # nosec B603 B607
            ["python3", path], capture_output=True, text=True,
            timeout=TEST_TIMEOUT, check=False,
        )
        if proc.returncode == 0:
            return True, "passed"
        return False, (proc.stderr.strip().splitlines() or ["error"])[-1][:200]
    except subprocess.TimeoutExpired:
        return False, "exec_timeout"
    finally:
        os.unlink(path)


def main():
    parser = argparse.ArgumentParser(description="HumanEval runner for Multi-Model Claude Code")
    parser.add_argument("--models", default=",".join(MODELS), help="Comma-separated model list")
    parser.add_argument("--tasks", type=int, default=20, help="Number of tasks (deterministic prefix)")
    parser.add_argument("--all", action="store_true", help="Run all 164 tasks")
    args = parser.parse_args()

    models = args.models.split(",")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    tasks = load_tasks(num_tasks=args.tasks, all_tasks=args.all)

    print("=" * 60)
    print("HumanEval Benchmark for Multi-Model Claude Code")
    print("=" * 60)
    print(f"Models: {models}")
    print(f"Tasks:  {len(tasks)}")
    print(f"Output: {RESULTS_DIR}")
    print()

    results = []
    completions_dir = RESULTS_DIR / "completions"
    completions_dir.mkdir(exist_ok=True)

    for model in models:
        print(f"\n{'='*60}\nModel: {model}\n{'='*60}")
        passed = 0
        for i, task in enumerate(tasks):
            tid = task["task_id"]
            print(f"  [{i+1}/{len(tasks)}] {tid}", flush=True)
            output, elapsed, status = run_claude_code(task, model)

            code = extract_code(output, task["entry_point"]) if output else None
            ok, detail = evaluate(task, code)
            if ok:
                passed += 1

            # persist the raw completion for auditability
            (completions_dir / f"{model}__{tid.replace('/', '_')}.txt").write_text(output or "")

            print(f"      {'PASS' if ok else 'FAIL'} ({status}, {elapsed:.0f}s) {('' if ok else detail)}", flush=True)
            results.append({
                "model": model, "task_id": tid,
                "passed": ok, "status": status,
                "elapsed": round(elapsed, 1), "detail": detail,
            })
        rate = 100.0 * passed / len(tasks) if tasks else 0
        print(f"  -> {model}: {passed}/{len(tasks)} passed ({rate:.1f}%)")

    # raw results
    results_csv = RESULTS_DIR / "results.csv"
    with open(results_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["model", "task_id", "passed", "status", "elapsed", "detail"])
        w.writeheader()
        w.writerows(results)

    # summary
    print("\n" + "=" * 60)
    print("Summary (pass@1)")
    print("=" * 60)
    print(f"{'Model':<20} {'Pass@1':>10} {'Passed':>10} {'Avg Time':>10}")
    print("-" * 52)
    summary = []
    for model in models:
        mr = [r for r in results if r["model"] == model]
        p = sum(1 for r in mr if r["passed"])
        avg = sum(r["elapsed"] for r in mr) / len(mr) if mr else 0
        rate = 100.0 * p / len(mr) if mr else 0
        print(f"{model:<20} {rate:>9.1f}% {p:>4}/{len(mr):<4} {avg:>8.0f}s")
        summary.append({"model": model, "pass_at_1": round(rate, 1), "passed": p, "total": len(mr), "avg_time": round(avg, 1)})

    with open(RESULTS_DIR / "summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nResults: {results_csv}")
    print(f"Summary: {RESULTS_DIR / 'summary.json'}")


if __name__ == "__main__":
    main()
