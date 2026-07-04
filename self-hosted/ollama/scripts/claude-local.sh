#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# claude-local.sh — Launch Claude Code against a local model via Ollama
#
# Temporarily swaps ~/.claude/settings.json to point at localhost:$LOCAL_MODEL_PORT,
# then restores the original settings on exit (even on Ctrl+C or crash).
#
# Usage:
#   ./claude-local.sh              # interactive session
#   ./claude-local.sh -p "prompt"  # print mode
#
# Environment variables:
#   LOCAL_MODEL_PORT   Ollama port (default: 11434; use 8131 for llama.cpp)
# ---------------------------------------------------------------------------

LOCAL_MODEL_PORT="${LOCAL_MODEL_PORT:-11434}"
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
BACKUP_FILE="$HOME/.claude/settings.json.backup.$$"   # PID-scoped: safe for concurrent sessions

# Restrict file creation permissions — backup contains config data
umask 077

# Restore original settings on exit (even on Ctrl+C)
_restore() {
    if [[ -f "$BACKUP_FILE" ]]; then
        cp "$BACKUP_FILE" "$CLAUDE_SETTINGS"
        rm -f "$BACKUP_FILE"
        echo ""
        echo "[restored] Original settings restored."
    fi
}
trap _restore EXIT INT TERM

# Check model server is reachable
if ! curl -sf "http://localhost:${LOCAL_MODEL_PORT}/v1/models" >/dev/null 2>&1; then
    echo "[error] Model server not reachable on localhost:${LOCAL_MODEL_PORT}."
    echo "        Start tunnel: ./scripts/tunnel.sh start"
    exit 1
fi

# Detect model dynamically — skip any claude-* aliases, use the actual model
MODEL=$(curl -s "http://localhost:${LOCAL_MODEL_PORT}/v1/models" | python3 -c "
import json, sys
models = json.load(sys.stdin).get('data', [])
for m in models:
    if not m['id'].startswith('claude'):
        print(m['id']); break
else:
    print(models[0]['id']) if models else exit(1)
" 2>/dev/null)

if [[ -z "$MODEL" ]]; then
    echo "[error] Could not detect model from localhost:${LOCAL_MODEL_PORT}"
    exit 1
fi

# Backup current settings
cp "$CLAUDE_SETTINGS" "$BACKUP_FILE"
echo "[swap] Backed up original settings."

# Write local model settings with detected model name
cat > "$CLAUDE_SETTINGS" << EOF
{
  "\$schema": "https://json.schemastore.org/claude-code-settings.json",
  "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:${LOCAL_MODEL_PORT}",
    "ANTHROPIC_API_KEY": "local",
    "CLAUDE_CODE_USE_BEDROCK": "0",
    "ANTHROPIC_MODEL": "${MODEL}",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "${MODEL}",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "${MODEL}",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "${MODEL}",
    "CLAUDE_CODE_SUBAGENT_MODEL": "${MODEL}",
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "128000",
    "DISABLE_PROMPT_CACHING": "1",
    "DISABLE_AUTOUPDATER": "1",
    "DISABLE_TELEMETRY": "1",
    "DISABLE_ERROR_REPORTING": "1",
    "DISABLE_NON_ESSENTIAL_MODEL_CALLS": "1"
  },
  "permissions": {
    "allow": [
      "Bash(git *)", "Bash(npm *)", "Bash(npx *)", "Bash(node *)",
      "Bash(python *)", "Bash(python3 *)", "Bash(pip *)", "Bash(pip3 *)",
      "Bash(ls *)", "Bash(cat *)", "Bash(head *)", "Bash(tail *)",
      "Bash(find *)", "Bash(grep *)", "Bash(rg *)", "Bash(mkdir *)",
      "Bash(cp *)", "Bash(mv *)", "Bash(echo *)",
      "Bash(which *)", "Bash(pwd)", "Bash(wc *)", "Bash(sort *)",
      "Bash(diff *)", "Bash(touch *)",
      "Read", "Edit", "Write", "Glob", "Grep"
    ],
    "deny": [
      "Read(./.env)", "Read(./.env.*)", "Read(./secrets/**)"
    ]
  }
}
EOF

echo "[swap] Loaded model: ${MODEL}"
echo "[info] Starting Claude Code → ${MODEL} (localhost:${LOCAL_MODEL_PORT})"
echo ""

# Launch claude with all args passed through
claude "$@"
