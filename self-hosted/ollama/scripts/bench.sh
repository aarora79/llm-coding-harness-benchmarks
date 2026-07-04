#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# bench.sh — Coding Benchmark: Claude via Bedrock vs self-hosted open-source model
#
# Usage:
#   ./scripts/bench.sh bedrock    # Test A: Claude via Amazon Bedrock
#   ./scripts/bench.sh local      # Test B: local model via SSH tunnel (default port 11434)
#   ./scripts/bench.sh both       # Run both back-to-back
#
# For llama.cpp (port 8131):
#   LOCAL_MODEL_PORT=8131 ./scripts/bench.sh local
#
# Runs 9 coding tasks against the sample/ project in this repo.
# Each task has an automated pass/fail verifier.
# Results saved to results/<backend>/<task>/
#
# Prerequisites:
#   - Claude Code CLI installed (npm install -g @anthropic-ai/claude-code)
#   - For "bedrock": AWS credentials configured
#   - For "local": SSH tunnel active on localhost:11434 (see scripts/tunnel.sh)
#   - python3 and pytest installed
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
SAMPLE_DIR="$REPO_DIR/sample"
RESULTS_DIR="$REPO_DIR/results"
RUNS=3

# Port for the local model server. Override for llama.cpp: LOCAL_MODEL_PORT=8131 ./bench.sh local
LOCAL_MODEL_PORT="${LOCAL_MODEL_PORT:-11434}"

export PATH="$HOME/.local/bin:$PATH"

# Ensure pytest is available (needed for c2_write_tests verifier)
if ! python3 -m pytest --version >/dev/null 2>&1; then
    echo "[setup] pytest not found — installing..."
    python3 -m pip install --user --quiet pytest || {
        echo "[error] Failed to install pytest. Run: pip install pytest"
        exit 1
    }
fi

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; RESET='\033[0m'

info()   { echo -e "${BLUE}[info]${RESET}  $1"; }
ok()     { echo -e "${GREEN}[ok]${RESET}    $1"; }
warn()   { echo -e "${YELLOW}[warn]${RESET}  $1"; }
fail()   { echo -e "${RED}[fail]${RESET}  $1"; exit 1; }
header() { echo -e "\n${BOLD}═══ $1 ═══${RESET}"; }

declare -a TASK_NAMES
declare -a TASK_TARGET_FILES
declare -a TASK_VERIFIERS

# ---------------------------------------------------------------------------
# TIER 1 — Simple: single clear change to one file
# ---------------------------------------------------------------------------

TASK_NAMES+=("c1_add_helper_function")
TASK_TARGET_FILES+=("sample/utils/path_utils.py")
TASK_VERIFIERS+=("_verify_c1_add_helper_function")

_verify_c1_add_helper_function() {
    local repo="$1"; local file="$repo/sample/utils/path_utils.py"
    grep -q "def sanitize_name" "$file" || { echo "sanitize_name not found"; return 1; }
    grep -q -- "-> str" "$file" || { echo "return type -> str not found"; return 1; }
    python3 -m py_compile "$file" 2>&1 || { echo "syntax error"; return 1; }
    python3 -c "
import sys; sys.path.insert(0, '$repo')
from sample.utils.path_utils import sanitize_name
assert sanitize_name('My Widget!') == 'my-widget', f'got: {sanitize_name(\"My Widget!\")}'
assert sanitize_name('  hello world  ') == 'hello-world', f'got: {sanitize_name(\"  hello world  \")}'
assert sanitize_name('--test--') == 'test', f'got: {sanitize_name(\"--test--\")}'
print('ok')
" 2>&1 || { echo "sanitize_name logic incorrect"; return 1; }
    return 0
}

# ---

TASK_NAMES+=("c1_modernize_type_hints")
TASK_TARGET_FILES+=("sample/utils/validator.py")
TASK_VERIFIERS+=("_verify_c1_modernize_type_hints")

_verify_c1_modernize_type_hints() {
    local repo="$1"; local file="$repo/sample/utils/validator.py"
    grep -q "Optional\[" "$file" && { echo "Optional[ still present"; return 1; } || true
    grep -q "| None" "$file" || { echo "| None not found"; return 1; }
    python3 -m py_compile "$file" 2>&1 || { echo "syntax error"; return 1; }
    return 0
}

# ---

