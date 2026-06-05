#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# setup-proxy.sh — Install and start the LiteLLM proxy for Amazon Bedrock
#
# This proxy translates the Anthropic Messages API (Claude Code) to the
# OpenAI Chat Completions API (Amazon Bedrock — all 38 third-party models).
#
# Backend: bedrock-mantle.us-east-1.api.aws (NOT bedrock-runtime)
# Auth: Bearer token generated from IAM credentials (12h validity)
#
# Anthropic models (Claude) do NOT need this proxy — use CLAUDE_CODE_USE_BEDROCK=1 directly.
#
# Prerequisites:
#   - Python 3.9+
#   - AWS credentials configured (aws configure / IAM role / SSO)
#   - Bedrock model access enabled in your AWS account
#
# Usage:
#   ./scripts/setup-proxy.sh              # install + start on port 4000
#   ./scripts/setup-proxy.sh --port 8080  # custom port
#   ./scripts/setup-proxy.sh --stop       # stop running proxy
#   ./scripts/setup-proxy.sh --refresh    # refresh Bedrock token (no restart)
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CONFIG_FILE="$PROJECT_DIR/config/litellm-config.yaml"
DEFAULT_HOST="127.0.0.1"
DEFAULT_PORT=4000
PID_FILE="$PROJECT_DIR/.litellm.pid"
TOKEN_FILE="$PROJECT_DIR/.mantle-token"
MANTLE_REGION="us-east-1"

usage() {
    echo "Usage: $0 [--host HOST] [--port PORT] [--stop] [--status] [--refresh]"
    echo ""
    echo "Options:"
    echo "  --host HOST   Address to bind on (default: $DEFAULT_HOST). Use a"
    echo "                non-loopback address (e.g. a VPC private IP) only if"
    echo "                you need clients on other hosts to reach the proxy."
    echo "  --port PORT   Port to run proxy on (default: $DEFAULT_PORT)"
    echo "  --stop        Stop running proxy"
    echo "  --status      Check if proxy is running"
    echo "  --refresh     Refresh Bedrock bearer token without restarting"
    exit 1
}

HOST=$DEFAULT_HOST
PORT=$DEFAULT_PORT
ACTION="start"

while [[ $# -gt 0 ]]; do
    case $1 in
        --host)    HOST="$2"; shift 2 ;;
        --port)    PORT="$2"; shift 2 ;;
        --stop)    ACTION="stop"; shift ;;
        --status)  ACTION="status"; shift ;;
        --refresh) ACTION="refresh"; shift ;;
        -h|--help) usage ;;
        *) echo "[error] Unknown option: $1"; usage ;;
    esac
done

stop_proxy() {
    if [[ -f "$PID_FILE" ]]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID"
            rm -f "$PID_FILE"
            echo "[stopped] LiteLLM proxy (PID $PID)"
        else
            rm -f "$PID_FILE"
            echo "[info] Proxy was not running (stale PID file cleaned)"
        fi
    else
        echo "[info] No proxy running"
    fi
}

check_status() {
    if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "[running] LiteLLM proxy PID $(cat "$PID_FILE")"
        curl -sf "http://localhost:${PORT}/health" 2>/dev/null && echo " - health: OK" || echo " - health: unreachable"
    else
        echo "[stopped] No proxy running"
    fi
    if [[ -f "$TOKEN_FILE" ]]; then
        local age_sec=$(( $(date +%s) - $(stat -f%m "$TOKEN_FILE" 2>/dev/null || stat -c%Y "$TOKEN_FILE" 2>/dev/null) ))
        local age_hr=$(( age_sec / 3600 ))
        echo "[token] Age: ${age_hr}h (valid for 12h, refresh with --refresh)"
    else
        echo "[token] No token file"
    fi
}

