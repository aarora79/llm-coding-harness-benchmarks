#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# claude-local.sh — Launch Claude Code against a local vLLM server.
#
# Does NOT touch ~/.claude/settings.json. Overrides are env vars only.
# Uses --setting-sources local to skip user settings (which may force Bedrock).
# Uses ANTHROPIC_BASE_URL without /v1 — Claude Code appends /v1/messages itself.
#
# Usage:
#   ./claude-local.sh                         # interactive session
#   ./claude-local.sh -p "hello"              # one-shot prompt
#
# Environment variables:
#   HOST                      vLLM host (default: 127.0.0.1)
#   PORT                      vLLM port (default: 8000)
#   MODEL                     model name to display/send (auto-detected if unset)
#   MAX_OUTPUT_TOKENS         max output tokens (default: 16000)
# ---------------------------------------------------------------------------

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
MODEL="${MODEL:-qwen3.6-35b}"
MAX_OUTPUT_TOKENS="${MAX_OUTPUT_TOKENS:20000}"

fail() { echo "[error] $*" >&2; exit 1; }

command -v claude >/dev/null 2>&1 || fail "Claude Code not found on PATH."

curl -sf --max-time 5 "http://${HOST}:${PORT}/v1/models" >/dev/null 2>&1 \
  || fail "vLLM not reachable at http://${HOST}:${PORT}. Is the tunnel up?"

if [[ -z "$MODEL" ]]; then
  MODEL=$(curl -s "http://${HOST}:${PORT}/v1/models" | python3 -c '
import json, sys
models = json.load(sys.stdin).get("data", [])
real = [m["id"] for m in models if "claude" not in m["id"].lower()]
print(real[0] if real else models[0]["id"] if models else "")
' 2>/dev/null || true)
fi

[[ -n "$MODEL" ]] || fail "Could not detect model from http://${HOST}:${PORT}/v1/models"

echo "┌─────────────────────────────────────────────────┐"
echo "│  Model: ${MODEL}"
echo "│  Endpoint: http://${HOST}:${PORT}"
echo "│  Max output tokens: ${MAX_OUTPUT_TOKENS}"
echo "└─────────────────────────────────────────────────┘"
echo ""

export ANTHROPIC_BASE_URL="http://${HOST}:${PORT}"
export ANTHROPIC_API_KEY="local"
export CLAUDE_CODE_USE_BEDROCK="0"
export CLAUDE_CODE_MAX_OUTPUT_TOKENS="${MAX_OUTPUT_TOKENS}"
export DISABLE_NON_ESSENTIAL_MODEL_CALLS="1"

CLAUDE_ARGS=(--model "$MODEL" --setting-sources local,project)
CLAUDE_ARGS+=(--settings '{"apiKeyHelper":"echo sk-local-vllm"}')
CLAUDE_ARGS+=(--append-system-prompt "CRITICAL: Never output thinking, reasoning, or internal monologue. Never use <think> tags or narrate what you are about to do. Act directly — call tools, write text, ask questions. No metacommentary.")

exec claude "${CLAUDE_ARGS[@]}" "$@"