TASK_NAMES+=("c1_add_input_validation")
TASK_TARGET_FILES+=("sample/utils/path_utils.py")
TASK_VERIFIERS+=("_verify_c1_add_input_validation")

_verify_c1_add_input_validation() {
    local repo="$1"; local file="$repo/sample/utils/path_utils.py"
    python3 -m py_compile "$file" 2>&1 || { echo "syntax error"; return 1; }
    python3 -c "
import sys; sys.path.insert(0, '$repo')
from sample.utils.path_utils import normalize_path
try:
    normalize_path(None); print('FAIL: None should raise'); sys.exit(1)
except (ValueError, TypeError): pass
try:
    normalize_path(123); print('FAIL: int should raise'); sys.exit(1)
except TypeError: pass
try:
    normalize_path('   '); print('FAIL: empty should raise'); sys.exit(1)
except ValueError: pass
r = normalize_path('my-item')
assert r == '/items/my-item', f'got: {r}'
print('ok')
" 2>&1 || { echo "validation logic incorrect"; return 1; }
    return 0
}

# ---------------------------------------------------------------------------
# TIER 2 — Medium: non-trivial logic or new file
# ---------------------------------------------------------------------------

TASK_NAMES+=("c2_add_logging")
TASK_TARGET_FILES+=("sample/utils/path_utils.py")
TASK_VERIFIERS+=("_verify_c2_add_logging")

_verify_c2_add_logging() {
    local repo="$1"; local file="$repo/sample/utils/path_utils.py"
    grep -q "import logging" "$file" || { echo "import logging missing"; return 1; }
    grep -q "getLogger(__name__)" "$file" || { echo "logger missing"; return 1; }
    grep -q "logger.debug" "$file" || { echo "no debug log calls found"; return 1; }
    python3 -m py_compile "$file" 2>&1 || { echo "syntax error"; return 1; }
    python3 -c "
import sys, logging
sys.path.insert(0, '$repo')
records = []
class Cap(logging.Handler):
    def emit(self, r): records.append(r.getMessage())
h = Cap(); h.setLevel(logging.DEBUG)
logging.getLogger().addHandler(h); logging.getLogger().setLevel(logging.DEBUG)
from sample.utils.path_utils import normalize_path
normalize_path('test-item')
assert any('test-item' in m or 'normalize' in m.lower() for m in records), f'no debug log fired, got: {records}'
print('ok')
" 2>&1 || { echo "debug log not firing at runtime"; return 1; }
    return 0
}

# ---

TASK_NAMES+=("c2_write_tests")
TASK_TARGET_FILES+=("sample/tests/unit/test_path_utils.py")
TASK_VERIFIERS+=("_verify_c2_write_tests")

_verify_c2_write_tests() {
    local repo="$1"; local file="$repo/sample/tests/unit/test_path_utils.py"
    [[ -f "$file" ]] || { echo "test file not created"; return 1; }
    python3 -m py_compile "$file" 2>&1 || { echo "syntax error"; return 1; }
    grep -q "normalize_path" "$file" || { echo "normalize_path not tested"; return 1; }
    grep -q "extract_name" "$file" || { echo "extract_name not tested"; return 1; }
    grep -q "validate_name" "$file" || { echo "validate_name not tested"; return 1; }
    local count; count=$(python3 -c "print(open('$file').read().count('\ndef test_'))" 2>/dev/null || echo 0)
    [[ "$count" -ge 9 ]] || { echo "only $count test functions, need >= 9"; return 1; }
    cd "$repo" && python3 -m pytest "$file" -q --tb=short 2>&1
    local rc=${PIPESTATUS[0]}
    [[ $rc -eq 0 ]] || { echo "tests failed"; return 1; }
    return 0
}

# ---

TASK_NAMES+=("c2_implement_retry")
TASK_TARGET_FILES+=("sample/utils/retry.py")
TASK_VERIFIERS+=("_verify_c2_implement_retry")

