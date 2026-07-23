#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# run-e2e-benchmark.sh -- one end-to-end SWE benchmark run, from pre-flight
# checks through scoring, for a model on any of the three hosting paths.
#
# Three inputs (flags or positional):
#   --provider   bedrock | litellm | vllm
#                  bedrock  = Anthropic models on Bedrock (provider=bedrock)
#                  litellm  = open-weight models on Bedrock via the LiteLLM
#                             mantle proxy (provider=endpoint at the proxy)
#                  vllm     = self-hosted model on a local vLLM server
#                             (provider=endpoint at :8000)
#   --model      the model id / served-model-name (e.g. qwen3.6-35b,
#                us.anthropic.claude-opus-4-8, moonshotai.kimi-k2-thinking)
#   --dataset    dataset YAML, relative to benchmarks/ (e.g.
#                dataset/mcp-gateway-registry.yaml)
#
# What it does, failing LOUDLY at the first problem:
#   0. Pre-flight: tools present, endpoint reachable, model served/valid,
#      AWS creds if needed, and -- key -- no pre-existing artifact folders that
#      would stall the headless /swe overwrite prompt.
#   1. Run the SWE benchmark harness over every task in the dataset.
#   2. Score the produced artifacts with the codex judge.
# At every step it prints the exact command to watch progress (tail / status).
#
# This script does NOT start vLLM or the LiteLLM proxy for you -- those are
# separate long-lived services with their own lifecycle scripts. It checks they
# are up and tells you how to start them if not. See:
#   benchmarks/docs/end-to-end-self-hosted-run.md   (the full run-book)
#   benchmarks/docs/path-*.md                        (per-path setup)
#
# Usage:
#   ./scripts/run-e2e-benchmark.sh --provider vllm --model qwen3.6-35b \
#       --dataset dataset/mcp-gateway-registry.yaml
#   ./scripts/run-e2e-benchmark.sh vllm qwen3.6-35b dataset/mcp-gateway-registry.yaml
#   ./scripts/run-e2e-benchmark.sh --provider vllm --model qwen3.6-35b \
#       --dataset dataset/hello-world.yaml --count 1 --yes
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BENCHMARKS_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(dirname "$BENCHMARKS_DIR")"
VLLM_DIR="$REPO_ROOT/self-hosted/vllm"

# Defaults
PROVIDER=""
MODEL=""
DATASET=""
COUNT="0"                 # 0 = all tasks
ENDPOINT=""               # derived from provider unless overridden
AWS_REGION_ARG="${AWS_REGION:-us-east-1}"
ASSUME_YES=0
SKIP_JUDGE=0
CONFIG="$BENCHMARKS_DIR/config/runner.yaml"

# Endpoints per path
VLLM_ENDPOINT="http://127.0.0.1:8000"
LITELLM_ENDPOINT="http://127.0.0.1:4000"

# --- pretty output -----------------------------------------------------------
info()  { printf '\033[0;36m[info]\033[0m  %s\n' "$1"; }
ok()    { printf '\033[0;32m[ok]\033[0m    %s\n' "$1"; }
warn()  { printf '\033[0;33m[warn]\033[0m  %s\n' "$1"; }
step()  { printf '\n\033[1;35m=== %s ===\033[0m\n' "$1"; }
die()   { printf '\033[0;31m[FAIL]\033[0m  %s\n' "$1" >&2; exit 1; }

usage() {
    sed -n '3,45p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

# --- arg parsing -------------------------------------------------------------
POSITIONAL=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --provider) PROVIDER="${2:?--provider needs a value}"; shift 2 ;;
        --model)    MODEL="${2:?--model needs a value}"; shift 2 ;;
        --dataset)  DATASET="${2:?--dataset needs a value}"; shift 2 ;;
        --count)    COUNT="${2:?--count needs a value}"; shift 2 ;;
        --endpoint) ENDPOINT="${2:?--endpoint needs a value}"; shift 2 ;;
        --aws-region) AWS_REGION_ARG="${2:?--aws-region needs a value}"; shift 2 ;;
        --config)   CONFIG="${2:?--config needs a value}"; shift 2 ;;
        --yes|-y)   ASSUME_YES=1; shift ;;
        --skip-judge) SKIP_JUDGE=1; shift ;;
        -h|--help)  usage 0 ;;
        --*)        die "unknown flag: $1 (see --help)" ;;
        *)          POSITIONAL+=("$1"); shift ;;
    esac
