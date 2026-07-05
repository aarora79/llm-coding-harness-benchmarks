#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# vllm-serve.sh — Serve an open-weight model with vLLM tensor-parallel on a
#                 multi-GPU EC2 node (reference: g6e.12xlarge, 4x L40S).
#
# vLLM shards the model across all GPUs with tensor parallelism
# (--tensor-parallel-size), so a 30B–80B model that will not fit on one L40S
# (46 GB) serves comfortably across four. Unlike Ollama's pipeline split,
# tensor parallelism keeps every GPU busy on every token and sustains high
# throughput under concurrent load — the regime the cost model in the strategy
# doc depends on.
#
# Usage:
#   ./vllm-serve.sh                         # default: qwen3-coder-30b, TP=4
#   MODEL=Qwen/Qwen3-32B ./vllm-serve.sh    # a different HF model
#   TP=2 ./vllm-serve.sh                    # fewer GPUs
#   ./vllm-serve.sh --foreground            # run in the foreground (see logs live)
#
# Environment variables (all optional — sensible defaults for a 4x L40S node):
#   MODEL              HF repo id to serve            (default: Qwen/Qwen3-Coder-30B-A3B-Instruct)
#   SERVED_NAME        name clients pass as --model   (default: qwen3-coder-30b)
#   TP                 tensor-parallel size / #GPUs   (default: 4)
#   PORT               OpenAI-compatible API port     (default: 8000)
#   MAX_MODEL_LEN      context window to serve        (default: 32768)
#   GPU_MEM_UTIL       fraction of VRAM vLLM may use  (default: 0.90)
#   TOOL_PARSER        vLLM tool-call parser          (default: qwen3_coder)
#                      set to "" / "none" to disable tool calling
#   VLLM_ENV           path to the vLLM virtualenv    (default: ~/vllm-env)
#   HF_TOKEN           HuggingFace token for gated/faster downloads (optional)
#
# Tool calling is ON by default. Agentic clients (opencode, Claude Code) send
# `tool_choice: "auto"`, which vLLM rejects unless the server was started with
# --enable-auto-tool-choice and a matching --tool-call-parser. The default
# parser `qwen3_coder` is correct for the Qwen3-Coder models; use `hermes` for
# other Qwen3 chat models, or set TOOL_PARSER=none for a plain completion
# server. Run `vllm serve --help` for the full parser list.
#
# The server binds to 127.0.0.1 only. Reach it from your laptop with an SSH
# tunnel (see tunnel.sh), exactly like the Ollama path — no public ingress.
# ---------------------------------------------------------------------------

MODEL="${MODEL:-Qwen/Qwen3-Coder-30B-A3B-Instruct}"
SERVED_NAME="${SERVED_NAME:-qwen3-coder-30b}"
TP="${TP:-4}"
PORT="${PORT:-8000}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-32768}"
GPU_MEM_UTIL="${GPU_MEM_UTIL:-0.90}"
TOOL_PARSER="${TOOL_PARSER:-qwen3_coder}"
VLLM_ENV="${VLLM_ENV:-$HOME/vllm-env}"

# Logs are written under the repo's gitignored logs dir (self-hosted/vllm/logs/)
# and simultaneously streamed to the console via tee. Override with LOG_DIR=...
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${LOG_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)/logs}"

FOREGROUND=0
[[ "${1:-}" == "--foreground" || "${1:-}" == "-f" ]] && FOREGROUND=1

RED='\033[0;31m'; GREEN='\033[0;32m'; BLUE='\033[0;34m'; YELLOW='\033[0;33m'; BOLD='\033[1m'; RESET='\033[0m'
info()   { echo -e "${BLUE}[info]${RESET}  $1"; }
ok()     { echo -e "${GREEN}[ok]${RESET}    $1"; }
warn()   { echo -e "${YELLOW}[warn]${RESET}  $1"; }
fail()   { echo -e "${RED}[fail]${RESET}  $1"; exit 1; }

# --help: print the header comment block (env vars + options) and exit.
if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  sed -n '5,41p' "$0" | sed 's/^# \{0,1\}//; s/^#$//'
  echo "Options: --foreground|-f  (run in foreground)   --stop  (stop + free GPUs)"
  exit 0
fi

# --stop: kill the background server (launcher + tee + vLLM workers) and free GPUs.
if [[ "${1:-}" == "--stop" ]]; then
  STOPPED=0
  if [[ -f /tmp/vllm-serve.pid ]]; then
    PID=$(cat /tmp/vllm-serve.pid)
    kill "$PID" 2>/dev/null && STOPPED=1 || true
    rm -f /tmp/vllm-serve.pid
  fi
  # The actual vLLM engine + TP workers are children; kill them by name too so
  # no orphaned process keeps the GPUs pinned.
  pkill -f "vllm serve" 2>/dev/null && STOPPED=1 || true
  if [[ "$STOPPED" -eq 1 ]]; then
    ok "Stopped vLLM server. GPUs free once the workers exit (check: nvidia-smi)."
  else
    warn "No running vLLM server found."
  fi
  exit 0
fi

VLLM_BIN="$VLLM_ENV/bin/vllm"
[[ -x "$VLLM_BIN" ]] || fail "vLLM not found at $VLLM_BIN. Run ./vllm-install.sh first (or set VLLM_ENV)."

