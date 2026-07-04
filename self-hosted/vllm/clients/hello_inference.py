#!/usr/bin/env python3
"""Minimal inference client for a self-hosted vLLM endpoint.

Sends one chat completion to the OpenAI-compatible API vLLM exposes and prints
the reply plus token usage. This is the "does inference work end to end from
Python" smoke test that pairs with scripts/vllm-verify.sh (which uses curl).

Usage:
    # after `uv sync` in self-hosted/vllm/ (see README):
    uv run clients/hello_inference.py
    uv run clients/hello_inference.py --prompt "Write a bubble sort in Rust."
    BASE_URL=http://localhost:8000/v1 MODEL=qwen3-coder-30b uv run clients/hello_inference.py

Environment variables:
    BASE_URL   vLLM endpoint (default: http://127.0.0.1:8000/v1)
    MODEL      served-model-name to target (default: qwen3-coder-30b)
    API_KEY    ignored by vLLM, but the SDK requires a non-empty value
"""

import argparse
import os
import time

from openai import OpenAI

DEFAULT_PROMPT = "Write a Python function that returns the nth Fibonacci number."


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="user prompt to send")
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--stream", action="store_true", help="stream tokens as they arrive")
    args = parser.parse_args()

    base_url = os.environ.get("BASE_URL", "http://127.0.0.1:8000/v1")
    model = os.environ.get("MODEL", "qwen3-coder-30b")
    api_key = os.environ.get("API_KEY", "not-needed")  # vLLM ignores it; SDK needs non-empty

    client = OpenAI(base_url=base_url, api_key=api_key)

    print(f"→ endpoint: {base_url}")
    print(f"→ model:    {model}")
    print(f"→ prompt:   {args.prompt}\n")

    start = time.time()

    if args.stream:
        stream = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": args.prompt}],
            max_tokens=args.max_tokens,
            temperature=args.temperature,
            stream=True,
        )
        tokens = 0
        for chunk in stream:
            delta = chunk.choices[0].delta.content or ""
            print(delta, end="", flush=True)
            tokens += 1 if delta else 0
        elapsed = time.time() - start
        print(f"\n\n[streamed ~{tokens} chunks in {elapsed:.2f}s "
              f"≈ {tokens / elapsed:.1f} chunks/sec]")
        return

    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": args.prompt}],
        max_tokens=args.max_tokens,
        temperature=args.temperature,
    )
    elapsed = time.time() - start

    print("── reply ─────────────────────────────────────")
    print(resp.choices[0].message.content)
    print("──────────────────────────────────────────────")
    u = resp.usage
    print(f"prompt tokens:     {u.prompt_tokens}")
    print(f"completion tokens: {u.completion_tokens}")
    print(f"wall clock:        {elapsed:.2f}s")
    if u.completion_tokens and elapsed > 0:
        print(f"single-request:    {u.completion_tokens / elapsed:.1f} tokens/sec "
              f"(not the batched throughput number)")


if __name__ == "__main__":
    main()