generate_token() {
    echo "[token] Generating Bedrock bearer token for $MANTLE_REGION..."

    if ! python3 -c "import aws_bedrock_token_generator" 2>/dev/null; then
        echo "[install] Installing aws-bedrock-token-generator..."
        python3 -m pip install aws-bedrock-token-generator --quiet
    fi

    local token
    token=$(AWS_REGION="$MANTLE_REGION" python3 -c "
import os
os.environ['AWS_REGION'] = '${MANTLE_REGION}'
from aws_bedrock_token_generator import provide_token
print(provide_token(region='${MANTLE_REGION}'))
")

    if [[ -z "$token" ]]; then
        echo "[error] Failed to generate Bedrock token. Check AWS credentials."
        exit 1
    fi

    export MANTLE_API_KEY="$token"
    echo "$token" > "$TOKEN_FILE"
    chmod 600 "$TOKEN_FILE"
    echo "[token] Bearer token generated (valid 12h)"
}

refresh_token() {
    generate_token
    echo "[done] Token refreshed. Proxy will use new token on next request."
    echo "       If the proxy is running, restart it: $0 --stop && $0"
}

start_proxy() {
    if ! command -v python3 &>/dev/null; then
        echo "[error] Python 3 is required. Install it first."
        exit 1
    fi

    # Check AWS credentials
    if ! aws sts get-caller-identity &>/dev/null 2>&1; then
        if ! python3 -c "import boto3; boto3.client('sts').get_caller_identity()" &>/dev/null 2>&1; then
            echo "[error] AWS credentials not configured."
            echo "        Run: aws configure, or attach an IAM instance profile with Bedrock access."
            exit 1
        fi
        echo "[info] Using IAM instance profile credentials"
    fi

    # Install litellm if not present
    if ! python3 -c "import litellm" 2>/dev/null; then
        echo "[install] Installing litellm[proxy]..."
        python3 -m pip install "litellm[proxy]" --quiet
    fi

    # Generate Bedrock bearer token
    generate_token

    # Stop existing proxy if running
    stop_proxy 2>/dev/null

    echo "[start] LiteLLM proxy on port $PORT"
    echo "[config] $CONFIG_FILE"
    echo "[backend] Amazon Bedrock (bedrock-mantle.$MANTLE_REGION.api.aws)"
    echo ""

    # Start in background with Bedrock token + routing fix exported
    export MANTLE_API_KEY
    export LITELLM_USE_CHAT_COMPLETIONS_URL_FOR_ANTHROPIC_MESSAGES=true
    # Default bind is 127.0.0.1; only override via --host if you have a real
    # need to expose the proxy beyond the local machine.
    if [[ "$HOST" != "127.0.0.1" && "$HOST" != "localhost" ]]; then
        echo "[warn] Binding to $HOST — the proxy will be reachable from any host that"
        echo "[warn] can reach $HOST:$PORT at the network layer. The proxy itself does"
        echo "[warn] NOT authenticate clients; rely on your security group / firewall."
    fi
    nohup litellm --config "$CONFIG_FILE" --host "$HOST" --port "$PORT" > "$PROJECT_DIR/.litellm.log" 2>&1 &
    echo $! > "$PID_FILE"

    # Wait for proxy to be ready
    echo -n "[wait] Proxy starting"
    for i in $(seq 1 15); do
        if curl -sf "http://localhost:${PORT}/health" &>/dev/null; then
            echo ""
            echo "[ready] Proxy running on http://${HOST}:${PORT}"
            echo "[pid] $(cat "$PID_FILE")"
            echo "[log] $PROJECT_DIR/.litellm.log"
            echo ""
            echo "Available models (38 via Bedrock):"
            curl -s "http://localhost:${PORT}/v1/models" | python3 -c "
import json, sys
data = json.load(sys.stdin).get('data', [])
for m in sorted(data, key=lambda x: x['id']):
    print(f'  - {m[\"id\"]}')
print(f'\nTotal: {len(data)} models')
" 2>/dev/null || echo "  (could not list models)"
            echo ""
            echo "Usage:"
            echo "  ANTHROPIC_BASE_URL=http://localhost:${PORT} \\"
            echo "  ANTHROPIC_API_KEY=bedrock-proxy \\"
            echo "  ANTHROPIC_MODEL=qwen-coder-next \\"
            echo "  DISABLE_PROMPT_CACHING=1 \\"
            echo "  CLAUDE_CODE_USE_BEDROCK=0 \\"
            echo "  claude"
            echo ""
            echo "  Or use the picker: ./scripts/claude-model.sh"
            return 0
        fi
        echo -n "."
        sleep 2
    done

    echo ""
    echo "[error] Proxy did not start in time. Check logs:"
    echo "        tail -f $PROJECT_DIR/.litellm.log"
    exit 1
}

case $ACTION in
    start)   start_proxy ;;
    stop)    stop_proxy ;;
    status)  check_status ;;
    refresh) refresh_token ;;
esac