# Sanity: enough GPUs for the requested tensor-parallel size?
GPU_COUNT=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | wc -l | xargs)
[[ "$GPU_COUNT" -ge "$TP" ]] || fail "Requested TP=$TP but only $GPU_COUNT GPU(s) visible."

info "Model:        $MODEL"
info "Served as:    $SERVED_NAME  (clients pass --model $SERVED_NAME)"
info "GPUs:         $TP of $GPU_COUNT (tensor parallelism)"
info "Context:      $MAX_MODEL_LEN tokens"
info "API:          http://127.0.0.1:$PORT/v1  (OpenAI-compatible)"
echo ""

# Telemetry off: vLLM's usage stats never leave the box (strategy doc §6 egress).
export VLLM_NO_USAGE_STATS=1
export DO_NOT_TRACK=1
[[ -n "${HF_TOKEN:-}" ]] && export HF_TOKEN

# Use vLLM's native Torch top-k/top-p sampler instead of FlashInfer's. FlashInfer
# JIT-compiles CUDA sampling kernels at startup and hardcodes CUDA_HOME=/usr/local/cuda,
# which does NOT exist on the Deep Learning AMI (its toolkit lives at
# /opt/pytorch/cuda). The native sampler needs no runtime nvcc, so the server
# boots reliably and faster. (Verified fix on the Ubuntu 24.04 DLAMI, 2026-07.)
export VLLM_USE_FLASHINFER_SAMPLER="${VLLM_USE_FLASHINFER_SAMPLER:-0}"
# Belt-and-suspenders: if anything else needs to JIT-compile against CUDA, point
# it at the toolkit the DLAMI actually ships rather than the missing default.
if [[ -z "${CUDA_HOME:-}" ]]; then
  for c in /opt/pytorch/cuda /usr/local/cuda; do
    [[ -x "$c/bin/nvcc" ]] && export CUDA_HOME="$c" && break
  done
fi

ARGS=(
  serve "$MODEL"
  --tensor-parallel-size "$TP"
  --host 127.0.0.1
  --port "$PORT"
  --served-model-name "$SERVED_NAME"
  --max-model-len "$MAX_MODEL_LEN"
  --gpu-memory-utilization "$GPU_MEM_UTIL"
)

# Enable tool calling unless explicitly disabled — agentic clients need it.
if [[ -n "$TOOL_PARSER" && "$TOOL_PARSER" != "none" ]]; then
  ARGS+=( --enable-auto-tool-choice --tool-call-parser "$TOOL_PARSER" )
  info "Tools:        enabled (parser: $TOOL_PARSER)"
else
  info "Tools:        disabled (plain completion server)"
fi

mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/vllm-serve.log"

if [[ "$FOREGROUND" -eq 1 ]]; then
  info "Starting vLLM in the foreground (Ctrl-C to stop). First run downloads weights."
  info "Full log streams to the console AND is tee'd to: $LOG"
  info "(logs/ is gitignored — the log is never committed)"
  # tee: everything vLLM prints goes to the terminal and to the log file at once.
  exec "$VLLM_BIN" "${ARGS[@]}" 2>&1 | tee "$LOG"
fi

info "Starting vLLM in the background. Full log tee'd to: $LOG"
info "(logs/ is gitignored — the log is never committed)"
info "First run downloads the weights (30B ≈ 61 GB) — allow several minutes."
# tee inside the background job so the file captures everything; the foreground
# shell stays free to poll for readiness below.
nohup bash -c "'$VLLM_BIN' $(printf '%q ' "${ARGS[@]}") 2>&1 | tee '$LOG'" >/dev/null 2>&1 &
SERVE_PID=$!
echo "$SERVE_PID" > /tmp/vllm-serve.pid
info "Launcher PID $SERVE_PID (saved to /tmp/vllm-serve.pid)"
info "Tail the log live with:  tail -f $LOG"
echo ""

# Poll for readiness. Weight download can be slow, so wait generously.
info "Waiting for the server to become ready (up to 30 min for first download)..."
for i in $(seq 1 360); do
  if ! kill -0 "$SERVE_PID" 2>/dev/null; then
    echo ""; fail "vLLM process exited early. Check: tail -50 $LOG"
  fi
  if curl -sf "http://127.0.0.1:$PORT/v1/models" >/dev/null 2>&1; then
    echo ""
    ok "Server ready at http://127.0.0.1:$PORT/v1"
    curl -s "http://127.0.0.1:$PORT/v1/models" | \
      "$VLLM_ENV/bin/python" -c "import json,sys; [print('       served model:', m['id']) for m in json.load(sys.stdin).get('data',[])]" 2>/dev/null || true
    echo ""
    echo "Next steps:"
    echo "  1. Verify inference:   ./vllm-verify.sh"
    echo "  2. Tunnel from laptop: LOCAL_MODEL_PORT=$PORT G6E_IP=<ip> ./tunnel.sh start"
    echo "  3. Stop the server:    ./vllm-serve.sh --stop   (or: kill \$(cat /tmp/vllm-serve.pid))"
    exit 0
  fi
  sleep 5
done
echo ""
fail "Server did not become ready in time. Check: tail -50 $LOG"