_verify_c2_implement_retry() {
    local repo="$1"; local file="$repo/sample/utils/retry.py"
    [[ -f "$file" ]] || { echo "retry.py not created"; return 1; }
    python3 -m py_compile "$file" 2>&1 || { echo "syntax error"; return 1; }
    grep -q "def retry_sync" "$file" || { echo "retry_sync missing"; return 1; }
    grep -q "def retry_async\|async def retry_async" "$file" || { echo "retry_async missing"; return 1; }
    python3 -c "
import sys, asyncio; sys.path.insert(0, '$repo')
from sample.utils.retry import retry_sync, retry_async

c = [0]
def flaky():
    c[0] += 1
    if c[0] < 3: raise ValueError('not yet')
    return 'done'
assert retry_sync(flaky, max_attempts=3, delay_seconds=0) == 'done'
assert c[0] == 3

c2 = [0]
def always_fails():
    c2[0] += 1; raise RuntimeError('always')
try:
    retry_sync(always_fails, max_attempts=3, delay_seconds=0); sys.exit(1)
except RuntimeError: pass
assert c2[0] == 3

async def test():
    c = [0]
    async def fa():
        c[0] += 1
        if c[0] < 2: raise ValueError('wait')
        return 'ok'
    assert await retry_async(fa, max_attempts=3, delay_seconds=0) == 'ok'
asyncio.run(test())
print('ok')
" 2>&1 || { echo "retry logic incorrect"; return 1; }
    return 0
}

# ---------------------------------------------------------------------------
# TIER 3 — Complex: multi-file awareness or architectural change
# ---------------------------------------------------------------------------

TASK_NAMES+=("c3_add_version_endpoint")
TASK_TARGET_FILES+=("sample/api/routes.py")
TASK_VERIFIERS+=("_verify_c3_add_version_endpoint")

_verify_c3_add_version_endpoint() {
    local repo="$1"; local file="$repo/sample/api/routes.py"
    grep -q "def get_version\|async def get_version" "$file" || { echo "get_version not found"; return 1; }
    grep -q '"/version"\|'"'/version'" "$file" || { echo "/version route missing"; return 1; }
    grep -q "__version__" "$file" || { echo "__version__ not imported"; return 1; }
    python3 -m py_compile "$file" 2>&1 || { echo "syntax error"; return 1; }
    return 0
}

# ---

TASK_NAMES+=("c3_add_timing_middleware")
TASK_TARGET_FILES+=("sample/app.py")
TASK_VERIFIERS+=("_verify_c3_add_timing_middleware")

_verify_c3_add_timing_middleware() {
    local repo="$1"; local file="$repo/sample/app.py"
    python3 -m py_compile "$file" 2>&1 || { echo "syntax error"; return 1; }
    grep -q "middleware\|@app.middleware" "$file" || { echo "middleware not found"; return 1; }
    grep -q "X-Response-Time" "$file" || { echo "X-Response-Time header missing"; return 1; }
    grep -q "import time" "$file" || { echo "import time missing"; return 1; }
    return 0
}

# ---

TASK_NAMES+=("c3_refactor_client_di")
TASK_TARGET_FILES+=("sample/core/client.py")
TASK_VERIFIERS+=("_verify_c3_refactor_client_di")

_verify_c3_refactor_client_di() {
    local repo="$1"; local file="$repo/sample/core/client.py"
    python3 -m py_compile "$file" 2>&1 || { echo "syntax error"; return 1; }
    grep -q "_http_client\|_client\|_sync_client" "$file" || { echo "module-level client variable not found"; return 1; }
    grep -q "def set_http_client\|def set_client" "$file" || { echo "setter not found"; return 1; }
    grep -q "def get_http_client\|def get_client" "$file" || { echo "getter not found"; return 1; }
    python3 -c "
import ast, sys
tree = ast.parse(open('$file').read())
funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
setter = next((f for f in funcs if 'set' in f and ('client' in f or 'http' in f)), None)
getter = next((f for f in funcs if 'get' in f and ('client' in f or 'http' in f)), None)
assert setter, f'no setter found, got: {list(funcs)}'
assert getter, f'no getter found, got: {list(funcs)}'
returns = [n for n in ast.walk(funcs[getter]) if isinstance(n, ast.Return)]
assert returns, 'getter has no return statement'
print('ok')
" 2>&1 || { echo "DI structure check failed"; return 1; }
    return 0
}

