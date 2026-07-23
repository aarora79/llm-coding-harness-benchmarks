---
name: benchmark
description: "Run one end-to-end SWE benchmark of an LLM on real coding tasks with Claude Code, on any of the three hosting paths (Anthropic on Bedrock, open-weight on Bedrock via the LiteLLM proxy, or a self-hosted vLLM server). Drives the full flow: pre-flight and error checks, clearing stale artifact folders, running the benchmark harness over a dataset, and scoring the artifacts with the codex judge. Use when the user wants to benchmark a model, run the SWE harness end to end, score a model on mcp-gateway-registry (or any dataset), or compare models on coding tasks. Wraps benchmarks/scripts/run-e2e-benchmark.sh and tells the user how to watch each step."
license: Apache-2.0
metadata:
  author: Amit Arora
  version: "1.0"
---

# Benchmark Skill

Use this skill to run **one complete SWE benchmark end to end** for a chosen model: pre-flight checks, the harness run over a dataset, and scoring with the judge. It is the interactive front end to [`benchmarks/scripts/run-e2e-benchmark.sh`](../../../benchmarks/scripts/run-e2e-benchmark.sh); it collects three inputs, surfaces the exact command to watch each long-running step, and fails loudly with an actionable message the moment anything is wrong.

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
2. **Confirm the backing service is up** for the chosen path (this skill does NOT start vLLM or the proxy -- they are long-lived services). Point the user at the right start command if not.
3. **Dry-run the pre-flight** so the user sees what will happen before anything runs.
4. **Run the orchestrator**, streaming its output, and tell the user what to tail.
5. **Report** where the results landed.

Keep the user informed at every step: before each long-running command, print it verbatim and give the tail/status command to watch it.

---

## Step 1 - Gather and confirm the three inputs

Ask for provider, model, and dataset if the user did not already give them. Then restate the plan and the folder the results will land in:

> I will run an end-to-end benchmark:
> - provider: **{provider}**
> - model: **{model}**
> - dataset: **{dataset}**
>
> Results will land under `benchmarks/swe-benchmark-data/<repo>/<task>/{model-slug}/`. Proceed?

Wait for confirmation before running anything.

## Step 2 - Confirm the backing service (per path)

The orchestrator checks this too and fails loudly, but confirm early so the user is not surprised.

- **provider = vllm** -- a vLLM server must already serve `{model}` on `127.0.0.1:8000`:
  ```bash
  curl -s http://127.0.0.1:8000/v1/models | python3 -m json.tool
  ```
  If it is not up, or serves a different model, tell the user to start it (from the repo root):
  ```bash
  cd self-hosted/vllm/scripts
  MODEL=<HF repo> SERVED_NAME={model} MAX_MODEL_LEN=200000 ./vllm-serve.sh
  # watch it come up:
  tail -f self-hosted/vllm/logs/vllm-serve.log
  ```
  Also recommend starting the DuckDB metrics collector so a GPU time series is captured during the run (see [end-to-end run-book](../../../benchmarks/docs/end-to-end-self-hosted-run.md), Step 2):
  ```bash
  cd self-hosted/vllm/scripts && ./vllm-metrics.sh start && ./vllm-metrics.sh status
  ```

- **provider = litellm** -- the LiteLLM proxy must be up on `127.0.0.1:4000`:
  ```bash
  cd benchmarks && ./scripts/bedrock-mantle-proxy.sh --status
  # if not running:
  ./scripts/bedrock-mantle-proxy.sh
  tail -f benchmarks/.litellm.log
  ```

- **provider = bedrock** -- confirm AWS credentials:
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
cd benchmarks/scripts && uv run python codex_judge.py --recursive --no-overwrite --folder ../swe-benchmark-data/<repo>
```

## Step 5 - Report

When it finishes, tell the user where the results are and what they contain:

- Each `benchmarks/swe-benchmark-data/<repo>/<task>/{model-slug}/` holds the four artifacts, `metrics.json` (cost + any vLLM server metrics), and `eval.json` (quality scores).
- The same task run by another model or path lands in a sibling `{model-slug}/` folder, directly comparable.
- Suggest inspecting one result:
  ```bash
  cat benchmarks/swe-benchmark-data/<repo>/<task>/{model-slug}/eval.json
  ```

## Notes

- **This skill does not start or stop vLLM or the LiteLLM proxy.** Those are long-lived services with their own scripts (`vllm-serve.sh`, `bedrock-mantle-proxy.sh`). The skill only checks they are up and tells the user how to start them.
- **provider = bedrock is Anthropic-only.** For non-Anthropic Bedrock models use `litellm`. The orchestrator warns if a non-Anthropic id is passed with `bedrock`.
- **The model slug is not always the model id.** For a Bedrock inference profile the folder name drops the `us.anthropic.` prefix and any `[...]` suffix (e.g. `us.anthropic.claude-opus-4-8` -> `claude-opus-4-8`); a served name like `qwen3-coder-30b` is unchanged. The pre-flight helper and the orchestrator both compute this the same way the harness does.
- Every script takes `--help`.
