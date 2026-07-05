#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# opencode-setup.sh — Install opencode (if missing) and point it at the local
#                     vLLM server as a custom OpenAI-compatible provider.
#
# opencode (https://opencode.ai) is a terminal coding agent. Here it drives the
# self-hosted vLLM model instead of a cloud API — the agentic counterpart to
# the raw inference clients in ../clients/.
#
# The script is idempotent:
#   - if `opencode` is already installed, it is NOT reinstalled
#   - the provider config is written to ~/.config/opencode/opencode.json,
#     backing up any existing file first
#
# Usage:
#   ./opencode-setup.sh              # install (if needed) + write config
#   ./opencode-setup.sh --launch     # ... then start an interactive session
#   ./opencode-setup.sh --check      # report install + config state, do nothing
#   ./opencode-setup.sh --help
#
# Environment variables (all optional):
#   SERVED_NAME   model id the vLLM server exposes   (default: qwen3-coder-30b)
#   PORT          vLLM API port                       (default: 8000)
#   MODEL_LABEL   human label shown in the opencode UI
#                 (default: "Qwen3-Coder-30B-A3B (vLLM, 4xL40S)")
#   CONTEXT       context-window limit for the model  (default: 32768)
#   OUTPUT_LIMIT  max output tokens                    (default: 8192)
#   OPENCODE_CONFIG  path to opencode config          (default: ~/.config/opencode/opencode.json)
#
# Prereq: a vLLM server serving SERVED_NAME on localhost:PORT WITH tool calling
# enabled (vllm-serve.sh does this by default). opencode is agentic and sends
# tool_choice=auto; a server without --enable-auto-tool-choice will reject it.
# ---------------------------------------------------------------------------

SERVED_NAME="${SERVED_NAME:-qwen3-coder-30b}"
PORT="${PORT:-8000}"
MODEL_LABEL="${MODEL_LABEL:-Qwen3-Coder-30B-A3B (vLLM, 4xL40S)}"
CONTEXT="${CONTEXT:-32768}"
OUTPUT_LIMIT="${OUTPUT_LIMIT:-8192}"
OPENCODE_CONFIG="${OPENCODE_CONFIG:-$HOME/.config/opencode/opencode.json}"
OPENCODE_BIN_DIR="$HOME/.opencode/bin"

RED='\033[0;31m'; GREEN='\033[0;32m'; BLUE='\033[0;34m'; YELLOW='\033[0;33m'; BOLD='\033[1m'; RESET='\033[0m'
info()   { echo -e "${BLUE}[info]${RESET}  $1"; }
ok()     { echo -e "${GREEN}[ok]${RESET}    $1"; }
warn()   { echo -e "${YELLOW}[warn]${RESET}  $1"; }
fail()   { echo -e "${RED}[fail]${RESET}  $1"; exit 1; }

usage() {
  sed -n '5,34p' "$0" | sed 's/^# \{0,1\}//; s/^#$//'
  exit 0
}

ACTION="setup"
case "${1:-}" in
  -h|--help)   usage ;;
  --launch)    ACTION="launch" ;;
  --check)     ACTION="check" ;;
  "")          ACTION="setup" ;;
  *)           fail "Unknown option: $1 (try --help)" ;;
esac

# Resolve the opencode binary whether or not it's on PATH.
find_opencode() {
  command -v opencode 2>/dev/null && return 0
  [[ -x "$OPENCODE_BIN_DIR/opencode" ]] && { echo "$OPENCODE_BIN_DIR/opencode"; return 0; }
  return 1
}

# ---- --check: report state and exit -------------------------------------
if [[ "$ACTION" == "check" ]]; then
  if OC=$(find_opencode); then
    ok "opencode installed: $OC ($("$OC" --version 2>/dev/null | head -1))"
  else
    warn "opencode not installed."
  fi
  if [[ -f "$OPENCODE_CONFIG" ]]; then
    ok "config present: $OPENCODE_CONFIG"
  else
    warn "no config at $OPENCODE_CONFIG"
  fi
  exit 0
fi

# ---- Step 1: install opencode only if missing ---------------------------
if OC=$(find_opencode); then
  ok "opencode already installed: $OC ($("$OC" --version 2>/dev/null | head -1)) — skipping install"
else
  info "opencode not found — installing from https://opencode.ai/install ..."
  curl -fsSL https://opencode.ai/install | bash
  OC=$(find_opencode) || fail "Install ran but opencode binary not found. Check ~/.opencode/bin."
  ok "opencode installed: $OC ($("$OC" --version 2>/dev/null | head -1))"
fi

# Warn if the bin dir isn't on PATH (installer adds it to shell rc, not this shell).
if ! command -v opencode >/dev/null 2>&1; then
  warn "$OPENCODE_BIN_DIR is not on your PATH in this shell."
  warn "Add it:  export PATH=\"\$HOME/.opencode/bin:\$PATH\"  (or open a new shell)"
fi

# ---- Step 2: write the vLLM provider config -----------------------------
mkdir -p "$(dirname "$OPENCODE_CONFIG")"
if [[ -f "$OPENCODE_CONFIG" ]]; then
  BACKUP="${OPENCODE_CONFIG}.backup.$(date +%s 2>/dev/null || echo bak)"
  cp "$OPENCODE_CONFIG" "$BACKUP"
  info "Backed up existing config → $BACKUP"
fi

cat > "$OPENCODE_CONFIG" <<EOF
{
  "\$schema": "https://opencode.ai/config.json",
  "model": "vllm/${SERVED_NAME}",
  "small_model": "vllm/${SERVED_NAME}",
  "provider": {
    "vllm": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "vLLM (self-hosted, local)",
      "options": {
        "baseURL": "http://localhost:${PORT}/v1",
        "apiKey": "not-needed"
      },
      "models": {
        "${SERVED_NAME}": {
          "name": "${MODEL_LABEL}",
          "limit": { "context": ${CONTEXT}, "output": ${OUTPUT_LIMIT} }
        }
      }
    }
  }
}
EOF
ok "Wrote opencode config → $OPENCODE_CONFIG"
info "Provider: vllm  |  model: vllm/${SERVED_NAME}  |  endpoint: http://localhost:${PORT}/v1"

# ---- Step 3: sanity-check the vLLM server -------------------------------
if curl -sf "http://localhost:${PORT}/v1/models" >/dev/null 2>&1; then
  ok "vLLM server reachable on localhost:${PORT}"
else
  warn "vLLM server NOT reachable on localhost:${PORT}. Start it: ./vllm-serve.sh"
fi

echo ""
if [[ "$ACTION" == "launch" ]]; then
  info "Launching opencode (backed by vllm/${SERVED_NAME})..."
  exec "$OC"
else
  echo "Next steps:"
  echo "  export PATH=\"\$HOME/.opencode/bin:\$PATH\""
  echo "  opencode                                  # interactive TUI"
  echo "  opencode run \"explain this repo\"          # one-shot"
  echo "  opencode models vllm                      # confirm the provider is wired"
fi