# ---------------------------------------------------------------------------
# Extract code block from model output and write to target file
# ---------------------------------------------------------------------------
_extract_and_write_code() {
    local text_file="$1"
    local target_file="$2"
    local repo="$3"
    local out_path="$repo/$target_file"

    mkdir -p "$(dirname "$out_path")"

    python3 - "$text_file" "$out_path" << 'PYEOF'
import sys, re

text = open(sys.argv[1]).read()
out_path = sys.argv[2]

matches = re.findall(r'```python\s*\n(.*?)```', text, re.DOTALL)
if not matches:
    matches = re.findall(r'```\s*\n(.*?)```', text, re.DOTALL)

if matches:
    code = max(matches, key=len).strip()
    with open(out_path, 'w') as f:
        f.write(code + '\n')
    sys.exit(0)
else:
    print('no code block found in output', file=sys.stderr)
    sys.exit(1)
PYEOF
}

# ---------------------------------------------------------------------------
# Reset sample/ to original state between runs
# ---------------------------------------------------------------------------
_reset_sample() {
    cd "$REPO_DIR"
    git checkout -- sample/ 2>/dev/null || true
    git clean -fd -- sample/utils/retry.py sample/tests/unit/test_path_utils.py 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# Backend setup
# ---------------------------------------------------------------------------
_setup_backend() {
    local backend="$1"
    if [[ "$backend" == "local" ]]; then
        if ! curl -sf "http://localhost:${LOCAL_MODEL_PORT}/v1/models" >/dev/null 2>&1; then
            fail "Model server not reachable on localhost:${LOCAL_MODEL_PORT}. Run: ./scripts/tunnel.sh start"
        fi
        local model_id
        model_id=$(curl -s "http://localhost:${LOCAL_MODEL_PORT}/v1/models" | python3 -c \
            "import json,sys; print(json.load(sys.stdin)['data'][0]['id'])" 2>/dev/null || echo "unknown")
        unset CLAUDE_CODE_USE_BEDROCK AWS_PROFILE 2>/dev/null || true
        export ANTHROPIC_BASE_URL="http://127.0.0.1:${LOCAL_MODEL_PORT}"
        export ANTHROPIC_API_KEY="local"
        export ANTHROPIC_MODEL="$model_id"
        info "Backend: local model ($model_id) via localhost:${LOCAL_MODEL_PORT}"
    elif [[ "$backend" == "bedrock" ]]; then
        unset ANTHROPIC_BASE_URL ANTHROPIC_API_KEY ANTHROPIC_MODEL 2>/dev/null || true
        export CLAUDE_CODE_USE_BEDROCK=1
        if ! aws sts get-caller-identity >/dev/null 2>&1; then
            fail "AWS credentials not configured."
        fi
        local identity
        identity=$(aws sts get-caller-identity --query 'Arn' --output text 2>/dev/null)
        info "Backend: Amazon Bedrock (Claude) — $identity"
    else
        fail "Unknown backend: $backend. Use 'bedrock' or 'local'."
    fi
}

# ---------------------------------------------------------------------------
# Build prompt for a task — embeds current file content
# ---------------------------------------------------------------------------
_build_prompt() {
    local task_name="$1"

    case "$task_name" in
        c1_add_helper_function)
            local content; content=$(cat "$SAMPLE_DIR/utils/path_utils.py")
            printf '%s\n\nCurrent content of sample/utils/path_utils.py:\n```python\n%s\n```\n\nAdd a function called sanitize_name after the existing functions:\n- Signature: def sanitize_name(name: str) -> str:\n- Docstring explaining what it does\n- Logic: (1) lowercase, (2) replace spaces with hyphens, (3) remove non-alphanumeric/hyphen chars, (4) strip leading/trailing hyphens\n- Example: sanitize_name("My Widget!") -> "my-widget"\n- Example: sanitize_name("--test--") -> "test"\n\nReturn ONLY the complete modified file as a single ```python ... ``` code block. No explanations.' \
                "Add a new function to this Python file." "$content"
            ;;
        c1_modernize_type_hints)
            local content; content=$(cat "$SAMPLE_DIR/utils/validator.py")
            printf '%s\n\nCurrent content of sample/utils/validator.py:\n```python\n%s\n```\n\nUpdate every Optional[X] to X | None using Python 3.10+ PEP 604 syntax.\nRemove Optional from the typing import (keep TYPE_CHECKING and any other imports still used).\n\nReturn ONLY the complete modified file as a single ```python ... ``` code block. No explanations.' \
                "Modernize the type hints in this Python file." "$content"
            ;;
        c1_add_input_validation)
            local content; content=$(cat "$SAMPLE_DIR/utils/path_utils.py")
            printf '%s\n\nCurrent content of sample/utils/path_utils.py:\n```python\n%s\n```\n\nAt the start of normalize_path, before any existing logic, add:\n1. if path is None: raise ValueError("path cannot be None")\n2. if not isinstance(path, str): raise TypeError("path must be a string")\n3. if len(path.strip()) == 0: raise ValueError("path cannot be empty")\n\nReturn ONLY the complete modified file as a single ```python ... ``` code block. No explanations.' \
                "Add input validation to a function in this Python file." "$content"
            ;;
        c2_add_logging)
            local content; content=$(cat "$SAMPLE_DIR/utils/path_utils.py")
            printf '%s\n\nCurrent content of sample/utils/path_utils.py:\n```python\n%s\n```\n\nChanges needed:\n1. Add "import logging" at the top\n2. Add "logger = logging.getLogger(__name__)" at module level\n3. In normalize_path, add at start: logger.debug(f"normalize_path input: {path}")\n4. In normalize_path, add just before return: logger.debug(f"normalize_path output: {path}")\n\nReturn ONLY the complete modified file as a single ```python ... ``` code block. No explanations.' \
                "Add logging to a function in this Python file." "$content"
            ;;
        c2_write_tests)
            local content; content=$(cat "$SAMPLE_DIR/utils/path_utils.py")
            printf '%s\n\nSource file:\n```python\n%s\n```\n\nRequirements:\n- Test all three functions: normalize_path, extract_name, validate_name\n- At least 3 test cases per function: normal input, edge cases, invalid input\n- Descriptive test names (e.g. test_normalize_path_adds_prefix)\n- Import: from sample.utils.path_utils import normalize_path, extract_name, validate_name\n\nReturn ONLY the complete test file as a single ```python ... ``` code block. No explanations.' \
                "Write a pytest test file for sample/utils/path_utils.py." "$content"
            ;;
        c2_implement_retry)
            printf '%s\n\nReturn ONLY the complete file as a single ```python ... ``` code block. No explanations.' \
                "Implement sample/utils/retry.py with:
