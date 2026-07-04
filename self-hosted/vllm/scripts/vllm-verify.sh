#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# vllm-verify.sh — Confirm the vLLM server is up and inference works.
#
# Hits the OpenAI-compatible endpoint three ways:
#   1. GET  /v1/models          — server is up, which model is served
#   2. POST /v1/chat/completions — a real generation round-trip
#   3. Reports tokens and a rough tokens/sec from the response usage block
#
# Usage:
#   ./vllm-verify.sh                 # default localhost:8000
#   PORT=8000 ./vllm-verify.sh
#   HOST=127.0.0.1 PORT=8000 ./vllm-verify.sh
# ---------------------------------------------------------------------------

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
BASE="http://${HOST}:${PORT}"

RED='\033[0;31m'; GREEN='\033[0;32m'; BLUE='\033[0;34m'; BOLD='\033[1m'; RESET='\033[0m'
info()   { echo -e "${BLUE}[info]${RESET}  $1"; }
ok()     { echo -e "${GREEN}[ok]${RESET}    $1"; }
fail()   { echo -e "${RED}[fail]${RESET}  $1"; exit 1; }
header() { echo -e "\n${BOLD}=== $1 ===${RESET}"; }

header "1. Server reachable? ($BASE)"
MODELS_JSON=$(curl -sf "$BASE/v1/models" 2>/dev/null) || fail "No response from $BASE/v1/models. Is the server up? (./vllm-serve.sh)"
MODEL_ID=$(echo "$MODELS_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['data'][0]['id'])" 2>/dev/null || echo "")
[[ -n "$MODEL_ID" ]] || fail "Server responded but no model listed. Check server logs."
ok "Serving model: $MODEL_ID"

header "2. Chat completion round-trip"
info "Prompt: \"Write a Python one-liner that reverses a string.\""
START=$(python3 -c "import time; print(time.time())")
RESP=$(curl -sf "$BASE/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d "{
    \"model\": \"$MODEL_ID\",
    \"messages\": [{\"role\": \"user\", \"content\": \"Write a Python one-liner that reverses a string. Reply with only the code.\"}],
    \"max_tokens\": 128,
    \"temperature\": 0
  }" 2>/dev/null) || fail "chat/completions request failed"
END=$(python3 -c "import time; print(time.time())")

echo "$RESP" | python3 -c "
import json, sys
d = json.load(sys.stdin)
msg = d['choices'][0]['message']['content'].strip()
usage = d.get('usage', {})
elapsed = $END - $START
out_tok = usage.get('completion_tokens', 0)
print('  ── model reply ─────────────────────────────')
for line in msg.splitlines():
    print('   ', line)
print('  ────────────────────────────────────────────')
print(f'  prompt tokens:     {usage.get(\"prompt_tokens\", \"?\")}')
print(f'  completion tokens: {out_tok}')
print(f'  wall clock:        {elapsed:.2f}s')
if out_tok and elapsed > 0:
    print(f'  approx gen speed:  {out_tok/elapsed:.1f} tokens/sec (single request; see the throughput harness for batched numbers)')
" || fail "could not parse chat response: $RESP"

ok "Inference works."
echo ""
echo "This is a single-request smoke test. For sustained tokens/sec under"
echo "concurrency — the number the cost model needs — use the throughput harness"
echo "(coming next), not this figure."
