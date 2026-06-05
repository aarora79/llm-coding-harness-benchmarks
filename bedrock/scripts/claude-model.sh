#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# claude-model.sh — Run Claude Code with any Bedrock model (raw model IDs)
#
# For Anthropic models: connects directly to Bedrock (no proxy needed)
# For third-party models: routes through LiteLLM proxy -> Amazon Bedrock
#
# All 38 third-party models support tools + streaming natively.
#
# Usage:
#   ./scripts/claude-model.sh                                                 # interactive picker
#   ./scripts/claude-model.sh --model qwen.qwen3-coder-next
#   ./scripts/claude-model.sh --model us.anthropic.claude-sonnet-4-6          # native Bedrock
#   ./scripts/claude-model.sh --model us.anthropic.claude-sonnet-4-6 -p "..."
#   ./scripts/claude-model.sh --list
#
# Environment:
#   PROXY_PORT       LiteLLM proxy port (default: 4000)
#   AWS_REGION       AWS region for Bedrock (default: us-east-1)
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PROXY_PORT="${PROXY_PORT:-4000}"
AWS_REGION="${AWS_REGION:-us-east-1}"

# ── Model Registry ────────────────────────────────────────────────
# Format: model_id|type|description
#   model_id: raw Bedrock model id (what is sent on the wire)
#   type    : "native" = direct Bedrock (Anthropic only)
#             "proxy"  = via LiteLLM -> Amazon Bedrock
MODELS=(
    # ── Anthropic (native — no proxy needed) ──────────────────────
    "us.anthropic.claude-opus-4-8|native|Claude Opus 4.8 — latest flagship as of June 5 2026"
    "us.anthropic.claude-opus-4-7|native|Claude Opus 4.7 — flagship"
    "us.anthropic.claude-opus-4-6-v1|native|Claude Opus 4.6 — flagship, strong reasoning"
    "us.anthropic.claude-sonnet-4-6|native|Claude Sonnet 4.6 — balanced speed/quality"
    "us.anthropic.claude-haiku-4-5-20251001-v1:0|native|Claude Haiku 4.5 — fast, lightweight"
    "us.anthropic.claude-opus-4-5-20251101-v1:0|native|Claude Opus 4.5 — previous gen flagship"
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0|native|Claude Sonnet 4.5 — previous gen balanced"

    # ── Qwen — Coding (via Bedrock) ────────────────────────────────
    "qwen.qwen3-coder-next|proxy|Qwen3 Coder Next — latest coding model as of June 5 2026"
    "qwen.qwen3-coder-480b-a35b-instruct|proxy|Qwen3 Coder 480B — largest coding MoE"
    "qwen.qwen3-coder-30b-a3b-instruct|proxy|Qwen3 Coder 30B — compact coding MoE"

    # ── Qwen — General / Vision (via Bedrock) ──────────────────────
    "qwen.qwen3-235b-a22b-2507|proxy|Qwen3 235B — general purpose MoE"
    "qwen.qwen3-32b|proxy|Qwen3 32B — dense, hybrid thinking"
    "qwen.qwen3-vl-235b-a22b-instruct|proxy|Qwen3 VL 235B — vision + language"
    "qwen.qwen3-next-80b-a3b-instruct|proxy|Qwen3 Next 80B — efficient MoE"

    # ── DeepSeek (via Bedrock) ─────────────────────────────────────
    "deepseek.v3.2|proxy|DeepSeek V3.2 — coding + reasoning MoE"
    "deepseek.v3.1|proxy|DeepSeek V3.1 — previous gen"

    # ── Mistral AI (via Bedrock) ───────────────────────────────────
    "mistral.devstral-2-123b|proxy|Devstral 2 123B — coding specialist"
    "mistral.mistral-large-3-675b-instruct|proxy|Mistral Large 3 675B — flagship MoE"
    "mistral.magistral-small-2509|proxy|Magistral Small — reasoning model"
    "mistral.ministral-3-14b-instruct|proxy|Ministral 14B — mid-size efficient"
    "mistral.ministral-3-8b-instruct|proxy|Ministral 8B — fast, lightweight"
    "mistral.ministral-3-3b-instruct|proxy|Ministral 3B — tiny, fastest"
    "mistral.voxtral-small-24b-2507|proxy|Voxtral Small 24B — multimodal"
    "mistral.voxtral-mini-3b-2507|proxy|Voxtral Mini 3B — tiny multimodal"

    # ── Moonshot AI / Kimi (via Bedrock) ───────────────────────────
    "moonshotai.kimi-k2.5|proxy|Kimi K2.5 — coding + reasoning"
    "moonshotai.kimi-k2-thinking|proxy|Kimi K2 Thinking — chain-of-thought"

    # ── MiniMax (via Bedrock) ──────────────────────────────────────
    "minimax.minimax-m2|proxy|MiniMax M2 — general purpose"
    "minimax.minimax-m2.1|proxy|MiniMax M2.1 — improved general"
    "minimax.minimax-m2.5|proxy|MiniMax M2.5 — latest as of June 5 2026, 80.2% SWE-bench (vendor claimed)"

    # ── NVIDIA Nemotron (via Bedrock) ──────────────────────────────
    "nvidia.nemotron-super-3-120b|proxy|Nemotron Super 120B — large reasoning"
    "nvidia.nemotron-nano-3-30b|proxy|Nemotron Nano 30B — mid-size"
    "nvidia.nemotron-nano-12b-v2|proxy|Nemotron Nano 12B — compact"
    "nvidia.nemotron-nano-9b-v2|proxy|Nemotron Nano 9B — smallest"

    # ── OpenAI GPT OSS (via Bedrock) ──────────────────────────────
    "openai.gpt-oss-120b|proxy|GPT OSS 120B — open-source GPT"
    "openai.gpt-oss-20b|proxy|GPT OSS 20B — compact open-source GPT"
    "openai.gpt-oss-safeguard-120b|proxy|GPT OSS Safeguard 120B"
    "openai.gpt-oss-safeguard-20b|proxy|GPT OSS Safeguard 20B"

    # ── Z.AI / GLM (via Bedrock) ──────────────────────────────────
    "zai.glm-5|proxy|GLM 5 — latest general model as of June 5 2026"
    "zai.glm-4.7|proxy|GLM 4.7 — strong reasoning"
    "zai.glm-4.7-flash|proxy|GLM 4.7 Flash — fast inference"
    "zai.glm-4.6|proxy|GLM 4.6 — previous gen"

    # ── Google Gemma (via Bedrock) ─────────────────────────────────
    "google.gemma-3-27b-it|proxy|Gemma 3 27B — open model, largest"
    "google.gemma-3-12b-it|proxy|Gemma 3 12B — open model, mid-size"
    "google.gemma-3-4b-it|proxy|Gemma 3 4B — open model, compact"

    # ── Writer / Palmyra (via Bedrock) ─────────────────────────────
    "writer.palmyra-vision-7b|proxy|Palmyra Vision 7B — vision model"
)

