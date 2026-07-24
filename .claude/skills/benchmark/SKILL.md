---
name: benchmark
description: "Run one end-to-end SWE benchmark of an LLM on real coding tasks with Claude Code, on any of the three hosting paths (Anthropic on Bedrock, open-weight on Bedrock via the LiteLLM proxy, or a self-hosted vLLM server). Drives the full flow: pre-flight and error checks, clearing stale artifact folders, running the benchmark harness over a dataset, and scoring the artifacts with the codex judge. Use when the user wants to benchmark a model, run the SWE harness end to end, score a model on mcp-gateway-registry (or any dataset), or compare models on coding tasks. Wraps benchmarks/scripts/run-e2e-benchmark.sh and tells the user how to watch each step."
license: Apache-2.0
metadata:
  author: Amit Arora
  version: "1.0"
---

# Benchmark Skill

Use this skill to run **one complete SWE benchmark end to end** for a chosen model: bring up the backing service, pre-flight checks, the harness run over a dataset, and scoring with the judge. It is the interactive front end to [`benchmarks/scripts/run-e2e-benchmark.sh`](../../../benchmarks/scripts/run-e2e-benchmark.sh); it collects three inputs, surfaces the exact command to watch each long-running step, and fails loudly with an actionable message the moment anything is wrong. For the **vllm** path it also manages the server (starts it on the requested model, stopping any other model first) and the DuckDB metrics collector (starts it before the run, stops it and archives the snapshot after).

All the real logic lives in the shell script and its Python helpers ([`preflight_check.py`](../../../benchmarks/scripts/preflight_check.py), [`run-swe-headless.py`](../../../benchmarks/scripts/run-swe-headless.py), [`codex_judge.py`](../../../benchmarks/scripts/codex_judge.py)). This skill orchestrates them and reports. The concepts and the per-path setup are documented in [`benchmarks/README.md`](../../../benchmarks/README.md) and [`benchmarks/docs/`](../../../benchmarks/docs/); the full manual run-book is [`benchmarks/docs/end-to-end-self-hosted-run.md`](../../../benchmarks/docs/end-to-end-self-hosted-run.md).

## The three inputs

Collect these three, in order. Do not guess -- ask if any is missing.

1. **provider** -- one of:
   - `bedrock` -- Anthropic models (Claude Opus/Sonnet/Haiku) directly on Amazon Bedrock.
   - `litellm` -- open-weight models on Amazon Bedrock through the LiteLLM mantle proxy (Kimi, Qwen, DeepSeek, Mistral, ...).
   - `vllm` -- a model you self-host on a local vLLM server (`127.0.0.1:8000`).
2. **model** -- the model id / served-model-name. Examples: `us.anthropic.claude-opus-4-8` (bedrock), `moonshotai.kimi-k2-thinking` (litellm), `qwen3-coder-30b` (vllm).
3. **dataset** -- a dataset YAML under `benchmarks/dataset/`. Default to `dataset/mcp-gateway-registry.yaml`; suggest `dataset/hello-world.yaml` for a quick sanity check.

## Workflow

1. **Gather the three inputs** -- provider, model, dataset. Confirm them back to the user.
2. **Bring up / confirm the backing service** for the chosen path. For **vllm**: check the HF token, then (re)start the vLLM server on the requested model -- stopping any other model first -- using the model guide's serve command at its largest context window; **require the served window to be at least 200K or STOP** (a smaller window is unsuited to agentic coding and fails on turn 1); then start the DuckDB collector. For litellm/bedrock: confirm the proxy/credentials.
3. **Dry-run the pre-flight** so the user sees what will happen before anything runs.
4. **Run the orchestrator**, streaming its output, and tell the user what to tail.
5. **Wrap up and report**: (vllm) stop the collector and archive its DuckDB snapshot tagged with model/scope/timestamp; write a `RUN-SUMMARY.md` (results table + notes) into the run's `{model-slug}/{scope}/` folder; then report where the results landed.

Keep the user informed at every step: before each long-running command, print it verbatim and give the tail/status command to watch it.

---

## Step 1 - Gather and confirm the three inputs

Ask for provider, model, and dataset if the user did not already give them. Then restate the plan and the folder the results will land in:

> I will run an end-to-end benchmark:
> - provider: **{provider}**
> - model: **{model}**
> - dataset: **{dataset}**
>
> Results will land under `benchmarks/swe-benchmark-data/{model-slug}/<repo>/<task>/`. Proceed?

Wait for confirmation before running anything.

## Step 2 - Confirm (and for vllm, bring up) the backing service

