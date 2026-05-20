#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# run.sh — Benchmark runner for Multi-Model Claude Code
#
# Runs each task against each model, collects pass/fail + latency + tokens,
# then optionally runs LLM-as-judge for quality scoring.
#
# Usage:
#   ./benchmark/run.sh                    # all models, all tasks
#   ./benchmark/run.sh --models "qwen-coder-next,deepseek-v3"
#   ./benchmark/run.sh --tasks "task1_bugfix,task2_tests"
#   ./benchmark/run.sh --no-judge         # skip LLM quality scoring
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
RESULTS_DIR="$SCRIPT_DIR/results"
TASKS_DIR="$SCRIPT_DIR/tasks"
CLAUDE_MODEL_SCRIPT="$PROJECT_DIR/scripts/claude-model.sh"

DEFAULT_MODELS="claude-sonnet,qwen-coder-next,deepseek-v3,kimi-k2.5,qwen-coder-30b"
DEFAULT_TASKS="task1_bugfix,task2_tests,task3_feature,task4_refactor,task5_circular_import"
RUN_JUDGE=true
TIMEOUT=180  # seconds per task

# macOS doesn't have 'timeout' — use gtimeout if available, else fallback
if command -v gtimeout &>/dev/null; then
    TIMEOUT_CMD="gtimeout"
elif command -v timeout &>/dev/null; then
    TIMEOUT_CMD="timeout"
else
    TIMEOUT_CMD=""
fi

# Pricing lookup (bash 3.2 compatible — no associative arrays)
get_input_price() {
    case "$1" in
        claude-sonnet)    echo "3.00" ;;
        qwen-coder-next)  echo "0.30" ;;
        deepseek-v3)      echo "0.50" ;;
        kimi-k2.5)        echo "0.60" ;;
        qwen-coder-30b)   echo "0.15" ;;
        *)                echo "0" ;;
    esac
}

get_output_price() {
    case "$1" in
        claude-sonnet)    echo "15.00" ;;
        qwen-coder-next)  echo "1.20" ;;
        deepseek-v3)      echo "2.00" ;;
        kimi-k2.5)        echo "2.50" ;;
        qwen-coder-30b)   echo "0.62" ;;
        *)                echo "0" ;;
    esac
}

# Parse args
MODELS="$DEFAULT_MODELS"
TASKS="$DEFAULT_TASKS"

while [[ $# -gt 0 ]]; do
    case $1 in
        --models)   MODELS="$2"; shift 2 ;;
        --tasks)    TASKS="$2"; shift 2 ;;
        --no-judge) RUN_JUDGE=false; shift ;;
        --timeout)  TIMEOUT="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: $0 [--models m1,m2] [--tasks t1,t2] [--no-judge] [--timeout SEC]"
            exit 0
            ;;
        *) echo "[error] Unknown: $1"; exit 1 ;;
    esac
done

mkdir -p "$RESULTS_DIR"
RESULTS_CSV="$RESULTS_DIR/results_$(date +%Y%m%d_%H%M%S).csv"
echo "model,task,pass,latency_sec,input_tokens,output_tokens,cost_usd" > "$RESULTS_CSV"

echo ""
echo "Multi-Model Coding Agent Benchmark"
echo "==================================="
echo "Models: $MODELS"
echo "Tasks:  $TASKS"
echo "Judge:  $RUN_JUDGE"
echo "Output: $RESULTS_CSV"
echo ""

run_task() {
    local model="$1"
    local task="$2"
    local task_dir="$TASKS_DIR/$task"
    local work_dir
    work_dir=$(mktemp -d)
    local prompt_file="$task_dir/prompt.txt"

    # Copy task files to working directory
    cp -r "$task_dir/"* "$work_dir/"

    # Read prompt
    local prompt
    prompt=$(cat "$prompt_file")

    echo -n "  [$model] $task ... "

    # Run Claude Code with timeout
    local start_time
    start_time=$(date +%s)
    local exit_code=0

    cd "$work_dir"
    if [[ -n "$TIMEOUT_CMD" ]]; then
        $TIMEOUT_CMD "$TIMEOUT" "$CLAUDE_MODEL_SCRIPT" --model "$model" \
            -p "$prompt" --allowedTools "Edit,Write,Read,Bash" --tools "Edit,Write,Read,Bash" \
            > "$work_dir/.claude_output" 2>&1 || exit_code=$?
    else
        # No timeout command available — run with background kill fallback
        "$CLAUDE_MODEL_SCRIPT" --model "$model" \
            -p "$prompt" --allowedTools "Edit,Write,Read,Bash" --tools "Edit,Write,Read,Bash" \
            > "$work_dir/.claude_output" 2>&1 &
        local pid=$!
        ( sleep "$TIMEOUT" && kill "$pid" 2>/dev/null ) &
        local watchdog=$!
        wait "$pid" 2>/dev/null || exit_code=$?
        kill "$watchdog" 2>/dev/null || true
        wait "$watchdog" 2>/dev/null || true
    fi
    local end_time
    end_time=$(date +%s)
    local latency=$((end_time - start_time))

    # Run verifier
    local pass=0
    case "$task" in
        task1_bugfix)
            cd "$work_dir" && python3 -m pytest test_binary_search.py -q 2>/dev/null && pass=1
            ;;
        task2_tests)
            if [[ -f "$work_dir/test_shopping_cart.py" ]]; then
                cd "$work_dir" && python3 -m pytest test_shopping_cart.py -q 2>/dev/null && pass=1
            fi
            ;;
        task3_feature)
            cd "$work_dir"
            python3 -c "