# ── Functions ─────────────────────────────────────────────────────

list_models() {
    echo ""
    echo "Available Models for Claude Code + Bedrock"
    echo "==========================================="
    echo ""
    echo "Backend: Amazon Bedrock (Chat Completions API for proxy models, Messages API for native)"
    echo ""
    printf "  %-46s %-8s %s\n" "MODEL ID" "TYPE" "DESCRIPTION"
    printf "  %-46s %-8s %s\n" "--------" "----" "-----------"

    for entry in "${MODELS[@]}"; do
        IFS='|' read -r model_id type desc <<< "$entry"
        printf "  %-46s %-8s %s\n" "$model_id" "$type" "$desc"
    done
    echo ""
    echo "native = direct Bedrock (no proxy needed, Anthropic models only)"
    echo "proxy  = via LiteLLM proxy -> Amazon Bedrock (start with: ./scripts/setup-proxy.sh)"
    echo ""
    echo "Total: ${#MODELS[@]} models (7 native + $((${#MODELS[@]} - 7)) via Bedrock)"
}

lookup_model() {
    local search="$1"
    for entry in "${MODELS[@]}"; do
        IFS='|' read -r model_id type desc <<< "$entry"
        if [[ "$model_id" == "$search" ]]; then
            echo "$model_id|$type|$desc"
            return 0
        fi
    done
    return 1
}