- retry_sync(func, max_attempts: int = 3, delay_seconds: float = 1.0, exceptions: tuple = (Exception,)) -> any
  Calls func() with no arguments. On exception, waits delay_seconds then retries. Re-raises after max_attempts.
- retry_async(func, max_attempts: int = 3, delay_seconds: float = 1.0, exceptions: tuple = (Exception,)) -> any
  Same for async functions. Use asyncio.sleep for delay.
Add type hints and docstrings."
            ;;
        c3_add_version_endpoint)
            local routes; routes=$(cat "$SAMPLE_DIR/api/routes.py")
            local ver; ver=$(cat "$SAMPLE_DIR/version.py")
            printf '%s\n\nsample/version.py:\n```python\n%s\n```\n\nCurrent sample/api/routes.py:\n```python\n%s\n```\n\nAdd GET /version returning: {"version": <__version__>, "service": "sample-app"}\n- Import __version__ from sample.version\n- Name the function get_version\n\nReturn ONLY the complete modified routes.py as a single ```python ... ``` code block. No explanations.' \
                "Add a GET /version endpoint to this FastAPI router." "$ver" "$routes"
            ;;
        c3_add_timing_middleware)
            local content; content=$(cat "$SAMPLE_DIR/app.py")
            printf '%s\n\nCurrent sample/app.py:\n```python\n%s\n```\n\nAdd @app.middleware("http") that:\n1. Records start time\n2. Calls await call_next(request)\n3. Calculates elapsed milliseconds\n4. Adds X-Response-Time header (e.g. "42.3ms")\n5. Logs DEBUG: "REQUEST {method} {path} completed in {ms:.1f}ms"\n\nAdd "import time" and "import logging" if not present.\n\nReturn ONLY the complete modified app.py as a single ```python ... ``` code block. No explanations.' \
                "Add HTTP request timing middleware to this FastAPI application." "$content"
            ;;
        c3_refactor_client_di)
            local content; content=$(cat "$SAMPLE_DIR/core/client.py")
            printf '%s\n\nCurrent sample/core/client.py:\n```python\n%s\n```\n\nChanges:\n1. Add module-level: _http_client: httpx.Client | None = None\n2. Add: def set_http_client(client: httpx.Client) -> None\n3. Add: def get_http_client() -> httpx.Client — returns _http_client if set, else new httpx.Client()\n4. Update fetch_data to call get_http_client() and use it directly (not as context manager)\n5. Keep fetch_data_async unchanged\n\nReturn ONLY the complete modified file as a single ```python ... ``` code block. No explanations.' \
                "Refactor this HTTP client module to support dependency injection." "$content"
            ;;
    esac
}