done

# Positional fallback: provider model dataset
[[ -z "$PROVIDER" && ${#POSITIONAL[@]} -ge 1 ]] && PROVIDER="${POSITIONAL[0]}"
[[ -z "$MODEL"    && ${#POSITIONAL[@]} -ge 2 ]] && MODEL="${POSITIONAL[1]}"
[[ -z "$DATASET"  && ${#POSITIONAL[@]} -ge 3 ]] && DATASET="${POSITIONAL[2]}"

# --- validate the three inputs ----------------------------------------------
[[ -n "$PROVIDER" ]] || die "provider is required (bedrock | litellm | vllm). See --help."
[[ -n "$MODEL"    ]] || die "model is required. See --help."
[[ -n "$DATASET"  ]] || die "dataset is required (e.g. dataset/mcp-gateway-registry.yaml). See --help."

case "$PROVIDER" in
    bedrock|litellm|vllm) ;;
    *) die "invalid provider '$PROVIDER'. Must be one of: bedrock, litellm, vllm." ;;
esac

# Resolve the dataset path relative to benchmarks/ and confirm it exists.
DATASET_PATH="$DATASET"
[[ "$DATASET_PATH" = /* ]] || DATASET_PATH="$BENCHMARKS_DIR/$DATASET"
[[ -f "$DATASET_PATH" ]] || die "dataset file not found: $DATASET_PATH"

# Map provider -> harness --provider and default endpoint.
case "$PROVIDER" in
    bedrock)  HARNESS_PROVIDER="bedrock";  DEFAULT_ENDPOINT="" ;;
    litellm)  HARNESS_PROVIDER="endpoint"; DEFAULT_ENDPOINT="$LITELLM_ENDPOINT" ;;
    vllm)     HARNESS_PROVIDER="endpoint"; DEFAULT_ENDPOINT="$VLLM_ENDPOINT" ;;
esac
[[ -n "$ENDPOINT" ]] || ENDPOINT="$DEFAULT_ENDPOINT"

cd "$BENCHMARKS_DIR"

# =============================================================================
step "Step 0 - Pre-flight checks"
# =============================================================================

# uv is the entry point for everything below.
command -v uv >/dev/null 2>&1 || die "uv is not installed or not on PATH. Install: https://docs.astral.sh/uv/"
ok "uv found: $(command -v uv)"

# The harness env must be synced.
[[ -d "$BENCHMARKS_DIR/.venv" ]] || die "benchmarks venv missing. Run: (cd $BENCHMARKS_DIR && uv sync)"
ok "benchmarks venv present"

# The runner config must exist (harness reads it).
if [[ ! -f "$CONFIG" ]]; then
    die "runner config not found: $CONFIG
       Create it once: (cd $BENCHMARKS_DIR && cp config/runner.example.yaml config/runner.yaml)"
fi
ok "runner config: $CONFIG"

# claude CLI must be available (the harness shells out to it).
command -v claude >/dev/null 2>&1 || die "claude CLI not found on PATH (the harness runs 'claude -p'). Install Claude Code."
ok "claude CLI found: $(command -v claude)"

# Per-path readiness.
case "$PROVIDER" in
    bedrock)
        info "Path: Anthropic models directly on Amazon Bedrock (provider=bedrock)."
        command -v aws >/dev/null 2>&1 || die "aws CLI not found; needed for Bedrock credentials."
        aws sts get-caller-identity >/dev/null 2>&1 \
            || die "AWS credentials not usable (aws sts get-caller-identity failed). Configure creds for region $AWS_REGION_ARG."
        ok "AWS credentials OK (region $AWS_REGION_ARG)"
        case "$MODEL" in
            *anthropic*|*claude*) ;;
            *) warn "provider=bedrock is Anthropic-only; '$MODEL' does not look like an Anthropic id. Non-Anthropic Bedrock models need --provider litellm." ;;
        esac
        ;;
    litellm)
        info "Path: open-weight models on Amazon Bedrock via the LiteLLM proxy (provider=endpoint at $ENDPOINT)."
        if ! curl -s -m 5 "$ENDPOINT/health" >/dev/null 2>&1; then
            die "LiteLLM proxy not reachable at $ENDPOINT.
       Start it: (cd $BENCHMARKS_DIR && ./scripts/bedrock-mantle-proxy.sh)
       Status:   (cd $BENCHMARKS_DIR && ./scripts/bedrock-mantle-proxy.sh --status)
       Log:      tail -f $BENCHMARKS_DIR/.litellm.log"
        fi
        ok "LiteLLM proxy healthy at $ENDPOINT"
        ;;
    vllm)
        info "Path: self-hosted model on a local vLLM server (provider=endpoint at $ENDPOINT)."
        if ! curl -s -m 5 "$ENDPOINT/health" >/dev/null 2>&1; then
            die "vLLM server not reachable at $ENDPOINT.
       Start it: (cd $VLLM_DIR/scripts && MODEL=... SERVED_NAME=$MODEL MAX_MODEL_LEN=200000 ./vllm-serve.sh)
       Log:      tail -f $VLLM_DIR/logs/vllm-serve.log"
        fi
        # Confirm the requested served-model-name is actually the one loaded.
        SERVED="$(curl -s -m 5 "$ENDPOINT/v1/models" 2>/dev/null \
            | uv run python -c 'import sys,json; d=json.load(sys.stdin); print(",".join(m["id"] for m in d.get("data",[])))' 2>/dev/null || true)"
        if [[ -z "$SERVED" ]]; then
            warn "vLLM is up but /v1/models did not return a model list; continuing."
        elif [[ ",$SERVED," != *",$MODEL,"* ]]; then
            die "vLLM is serving [$SERVED], not '$MODEL'. Pass --model matching the served-model-name, or restart vLLM with SERVED_NAME=$MODEL."
        else
            ok "vLLM serving '$MODEL' at $ENDPOINT"
        fi
        # The DuckDB metrics collector is optional but recommended on this path.
        if uv run --project "$VLLM_DIR" python -c 'import sys' >/dev/null 2>&1; then :; fi
        if pgrep -f "collect_metrics" >/dev/null 2>&1; then
            ok "vLLM metrics collector is running (DuckDB time series is being captured)"
        else
            warn "vLLM metrics collector is NOT running. To capture a GPU time series:
       Clear + start: see benchmarks/docs/end-to-end-self-hosted-run.md (Step 2)
       Start:         (cd $VLLM_DIR/scripts && ./vllm-metrics.sh start)
       Status:        (cd $VLLM_DIR/scripts && ./vllm-metrics.sh status)"
        fi
        ;;
esac

# The critical check: pre-existing artifact folders stall the headless /swe run.
info "Checking for pre-existing artifact folders (these stall the headless /swe overwrite prompt)..."
set +e
uv run python scripts/preflight_check.py --dataset "$DATASET" --model "$MODEL" --check
PREFLIGHT_RC=$?
set -e
if [[ "$PREFLIGHT_RC" -eq 2 ]]; then
    if [[ "$ASSUME_YES" -eq 1 ]]; then
        warn "Clearing pre-existing artifact folders (--yes given)..."
        uv run python scripts/preflight_check.py --dataset "$DATASET" --model "$MODEL" --clear
    else
        die "Pre-existing artifact folders would stall the run. Re-run with --yes to clear them automatically, or clear manually:
       uv run python scripts/preflight_check.py --dataset $DATASET --model $MODEL --clear"
    fi
elif [[ "$PREFLIGHT_RC" -ne 0 ]]; then
    die "pre-flight folder check failed (exit $PREFLIGHT_RC)."
fi
ok "No blocking artifact folders."

ok "Pre-flight complete."

# =============================================================================
step "Step 1 - Run the SWE benchmark"
# =============================================================================
BENCH_ARGS=(--config "$CONFIG" --provider "$HARNESS_PROVIDER" --model "$MODEL" --dataset "$DATASET" --stream)
[[ "$COUNT" != "0" ]] && BENCH_ARGS+=(--count "$COUNT")
[[ "$HARNESS_PROVIDER" == "endpoint" ]] && BENCH_ARGS+=(--endpoint "$ENDPOINT")
[[ "$PROVIDER" == "bedrock" ]] && BENCH_ARGS+=(--aws-region "$AWS_REGION_ARG")

info "Command:"
info "  uv run scripts/run-swe-headless.py ${BENCH_ARGS[*]}"
info "Artifacts will land under: swe-benchmark-data/<repo>/<task>/$(uv run python -c "import sys; sys.path.insert(0,'scripts'); from runner_config import model_to_slug; print(model_to_slug('$MODEL'))")/"
info "Watch GPU metrics (vllm path):  cd $VLLM_DIR && uv run python -m clients.build_dashboard && open benchmark-output/dashboard.html"
echo
uv run scripts/run-swe-headless.py "${BENCH_ARGS[@]}" \
    || die "benchmark run failed. Inspect the trace above; per-task errors are also recorded in each task's metrics.json."
ok "Benchmark run complete."

# =============================================================================
step "Step 2 - Score the artifacts (codex judge)"
# =============================================================================
if [[ "$SKIP_JUDGE" -eq 1 ]]; then
    warn "Skipping the judge (--skip-judge). Score later with:"
    warn "  (cd $BENCHMARKS_DIR/scripts && uv run python codex_judge.py --recursive --no-overwrite --folder ../swe-benchmark-data)"
else
    command -v codex >/dev/null 2>&1 || die "codex CLI not found on PATH (the judge runs 'codex exec'). Install codex, or re-run with --skip-judge."
    # Judge only the folders this dataset+model just produced: point at the
    # dataset's repo tree and let --recursive + --no-overwrite handle the rest.
    REPO_SUBDIR="$(uv run python -c "import sys; sys.path.insert(0,'scripts'); from dataset_loader import load_dataset; d=load_dataset('$DATASET_PATH'); import importlib.util,pathlib; s=importlib.util.spec_from_file_location('h','scripts/run-swe-headless.py'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print(m._repo_name(d.tasks[0].repo))")"
    JUDGE_TARGET="swe-benchmark-data/$REPO_SUBDIR"
    info "Command:"
    info "  (cd scripts && uv run python codex_judge.py --recursive --no-overwrite --folder ../$JUDGE_TARGET)"
    info "codex exec buffers output and prints only its final message per folder -- a few minutes each at high effort is normal."
    echo
    ( cd scripts && uv run python codex_judge.py --recursive --no-overwrite --folder "../$JUDGE_TARGET" ) \
        || die "judge run failed. See the log above; re-run just the judge with the command shown."
    ok "Scoring complete."
fi

# =============================================================================
step "Done"
# =============================================================================
SLUG="$(uv run python -c "import sys; sys.path.insert(0,'scripts'); from runner_config import model_to_slug; print(model_to_slug('$MODEL'))")"
ok "End-to-end benchmark finished for provider=$PROVIDER model=$MODEL dataset=$DATASET"
info "Per-task results (metrics.json cost + eval.json quality) are under:"
info "  $BENCHMARKS_DIR/swe-benchmark-data/*/*/$SLUG/"