from fastapi.testclient import TestClient
from app import app
c = TestClient(app)
r = c.post('/items', json={'name': 'Test', 'price': 5.99})
assert r.status_code == 201, f'Got {r.status_code}'
data = r.json()
assert 'id' in data
assert data['name'] == 'Test'
# Test validation
r2 = c.post('/items', json={'name': 'Bad', 'price': -1})
assert r2.status_code == 422
" 2>/dev/null && pass=1
            ;;
        task4_refactor)
            cd "$work_dir"
            if python3 -m pytest test_process_csv.py -q 2>/dev/null; then
                local func_count
                func_count=$(grep -c "^def \|^    def " process_csv.py 2>/dev/null || echo 0)
                if [[ "$func_count" -ge 4 ]]; then
                    pass=1
                fi
            fi
            ;;
        task5_circular_import)
            cd "$work_dir" && python3 -m pytest test_app.py -q 2>/dev/null && pass=1
            ;;
    esac

    # Extract tokens from LiteLLM log (approximate — last request)
    local input_tokens=0
    local output_tokens=0

    # Calculate cost
    local in_price
    in_price=$(get_input_price "$model")
    local out_price
    out_price=$(get_output_price "$model")
    local cost
    cost=$(echo "scale=6; ($input_tokens * $in_price + $output_tokens * $out_price) / 1000000" | bc 2>/dev/null || echo "0")

    # Record result
    echo "$model,$task,$pass,$latency,$input_tokens,$output_tokens,$cost" >> "$RESULTS_CSV"

    if [[ "$pass" -eq 1 ]]; then
        echo "PASS (${latency}s)"
    else
        echo "FAIL (${latency}s)"
    fi

    # Save output for judge
    if [[ "$RUN_JUDGE" == "true" && -f "$work_dir/.claude_output" ]]; then
        mkdir -p "$RESULTS_DIR/outputs/$model"
        cp "$work_dir/.claude_output" "$RESULTS_DIR/outputs/$model/$task.txt"
    fi

    # Cleanup
    rm -rf "$work_dir"
    cd "$SCRIPT_DIR"
}

# Main loop
IFS=',' read -ra MODEL_LIST <<< "$MODELS"
IFS=',' read -ra TASK_LIST <<< "$TASKS"

for model in "${MODEL_LIST[@]}"; do
    echo ""
    echo "[$model]"
    for task in "${TASK_LIST[@]}"; do
        run_task "$model" "$task"
    done
done

echo ""
echo "==================================="
echo "Results saved to: $RESULTS_CSV"
echo ""

# Print summary table
print_summary() {
    echo "Summary:"
    echo "--------"
    printf "%-20s " "Model"
    for task in "${TASK_LIST[@]}"; do
        printf "%-8s " "${task##task*_}"
    done
    printf "%-6s\n" "Rate"
    echo "---"

    for model in "${MODEL_LIST[@]}"; do
        printf "%-20s " "$model"
        local pass_count=0
        local total=0
        while IFS=',' read -r m t p rest; do
            if [[ "$m" == "$model" ]]; then
                if [[ "$p" == "1" ]]; then
                    printf "%-8s " "PASS"
                    ((pass_count++)) || true
                else
                    printf "%-8s " "FAIL"
                fi
                ((total++)) || true
            fi
        done < <(tail -n +2 "$RESULTS_CSV")
        local rate=0
        [[ $total -gt 0 ]] && rate=$((pass_count * 100 / total))
        printf "%-6s\n" "${rate}%"
    done
}

print_summary

# Run LLM judge if enabled
if [[ "$RUN_JUDGE" == "true" ]]; then
    echo ""
    echo "Running LLM-as-Judge (Claude Opus via native Bedrock)..."
    python3 "$SCRIPT_DIR/judge.py" "$RESULTS_CSV" "$RESULTS_DIR/outputs" 2>/dev/null || \
        echo "[warn] Judge failed — run manually: python3 benchmark/judge.py $RESULTS_CSV"
fi