# ---------------------------------------------------------------------------
# Run one task / one run
# ---------------------------------------------------------------------------
_run_task() {
    local backend="$1"
    local task_idx="$2"
    local run_num="$3"

    local task_name="${TASK_NAMES[$task_idx]}"
    local target_file="${TASK_TARGET_FILES[$task_idx]}"
    local verifier="${TASK_VERIFIERS[$task_idx]}"

    local output_dir="$RESULTS_DIR/${backend}/${task_name}"
    mkdir -p "$output_dir"

    local output_file="$output_dir/run_${run_num}.json"
    local text_file="$output_dir/run_${run_num}.txt"
    local timing_file="$output_dir/run_${run_num}.timing"
    local verify_file="$output_dir/run_${run_num}.verify"

    _reset_sample

    info "  Run $run_num/$RUNS — $task_name"

    local prompt
    prompt=$(_build_prompt "$task_name")

    local start_time
    start_time=$(python3 -c "import time; print(time.time())")

    local exit_code=0
    if [[ "$backend" == "local" ]]; then
        ANTHROPIC_BASE_URL="http://127.0.0.1:${LOCAL_MODEL_PORT}" \
        ANTHROPIC_API_KEY="local" \
        ANTHROPIC_MODEL="${ANTHROPIC_MODEL:-local}" \
        claude -p "$prompt" \
            --output-format json \
            --no-session-persistence \
            > "$output_file" 2>"$output_dir/run_${run_num}.stderr" || exit_code=$?
    elif [[ "$backend" == "bedrock" ]]; then
        CLAUDE_CODE_USE_BEDROCK=1 \
        claude -p "$prompt" \
            --output-format json \
            --no-session-persistence \
            > "$output_file" 2>"$output_dir/run_${run_num}.stderr" || exit_code=$?
    fi

    local end_time
    end_time=$(python3 -c "import time; print(time.time())")
    local elapsed
    elapsed=$(python3 -c "print(round($end_time - $start_time, 2))")

    python3 -c "
import json, sys
try:
    data = json.load(open('$output_file'))
    if isinstance(data, dict):
        print(data.get('result', data.get('content', '')))
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get('type') == 'text':
                print(item.get('text', ''))
except:
    sys.stdout.write(open('$output_file').read())
" > "$text_file" 2>/dev/null

    _extract_and_write_code "$text_file" "$target_file" "$REPO_DIR" \
        > "$output_dir/run_${run_num}.extract" 2>&1 || true

    local verify_status=0
    "$verifier" "$REPO_DIR" > "$verify_file" 2>&1 || verify_status=$?
    local verify_result="pass"
    [[ $verify_status -eq 0 ]] || verify_result="fail"

    if [[ "$verify_result" == "fail" ]]; then
        local reason; reason=$(head -1 "$verify_file" 2>/dev/null || echo "unknown")
        echo -e "          ${RED}[verify fail]${RESET} $reason"
    fi

    cat > "$timing_file" << TIMING_EOF
{
    "backend": "$backend",
    "task": "$task_name",
    "run": $run_num,
    "wall_clock_seconds": $elapsed,
    "exit_code": $exit_code,
    "verify": "$verify_result",
    "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
TIMING_EOF

    local verify_label="${GREEN}[PASS]${RESET}"
    [[ "$verify_result" == "pass" ]] || verify_label="${RED}[FAIL]${RESET}"
    echo -e "          ${elapsed}s   verify:${verify_label}   ${task_name} (run ${run_num})"
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
_generate_summary() {
    local backend="$1"
    header "Summary: $backend"

    printf "%-35s %10s %10s %10s %8s %10s\n" "Task" "Run1(s)" "Run2(s)" "Run3(s)" "Avg(s)" "PassRate"
    printf -- "%-35s %10s %10s %10s %8s %10s\n" "----" "-------" "-------" "-------" "------" "--------"

    local total_runs=0 total_pass=0

    for i in "${!TASK_NAMES[@]}"; do
        local task_name="${TASK_NAMES[$i]}"
        local times=() verifies=()
        for run in $(seq 1 $RUNS); do
            local tf="$RESULTS_DIR/${backend}/${task_name}/run_${run}.timing"
            if [[ -f "$tf" ]]; then
                times+=("$(python3 -c "import json; print(json.load(open('$tf'))['wall_clock_seconds'])")")
                verifies+=("$(python3 -c "import json; print(json.load(open('$tf'))['verify'])")")
            else
                times+=("-"); verifies+=("-")
            fi
        done

        local avg="-" pass_count=0
        avg=$(python3 -c "
vals=[float(x) for x in '${times[*]}'.split() if x!='-']
print(round(sum(vals)/len(vals),2)) if vals else print('-')
")
        for v in "${verifies[@]}"; do
            [[ "$v" == "pass" ]] && pass_count=$((pass_count+1)) || true
        done

        total_runs=$((total_runs+RUNS))
        total_pass=$((total_pass+pass_count))

        printf "%-35s %10s %10s %10s %8s %10s\n" \
            "$task_name" "${times[0]:-'-'}" "${times[1]:-'-'}" "${times[2]:-'-'}" "$avg" "${pass_count}/${RUNS}"
    done

    echo ""
    local pct=0
    [[ $total_runs -gt 0 ]] && pct=$(python3 -c "print(round($total_pass/$total_runs*100,1))")
    echo -e "  ${BOLD}Overall: ${total_pass}/${total_runs} (${pct}%)${RESET}"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    local mode="${1:-}"
    if [[ -z "$mode" ]]; then
        echo "Usage: $0 {bedrock|local|both}"
        echo ""
        echo "  bedrock   — Claude via Amazon Bedrock"
        echo "  local     — Open-source model via SSH tunnel (localhost:${LOCAL_MODEL_PORT})"
        echo "  both      — Run both and compare"
        echo ""
        echo "9 coding tasks x${RUNS} runs = $((9*RUNS)) total. Results saved to results/"
        exit 1
    fi

    [[ -n "${CLAUDECODE:-}" ]] && fail "Cannot run inside a Claude Code session. Open a new terminal."

    header "LLM Coding Benchmark"
    info "Sample project: $SAMPLE_DIR"
    info "Results: $RESULTS_DIR"
    info "Tasks: ${#TASK_NAMES[@]} x${RUNS} runs"

    local backends=()
    [[ "$mode" == "both" ]] && backends=("bedrock" "local") || backends=("$mode")

    for backend in "${backends[@]}"; do
        header "Backend: $backend"
        _setup_backend "$backend"

        for i in "${!TASK_NAMES[@]}"; do
            header "Task: ${TASK_NAMES[$i]}"
            for run in $(seq 1 $RUNS); do
                _run_task "$backend" "$i" "$run"
            done
        done

        _generate_summary "$backend"
    done

    if [[ "$mode" == "both" ]]; then
        header "Comparison: Bedrock vs Local"
        printf "%-35s %12s %8s %12s %8s\n" "Task" "Bedrock(s)" "B-Pass" "Local(s)" "L-Pass"
        printf -- "%-35s %12s %8s %12s %8s\n" "----" "----------" "------" "--------" "------"
        for task_name in "${TASK_NAMES[@]}"; do
            local avg_b avg_l pass_b pass_l
            for bk in bedrock local; do
                local av pv
                av=$(python3 -c "
import json, glob
files=glob.glob('$RESULTS_DIR/$bk/$task_name/run_*.timing')
vals=[json.load(open(f))['wall_clock_seconds'] for f in files]
print(round(sum(vals)/len(vals),2)) if vals else print('-')
")
                pv=$(python3 -c "
import json, glob
files=glob.glob('$RESULTS_DIR/$bk/$task_name/run_*.timing')
n=sum(1 for f in files if json.load(open(f)).get('verify')=='pass')
print(f'{n}/$RUNS')
")
                [[ "$bk" == "bedrock" ]] && avg_b="$av" && pass_b="$pv" || true
                [[ "$bk" == "local" ]] && avg_l="$av" && pass_l="$pv" || true
            done
            printf "%-35s %12s %8s %12s %8s\n" "$task_name" "$avg_b" "$pass_b" "$avg_l" "$pass_l"
        done
    fi

    header "Done"
    info "Review outputs: ls $RESULTS_DIR/${mode}/*/run_*.txt"
}

main "$@"