pick_model_interactive() {
    echo "" >&2
    echo "Select a model:" >&2
    echo "" >&2
    local i=1
    for entry in "${MODELS[@]}"; do
        IFS='|' read -r model_id type desc <<< "$entry"
        printf "  %2d) %-46s [%s] %s\n" "$i" "$model_id" "$type" "$desc" >&2
        ((i++))
    done
    echo "" >&2
    read -rp "Enter number (1-${#MODELS[@]}): " choice

    if [[ "$choice" -ge 1 && "$choice" -le "${#MODELS[@]}" ]]; then
        echo "${MODELS[$((choice-1))]}"
    else
        echo "[error] Invalid choice" >&2
        exit 1
    fi
}

check_proxy() {
    if ! curl -sf "http://localhost:${PROXY_PORT}/health" &>/dev/null; then
        echo "[error] LiteLLM proxy not running on port $PROXY_PORT"
        echo "        Start it: ./scripts/setup-proxy.sh"
        exit 1
    fi
}

# ── Parse args ────────────────────────────────────────────────────

MODEL_ID_ARG=""
CLAUDE_ARGS=()

while [[ $# -gt 0 ]]; do
    case $1 in
        --model|-m)  MODEL_ID_ARG="$2"; shift 2 ;;
        --list|-l)   list_models; exit 0 ;;
        -h|--help)
            echo "Usage: $0 [--model MODEL_ID] [--list] [claude args...]"
            echo "       $0 --model qwen.qwen3-coder-next -p 'write a function'"
            echo "       $0 --list"
            exit 0
            ;;
        *)  CLAUDE_ARGS+=("$1"); shift ;;
    esac
done

# Interactive selection if no model specified
if [[ -z "$MODEL_ID_ARG" ]]; then
    SELECTED=$(pick_model_interactive)
else
    SELECTED=$(lookup_model "$MODEL_ID_ARG") || {
        echo "[error] Unknown model: $MODEL_ID_ARG"
        echo "        Run: $0 --list"
        exit 1
    }
fi

IFS='|' read -r MODEL_ID TYPE DESC <<< "$SELECTED"
echo ""
echo "[model] $MODEL_ID — $DESC"

# ── Launch Claude Code ────────────────────────────────────────────

if [[ "$TYPE" == "native" ]]; then
    echo "[mode] Native Bedrock (no proxy)"
    echo ""
    CLAUDE_CODE_USE_BEDROCK=1 \
    AWS_REGION="$AWS_REGION" \
    ANTHROPIC_MODEL="$MODEL_ID" \
    claude ${CLAUDE_ARGS[@]+"${CLAUDE_ARGS[@]}"}

elif [[ "$TYPE" == "proxy" ]]; then
    check_proxy
    echo "[mode] LiteLLM proxy -> Amazon Bedrock (localhost:$PROXY_PORT)"
    echo ""
    ANTHROPIC_BASE_URL="http://localhost:${PROXY_PORT}" \
    ANTHROPIC_API_KEY="bedrock-proxy" \
    claude --settings "$PROJECT_DIR/config/claude-proxy-settings.json" \
           --model "$MODEL_ID" \
           ${CLAUDE_ARGS[@]+"${CLAUDE_ARGS[@]}"}
fi