The orchestrator re-checks the service and fails loudly, but for the **vllm** path this skill actively brings the server up on the requested model. Handle the path the user chose:

### provider = vllm -- ensure the server is serving `{model}`

Do these in order. Do not skip the HF-token check; a missing token is the most common cause of a stalled first download.

**2a. HuggingFace token must be available BEFORE starting vLLM.** `vllm-serve.sh` resolves a token from `$HF_TOKEN`, else a `.hf_token` file in the repo root, the `self-hosted/vllm/` dir, or `$HOME`. Check that at least one is present:

```bash
cd /home/ubuntu/repos/aarora79/sample-claude-code-multi-model
if [ -n "${HF_TOKEN:-}" ] || [ -s .hf_token ] || [ -s self-hosted/vllm/.hf_token ] || [ -s "$HOME/.hf_token" ]; then
  echo "HF token available"
else
  echo "NO HF TOKEN"
fi
```

If it prints `NO HF TOKEN`, **stop and ask the user to provide one** before continuing -- e.g. write it to `.hf_token` in the repo root (the file is gitignored):

> No HuggingFace token found. vLLM needs one to download `{model}` at a usable speed (without it, HF's anonymous rate limits can make a 60-160 GB download crawl or stall). Please provide a token: write it to `.hf_token` in the repo root, or export `HF_TOKEN`. Let me know once it's set and I'll continue.

Do not print or echo the token value. **Note:** the serve script reads `.hf_token` (with an underscore). If the user only has a `.hftoken` file, tell them to rename it to `.hf_token` or export `HF_TOKEN`, because the script will not pick up `.hftoken`.

**2b. Check what vLLM is currently serving (if anything):**

```bash
curl -s http://127.0.0.1:8000/v1/models | python3 -c "import sys,json; print([m['id'] for m in json.load(sys.stdin).get('data',[])])" 2>/dev/null || echo "not running"
```

- If it already serves `{model}` -> nothing to do, move on.
- If it serves a **different** model -> stop it first, then start the requested one:
  ```bash
  cd self-hosted/vllm/scripts && ./vllm-serve.sh --stop
  ```
- If it is **not running** -> start the requested one.

**2c. Start vLLM on `{model}` using the parameters from its model guide.** Read `self-hosted/vllm/models/{model}.md` (e.g. `qwen3.6-35b-a3b.md`) and use the serve command it documents, choosing the **largest practical context window** that guide endorses (the "Serve it" block's `MAX_MODEL_LEN`; do not exceed the guide's recommended max, since a bigger window can fail to boot at useful concurrency). Copy that guide's `MODEL`, `SERVED_NAME`, `TOOL_PARSER`, and `MAX_MODEL_LEN` exactly. `vllm-serve.sh` always tees its log to `self-hosted/vllm/logs/vllm-serve.log`.

```bash
cd self-hosted/vllm/scripts
# Values below come straight from self-hosted/vllm/models/{model}.md -- do not invent them.
MODEL="<HF repo from the guide>" \
SERVED_NAME="{model}" \
TP=4 \
PORT=8000 \
MAX_MODEL_LEN="<largest window the guide endorses>" \
GPU_MEM_UTIL=0.90 \
TOOL_PARSER="<parser from the guide>" \
  ./vllm-serve.sh
# The launcher blocks until the server is ready (first download can take minutes).
# Watch progress in another terminal:  tail -f self-hosted/vllm/logs/vllm-serve.log
```

If there is no guide for `{model}` under `self-hosted/vllm/models/`, tell the user and ask for the HF repo id, tool-call parser, and desired context window rather than guessing.

**2d. Context-window gate -- REQUIRE at least 200K, or STOP.** Agentic coding tasks on real repositories routinely need 100K-250K input tokens in a single request (the `/swe` skill prompt alone is ~12K, and reading repo files pushes far higher). A context window below **200000** is not suited for these tasks: the model overflows the window on turn 1 (before auto-compaction can help, since the first prompt already does not fit) and every task fails. This is a node/VRAM limitation, not a model-quality signal.

After the server reports ready, read the window it actually booted with and hard-stop if it is under 200000:

```bash
WINDOW="$(curl -s -m 5 http://127.0.0.1:8000/v1/models \
  | python3 -c 'import sys,json; d=json.load(sys.stdin).get("data",[]); print(next((m.get("max_model_len") for m in d if m.get("max_model_len")), 0))' 2>/dev/null || echo 0)"
echo "served context window: $WINDOW"
if [ "$WINDOW" -lt 200000 ]; then echo "WINDOW TOO SMALL -- STOP"; fi
```

If it prints `WINDOW TOO SMALL -- STOP`, **do not run the benchmark.** Stop here and tell the user the model cannot be served at a usable window on this node, e.g.:

> `{model}` booted at a {WINDOW}-token context window on this node, but agentic coding tasks need at least 200K. A window this small overflows on the first prompt and every task fails, so I am not starting the run. This is a VRAM limitation of the current machine, not the model. Options: benchmark a model that fits a >=200K window on this node (e.g. `qwen3.6-35b` or `qwen3-coder-30b`), or serve `{model}` on a larger-VRAM node (see its model guide for the required instance) where a 200K-256K window fits.

Stop the collector if it was already started, leave the notes for the user, and do not proceed to Step 3. (This gate is why Step 2c picks the *largest* window the guide endorses -- if even that is under 200K, the model is not benchmarkable here.)

**2e. Start the DuckDB metrics collector** so a GPU time series is captured for the whole run (it is stopped and the snapshot archived in Step 5):

```bash
cd self-hosted/vllm/scripts && ./vllm-metrics.sh start && ./vllm-metrics.sh status
```

### provider = litellm -- the LiteLLM proxy must be up on `127.0.0.1:4000`

```bash
cd benchmarks && ./scripts/bedrock-mantle-proxy.sh --status
# if not running:
./scripts/bedrock-mantle-proxy.sh
tail -f benchmarks/.litellm.log
```

### provider = bedrock -- confirm AWS credentials

```bash
aws sts get-caller-identity
```

## Step 3 - Pre-flight (see what will happen first)

Show the user which artifact folders already exist for this model+dataset (a pre-existing folder makes the headless `/swe` run stall on its overwrite prompt). This is a read-only check:

```bash
cd benchmarks
uv run python scripts/preflight_check.py --dataset {dataset} --model {model} --check
```

- Exit 0: nothing exists, safe to run.
- Exit 2: folders exist. Ask the user whether to **clear** them (a fresh run) or **keep** them (rename to preserve the prior run). If clearing, the orchestrator does it automatically when passed `--yes`; or clear explicitly:
  ```bash
  uv run python scripts/preflight_check.py --dataset {dataset} --model {model} --clear
  ```

## Step 4 - Run the orchestrator

Run the end-to-end script from `benchmarks/`. It re-runs every pre-flight check (fail-loud), runs the harness with `--stream`, then scores with the judge. Pass `--yes` only after the user has agreed to clear any existing folders in Step 3.

```bash
cd benchmarks
./scripts/run-e2e-benchmark.sh --provider {provider} --model {model} --dataset {dataset} [--yes] [--count N] [--skip-judge]
```

Tell the user, before it runs:

- The harness streams a live trace; add `--count 1` to try a single task first on a big dataset.
- **vllm path** -- watch the server and GPU metrics in another terminal:
  ```bash
  tail -f self-hosted/vllm/logs/vllm-serve.log
  cd self-hosted/vllm && uv run python -m clients.build_dashboard && echo "open benchmark-output/dashboard.html"
  ```
- **litellm path** -- watch the proxy: `tail -f benchmarks/.litellm.log`
- The judge (`codex exec`) buffers output and prints only its final message per folder, so a few minutes each at high effort is normal; it is working, not hung.

If the run is long, remind the user they can run only the harness now and score later with `--skip-judge`, then:
```bash
cd benchmarks/scripts && uv run python codex_judge.py --recursive --no-overwrite --folder ../swe-benchmark-data/{model-slug}
```

## Step 5 - Wrap up (stop the collector, archive the DuckDB snapshot, write the run summary) and report

**5a. (vllm path) Stop the DuckDB metrics collector** now that the run is done, so it stops appending to the live database:

```bash
cd self-hosted/vllm/scripts && ./vllm-metrics.sh stop
```

**5b. (vllm path) Archive the DuckDB snapshot** by renaming the live database to one tagged with the model, dataset/task scope, and a timestamp -- so it is preserved for this run and the next run starts from a fresh, empty database. The live file is `self-hosted/vllm/benchmark-output/vllm-metrics.duckdb`. Build the timestamp from the actual current time (do not hardcode it), and use the dataset's repo name as the task scope:

```bash
cd self-hosted/vllm/benchmark-output
TS="$(date -u +%Y%m%dT%H%M%SZ)"
# {model-slug} = the model folder name; {scope} = the dataset repo (e.g. mcp-gateway-registry)
mv vllm-metrics.duckdb "vllm-metrics_{model-slug}_{scope}_${TS}.duckdb"
echo "archived snapshot: vllm-metrics_{model-slug}_{scope}_${TS}.duckdb"
```

If the collector was never started (or this is not the vllm path), skip 5a/5b. If you want a dashboard from the archived snapshot, render it before or after the rename by pointing `--db` at the file:

```bash
cd self-hosted/vllm && uv run python -m clients.build_dashboard \
  --db benchmark-output/vllm-metrics_{model-slug}_{scope}_{timestamp}.duckdb \
  --output benchmark-output/dashboard_{model-slug}_{scope}_{timestamp}.html
```

**5c. Write a run summary** to `benchmarks/swe-benchmark-data/{model-slug}/{scope}/RUN-SUMMARY.md` (one per model+dataset run, alongside the per-task folders) so the outcome is captured on disk, not just in the chat. Do this on **every** path (bedrock/litellm/vllm), after scoring. Gather the numbers from the artifacts rather than retyping them -- read each task's `metrics.json` (`metrics_that_matter`: `num_turns`, `input_tokens`, `output_tokens`, `latency_seconds`) and `eval.json` (`task_score`):

```bash
cd benchmarks/swe-benchmark-data/{model-slug}/{scope}
python3 - <<'PY'
import json, os
rows=[]
for t in sorted(os.listdir('.')):
    if not os.path.isdir(t):
        continue
    m=json.load(open(f'{t}/metrics.json')).get('metrics_that_matter',{})
    e=json.load(open(f'{t}/eval.json'))
    rows.append((t, m.get('num_turns'), m.get('input_tokens'),
                 m.get('output_tokens'), m.get('latency_seconds'), e.get('task_score')))
for r in sorted(rows, key=lambda r: -(r[5] or 0)):
    print(r)
PY
```

Then write `RUN-SUMMARY.md` with this structure (fill the front-matter from the run's actual inputs; sort the table by judge score, highest first):

```markdown
# Benchmark run summary: {model} on {scope}

- Provider: {provider}
- Model: {model}
- Dataset: {dataset} ({N} tasks, ref {ref})
- Served context window: {window if vllm/litellm, else n/a}; auto-compaction at {floor(window*0.9)}
- Run date: {UTC date}

{one-line headline: how many tasks succeeded (K/N with 4/4 artifacts), and whether any retries/crashes/compaction events occurred}

## Results

| Task | Turns | Input tokens | Output tokens | Latency (s) | Judge score |
|---|---|---|---|---|---|
| ... one row per task, sorted by judge score desc ... |

Token counts are cumulative across all turns of a task, not a single request. The judge is calibrated so a median artifact lands around 60-70.

## Notes

- {anything notable: did compaction fire? were there failures and why? server/window changes made mid-run?}
- Each task is a fresh `claude -p` process: independent context, per-task window, no cross-task carryover.

## Artifacts

- Per-task results under `benchmarks/swe-benchmark-data/{model-slug}/{scope}/<task>/` (four design artifacts + `metrics.json` + `eval.json`).
- {if vllm: GPU time series snapshot path from 5b}
```

If a task failed (fewer than 4/4 artifacts, or `is_error`), say so in the headline and note the cause from its `metrics.json` (`error`/`api_error_status`). Do not present a partial run as a clean sweep.

**5d. Report** where the results are and what they contain:

- Point the user at the `RUN-SUMMARY.md` you just wrote first.
- Each `benchmarks/swe-benchmark-data/{model-slug}/<repo>/<task>/` holds the four artifacts, `metrics.json` (cost + any vLLM server metrics), and `eval.json` (quality scores).
- The same task run by another model lands under a sibling top-level `{model-slug}/` folder, directly comparable.
- Suggest inspecting one result:
  ```bash
  cat benchmarks/swe-benchmark-data/{model-slug}/<repo>/<task>/eval.json
  ```
- If a DuckDB snapshot was archived (5b), point the user at it.

## Notes

- **This skill manages the vLLM server and the DuckDB collector for the `vllm` path** (Step 2 brings the server up on the requested model, stopping any other model first; Step 5 stops the collector and archives its snapshot). It does **not** manage the LiteLLM proxy -- that is a long-lived service with its own script (`bedrock-mantle-proxy.sh`); the skill only checks it is up.
- **provider = bedrock is Anthropic-only.** For non-Anthropic Bedrock models use `litellm`. The orchestrator warns if a non-Anthropic id is passed with `bedrock`.
- **The model slug is not always the model id.** For a Bedrock inference profile the folder name drops the `us.anthropic.` prefix and any `[...]` suffix (e.g. `us.anthropic.claude-opus-4-8` -> `claude-opus-4-8`); a served name like `qwen3-coder-30b` is unchanged. The pre-flight helper and the orchestrator both compute this the same way the harness does.
- Every script takes `--help`.
