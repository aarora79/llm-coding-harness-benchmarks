# Coding Benchmark

Compare the quality and speed of your self-hosted open-source model against Claude via Amazon Bedrock
on real coding tasks. The benchmark runs 9 tasks across 3 difficulty tiers, each with an automated
pass/fail verifier. No manual review needed.

---

## Prerequisites

On your **local machine**:

```bash
# Claude Code installed
npm install -g @anthropic-ai/claude-code

# Python 3 and pytest
python3 --version
pip install pytest
```

For the **local model** backend: SSH tunnel must be active before running.

```bash
export G6E_IP=<your-ec2-instance-ip>
export G6E_KEY=~/.ssh/<your-key>.pem
./scripts/tunnel.sh start
```

For the **bedrock** backend: AWS credentials configured with Bedrock access.

```bash
aws sts get-caller-identity   # verify credentials
```

---

## Running the Benchmark

```bash
# Compare both backends back-to-back
./scripts/bench.sh both

# Run only the local model
./scripts/bench.sh local

# Run only Amazon Bedrock
./scripts/bench.sh bedrock

# Use llama.cpp instead of Ollama (port 8131)
LOCAL_MODEL_PORT=8131 ./scripts/bench.sh local
```

Each task runs **3 times**. Results are saved to `results/<backend>/<task>/`.

---

## The Sample Project

All tasks operate on `sample/` — a minimal Python web application built with FastAPI and httpx.

```
sample/
├── app.py               # FastAPI app entry point
├── version.py           # __version__ string
├── api/
│   └── routes.py        # API route handlers (/health, /ping)
├── core/
│   └── client.py        # HTTP client (sync + async)
└── utils/
    ├── path_utils.py    # normalize_path(), extract_name(), validate_name()
    └── validator.py     # DataValidator class
```

The model is given the current file content and a precise instruction. It must return valid,
runnable Python. The verifier checks the result automatically.

---

## The 9 Tasks

### Tier 1 — Simple
Single, well-scoped change to one file. Tests whether the model can follow exact instructions
without breaking existing code.

| Task | File | What the model must do |
|---|---|---|
| `c1_add_helper_function` | `utils/path_utils.py` | Add `sanitize_name(name: str) -> str` — lowercase, replace spaces with hyphens, strip non-alphanumeric |
| `c1_modernize_type_hints` | `utils/validator.py` | Replace all `Optional[X]` with `X \| None` (PEP 604 syntax) |
| `c1_add_input_validation` | `utils/path_utils.py` | Add `None`, type, and empty string guards at the top of `normalize_path()` |

### Tier 2 — Medium
Non-trivial logic, or requires creating a new file from scratch.

| Task | File | What the model must do |
|---|---|---|
| `c2_add_logging` | `utils/path_utils.py` | Add `import logging`, create a module-level logger, add `logger.debug()` calls in `normalize_path()` |
| `c2_write_tests` | `tests/unit/test_path_utils.py` | Write a pytest file with 9+ test functions covering all three functions in `path_utils.py` |
| `c2_implement_retry` | `utils/retry.py` | Create `retry_sync()` and `retry_async()` with configurable attempts, delay, and exception filter |

### Tier 3 — Complex
Multi-file awareness or architectural change. The model must understand how files relate to each other.

| Task | File | What the model must do |
|---|---|---|
| `c3_add_version_endpoint` | `api/routes.py` | Add `GET /version` endpoint that imports `__version__` from `sample/version.py` |
| `c3_add_timing_middleware` | `app.py` | Add `@app.middleware("http")` that measures request duration and adds `X-Response-Time` header |
| `c3_refactor_client_di` | `core/client.py` | Add module-level `_http_client`, `set_http_client()`, and `get_http_client()` for dependency injection |

---

## How Verification Works

Each task has a dedicated verifier function in `bench.sh`. Verifiers run automatically after
the model writes its output. They check:

1. **Syntax** — `python3 -m py_compile` confirms the file is valid Python
2. **Structure** — `grep` checks for required function names, imports, patterns
3. **Runtime** — Python assertions execute the new code with specific inputs and assert correct output

For example, `c1_add_helper_function` verifies:
```python
assert sanitize_name("My Widget!")    == "my-widget"
assert sanitize_name("  hello world") == "hello-world"
assert sanitize_name("--test--")      == "test"
```

A task **passes** only when all three checks pass. Partial credit is not given.

---

## Reading the Results

After each backend completes, a summary table is printed:

```
Task                                Run1(s)    Run2(s)    Run3(s)   Avg(s)   PassRate
----                                -------    -------    -------   ------   --------
c1_add_helper_function                 12.3       11.8       13.1     12.4      3/3
c1_modernize_type_hints                 8.1        7.9        8.4      8.1      3/3
c1_add_input_validation                 9.5       10.2        9.8      9.8      3/3
c2_add_logging                         14.2       13.7       15.1     14.3      3/3
c2_write_tests                         45.2       48.1       43.9     45.7      2/3
c2_implement_retry                     38.4       41.2       37.8     39.1      3/3
c3_add_version_endpoint                22.1       20.8       23.4     22.1      3/3
c3_add_timing_middleware               18.9       19.4       17.8     18.7      2/3
c3_refactor_client_di                  31.2       29.8       33.1     31.4      3/3

Overall: 25/27 (92.6%)
```

When running `both`, a side-by-side comparison is printed at the end showing wall-clock time and
pass rate for each task across both backends.

---

## Raw Output

Every run saves its output to `results/`:

```
results/
└── local/
    └── c2_write_tests/
        ├── run_1.json      # Full Claude Code JSON output
        ├── run_1.txt       # Extracted text (model response)
        ├── run_1.timing    # Timing + pass/fail metadata
        └── run_1.verify    # Verifier output (pass reason or failure message)
```

To read a model response:
```bash
cat results/local/c2_write_tests/run_1.txt
```

To check why a task failed:
```bash
cat results/local/c2_write_tests/run_1.verify
```

---

## Resetting Between Runs

The benchmark resets `sample/` to its original state before each task using `git checkout`.
If you want to manually reset:

```bash
git checkout -- sample/
git clean -fd -- sample/utils/retry.py sample/tests/unit/test_path_utils.py
```
