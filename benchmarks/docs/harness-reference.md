# Harness reference (shared across all three paths)

This is the shared operational reference for the benchmark harness. The **dataset format**, the **dataset loader**, the **runner config**, the way the **harness invokes `claude -p`**, the **metrics file**, and the **judge** are identical no matter which of the three hosting paths you use. The path-specific setup (how `claude -p` actually reaches the model) lives in the per-path guides:

- [Path 1 - Anthropic models directly on Amazon Bedrock](path-anthropic-on-bedrock.md)
- [Path 2 - open-weight models on Amazon Bedrock via a LiteLLM proxy](path-open-weight-on-bedrock-litellm.md)
- [Path 3 - self-hosted open-weight models on EC2 with vLLM](path-self-hosted-vllm.md)

Start at the [top-level README](../README.md) for the concepts and to pick a path.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) for package and environment management.
- Python 3.10+.

The benchmark harness has its own virtual environment, isolated from the model runtimes elsewhere in this repository. Set it up once:

```bash
cd benchmarks
uv sync
```

This creates `benchmarks/.venv` with `pydantic`, `pyyaml`, `requests`, `matplotlib`, and `numpy`, plus the dev tools (`ruff`, `mypy`, `bandit`). Run everything in this directory with `uv run`.

## The dataset

A dataset is a single YAML file: a metadata header plus a list of tasks. Datasets live in [dataset/](../dataset/); the reference dataset is [dataset/mcp-gateway-registry.yaml](../dataset/mcp-gateway-registry.yaml), whose tasks are drawn from real upstream issues in [agentic-community/mcp-gateway-registry](https://github.com/agentic-community/mcp-gateway-registry). Nothing in the harness is specific to a particular repository -- adding a new benchmark dataset is just writing another YAML file in this format.

### Top-level fields

| Field | Type | Description |
| --- | --- | --- |
| `schema_version` | str | Version of the file format. The loader only accepts versions it knows about. |
| `name` | str | Machine-friendly dataset id (kebab-case). |
| `title` | str | Human-readable dataset name. |
| `description` | str | What the dataset covers and how it is meant to run. |
| `created` | date | ISO date the dataset was authored (`YYYY-MM-DD`). Optional. |
| `default_ref` | str | Git ref (tag, branch, or commit) a task clones when it does not set its own `ref`. Pin this for reproducibility. |
| `metrics` | list | The per-run signals the harness is expected to collect. Documentary only; actual values live in run outputs, never in the dataset. |
| `complexity_levels` | list | The allowed values for a task's `complexity` field. |
| `tasks` | list | The tasks (see below). |

### Per-task fields

| Field | Type | Description |
| --- | --- | --- |
| `id` | str | Stable slug; also used as the output subdirectory name. Must be unique within the dataset. |
| `repo` | str | HTTPS URL of the repository the agent clones. |
| `ref` | str | Git ref for this task. Optional; defaults to `default_ref`. Always pin so runs are reproducible. |
| `complexity` | str | One of `complexity_levels`. |
| `tags` | list | Free-form labels for slicing results (domain, language, change type, AWS service, and so on). |
| `problem_statement` | str | Multi-line description of the task, in enough detail for the agent to act without the repo author present. |
| `problem_issue_url` | str | Canonical GitHub issue the task derives from. When both this and `problem_statement` are present, the statement is authoritative and the URL is the source of record. |
| `clarifying_answers` | str | Pre-supplied answers to the questions the `/swe` skill would otherwise ask, so the run stays fully non-interactive. Optional. |
| `ground_truth` | map | Reviewer-facing notes on the intended solution. **Never given to the agent.** Optional. |

At least one of `problem_statement` or `problem_issue_url` must be present on every task.

`ground_truth`, when present, has three sub-fields, all optional:

| Field | Type | Description |
| --- | --- | --- |
| `approach` | str | How the change is meant to be made. |
| `expectations` | list | Points a correct design is expected to cover. |
| `reference_url` | str | How the issue was actually resolved upstream (PR, commit, or issue), if known. |

### Minimal example

```yaml
schema_version: "1.0"
name: example-dataset
title: Example dataset
description: A minimal valid dataset.
default_ref: main
metrics: [input_tokens, output_tokens, num_turns]
complexity_levels: [low, medium, high]
tasks:
  - id: fix-the-thing
    repo: https://github.com/example/repo
    complexity: low
    tags: [demo]
    problem_statement: |
      Describe the task here, in enough detail for an agent to act on it.
```

## The dataset loader

[scripts/dataset_loader.py](../scripts/dataset_loader.py) parses a dataset file into typed [Pydantic](https://docs.pydantic.dev/) models and validates it, so every consumer reads the same enforced shape instead of poking at raw dictionaries.

It exposes three models -- `Dataset`, `Task`, and `GroundTruth` -- and a single entry point:

```python
from dataset_loader import load_dataset

dataset = load_dataset("dataset/hello-world.yaml")
for task in dataset.tasks:
    print(task.id, task.complexity, dataset.resolved_ref(task))

task = dataset.task_by_id("add-contributing-guide")
```

`load_dataset` raises `DatasetError` if the file is missing, unparseable, or fails validation. Validation enforces:

- The `schema_version` is one the loader supports.
- Every task's `complexity` is one of the dataset's `complexity_levels`.
- Task ids are unique.
- Every task has at least one problem source (`problem_statement` or `problem_issue_url`).

The loader also resolves each task's `ref` to `default_ref` when the task omits it, so downstream code always sees a concrete ref.

### Validating a dataset from the command line

The loader doubles as a CLI that validates a file and prints a summary -- useful when authoring or editing a dataset:

```bash
cd benchmarks
uv run scripts/dataset_loader.py dataset/hello-world.yaml
```

It exits non-zero and logs the validation error if the file is invalid.

## The runner config

The dataset says *what* to run; the runner config says *how* to run it. It is a small YAML file ([config/runner.example.yaml](../config/runner.example.yaml)) holding the run-time parameters: which provider, endpoint, and model to drive, which dataset to run, where artifacts go, and how `claude -p` is invoked. [scripts/runner_config.py](../scripts/runner_config.py) parses it into a validated Pydantic `RunnerConfig`.

The harness reaches models two ways, selected by the `provider` field: through an OpenAI/Anthropic-compatible **`endpoint`** (a local vLLM server, a LiteLLM proxy, a gateway, or the Anthropic API -- the default), or directly on **Amazon Bedrock** (`provider: bedrock`). Which one each hosting path uses is spelled out in that path's guide.

Every field can be overridden on the command line, and **CLI flags always win over the file**, so a committed config stays the reusable default while one-off runs stay flexible.

### Setup: create your own config

`config/runner.example.yaml` is a template, not a config you run directly. Before your first run, copy it to `config/runner.yaml` and edit it for your endpoint:

```bash
cd benchmarks
cp config/runner.example.yaml config/runner.yaml
# then edit config/runner.yaml: set your endpoint (and api_key if needed)
```

`config/runner.yaml` is gitignored, so your local endpoint and key never get committed. Keep `config/runner.example.yaml` as the checked-in, documented template; point the harness at your copy with `--config config/runner.yaml`. Leave `settings_file` commented out unless you specifically need the options in the vLLM settings file -- the harness synthesizes routing from `endpoint` and `api_key` on its own.

The two values that change from run to run -- **`model`** and **`dataset`** -- are deliberately left unset in the template. Pass them on the command line so one config file serves every model and dataset instead of maintaining a file per combination:

```bash
uv run scripts/run-swe-headless.py --config config/runner.yaml \
    --model qwen3-coder-30b --dataset dataset/mcp-gateway-registry.yaml
```

CLI flags always win, so you can still pin `model`/`dataset` in the file if you prefer a fixed default. If neither the file nor the CLI supplies them, the run fails fast with a clear error.

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `provider` | str | `endpoint` | How `claude -p` reaches the model: `endpoint` (a base URL) or `bedrock` (native Amazon Bedrock). |
| `endpoint` | str | -- | Base URL of the OpenAI/Anthropic-compatible endpoint the model is served on. Required for `provider: endpoint`; ignored for `provider: bedrock`. |
| `model` | str | -- | Model name/id passed to `claude --model`. For `provider: bedrock` this is a Bedrock model id or inference profile (e.g. `us.anthropic.claude-opus-4-8`); the harness strips the vendor/region prefix and any `[...]` suffix to derive the `{model-name}` artifact top-level folder (so `us.anthropic.claude-opus-4-8` writes under `claude-opus-4-8/`). Usually supplied with `--model` rather than pinned in the file; required from one source or the other. |
| `api_key` | str | `local` | API key sent to the endpoint (local servers ignore the value). `provider: endpoint` only. |
| `aws_region` | str | -- | AWS region for `provider: bedrock` (e.g. `us-east-1`). Falls back to `AWS_REGION` / `AWS_DEFAULT_REGION` from the environment when unset. |
| `dataset` | str | -- | Path to the dataset YAML (relative to `benchmarks/`). Usually supplied with `--dataset` rather than pinned in the file; required from one source or the other. |
| `output_dir` | str | `swe-benchmark-data` | Directory (under `benchmarks/`) where the `/swe` skill writes artifacts. |
| `clone_dir` | str | `/tmp` | Parent directory for per-task temporary repo clones. |
| `tasks` | list | `[]` | Task ids to run; empty runs every task in the dataset. |
| `concurrency` | int | `1` | How many tasks to run at once. `1` runs serially; higher values overlap runs and make the `vllm_prometheus` block a server-wide aggregate (see [Running tasks concurrently](#running-tasks-concurrently)). |
| `permission_mode` | str | `acceptEdits` | `claude -p` permission mode. `bypassPermissions` is intentionally rejected. |
| `allowed_tools` | list | read + write set | Tools `claude -p` may use without prompting. |
| `max_turns` | int | `60` | Cap on the agent loop (`claude --max-turns`). |
| `max_output_tokens` | int | `16000` | Per-response output-token cap. |
| `timeout_seconds` | int | `1800` | Wall-clock timeout for a single task's run. |
| `settings_file` | str | none | Optional `claude --settings` JSON (e.g. the vLLM Claude Code config). |

Validate a config the same way as a dataset. Since the template leaves `model` and `dataset` unset, pass them (the validator takes the same `--model`/`--dataset` overrides as the harness):

```bash
cd benchmarks
uv run scripts/runner_config.py config/runner.example.yaml \
    --model qwen3-coder-30b --dataset dataset/mcp-gateway-registry.yaml
```

## Running the benchmark

[scripts/run-swe-headless.py](../scripts/run-swe-headless.py) is the harness. For each selected task it:

1. Clones the task's repo at its pinned ref into a temporary directory under `clone_dir`.
2. Invokes `claude -p "/swe repo: ... problem: ... model: ... answers: ..."` non-interactively, letting the `/swe` skill produce the four artifacts under `swe-benchmark-data/{model-name}/{repo-name}/{task-id}/`.
3. Parses the run's JSON result (`--output-format json`) for the benchmark metrics -- token usage, latency, and `num_turns` -- and writes them to `metrics.json` beside the artifacts. The top-level metrics report only what the model API returned; vLLM's full Prometheus `/metrics` surface (scraped before and after the run) is kept in a separate nested block (see [The metrics file](#the-metrics-file)).
4. Removes the temporary clone.

It runs `claude -p` with `--permission-mode acceptEdits` and a narrow `--allowedTools` allowlist; it never uses `bypassPermissions` or `--dangerously-skip-permissions`.

### How `--settings` pins routing

However `provider` is set, the harness always passes `claude --settings`, and this matters: a Claude Code settings object's `env` block takes precedence over process environment variables, including any in your global `~/.claude/settings.json`. Passing `--settings` is what reliably wins over that global file and pins routing to whatever the config asked for. Exactly what the harness puts in that settings object differs per path and is documented in each path guide.

When `claude -p` returns an error, the harness records the error message and `api_error_status` in `metrics.json` and logs them, so a failed run is diagnosable without re-running it by hand.

### Common invocations

```bash
cd benchmarks

# Run every task in the dataset named by the config
uv run scripts/run-swe-headless.py --config config/runner.yaml

# Kick the tires: run the trivial hello-world sanity dataset first
uv run scripts/run-swe-headless.py --config config/runner.yaml \
    --dataset dataset/hello-world.yaml

# Override the model and run a subset of tasks (CLI wins over the config)
uv run scripts/run-swe-headless.py --config config/runner.yaml \
    --dataset dataset/hello-world.yaml --model qwen3-coder-30b \
    --tasks add-contributing-guide

# Smoke-test a large dataset by running only its first task
uv run scripts/run-swe-headless.py --config config/runner.yaml \
    --dataset dataset/mcp-gateway-registry.yaml --count 1

# Run three tasks at a time (per-run API metrics stay correct; the
# vllm_prometheus block becomes a server-wide aggregate -- see below)
uv run scripts/run-swe-headless.py --config config/runner.yaml \
    --dataset dataset/mcp-gateway-registry.yaml --concurrency 3

# Print the prompt and command for each task without running anything
uv run scripts/run-swe-headless.py --config config/runner.yaml --dry-run

# Watch a live trace of what the agent is doing while it runs
uv run scripts/run-swe-headless.py --config config/runner.yaml \
    --dataset dataset/mcp-gateway-registry.yaml --count 1 --stream

# Same, but do not truncate assistant text or tool output in the trace
uv run scripts/run-swe-headless.py --config config/runner.yaml \
    --dataset dataset/mcp-gateway-registry.yaml --count 1 --stream --verbose
```

`--count N` keeps only the first `N` tasks in dataset order (after any `--tasks` filter); `--count 0`, the default, runs them all.

By default the harness runs `claude -p` with `--output-format json`, which buffers the whole run and prints nothing until the task finishes -- so a long task looks like it is hanging when it is really just working. Pass `--stream` to run with `--output-format stream-json` instead: the harness reads the agent's events as they arrive and logs a short trace line per event (assistant text, each tool call, and so on). Add `--verbose` to print assistant text and tool results in full instead of truncating them in that trace (useful for debugging a model that stalls or emits malformed tool calls); it has no effect without `--stream`. The captured metrics and `metrics.json` are identical regardless of these flags; they only change what you see during the run.

[scripts/run-swe-benchmark.sh](../scripts/run-swe-benchmark.sh) is a thin convenience wrapper that forwards its arguments to the harness, injecting `--config config/runner.yaml` when you do not pass your own `--config`:

```bash
cd benchmarks
./scripts/run-swe-benchmark.sh --dataset dataset/hello-world.yaml --dry-run
```

### Running tasks concurrently

The harness runs tasks serially by default (`concurrency: 1`). Set `concurrency` in the config (or `--concurrency N` on the CLI) to run several tasks at once through a thread pool of that width. Each task clones into its own temporary directory, writes to a distinct artifact directory, and runs `claude -p` as an independent subprocess, so the work parallelizes cleanly and wall-clock time drops roughly linearly with the pool width (bounded by the endpoint's own throughput). `--stream` is disabled under concurrency because interleaved event traces from several tasks are unreadable.

There is one important consequence for the metrics. The per-run fields sourced from the model API -- `input_tokens`, `output_tokens`, `latency_seconds`, `num_turns`, `generation_tokens_per_sec`, and everything in `metrics_that_matter` that comes from `claude_api` -- stay **exactly correct** regardless of concurrency, because Claude Code attributes them to the individual request. What changes is the `vllm_prometheus` block. Those numbers are window deltas of **server-wide** counters, so when runs overlap the window is shared: ratios like `prefix_cache_hit_rate` become the real *aggregate* hit rate across all the concurrent runs (a genuine GPU-level KPI, just not isolated to one task), and absolute counts like the token deltas are *summed* across the overlapping runs, so each of the N files carries a near-identical, inflated figure. To make this unmissable, under concurrency > 1 the harness sets `"single_tenant": false` on the block and prepends an `AGGREGATE (concurrency > 1): ...` warning to its `note`. The sampled `gauges_sampled.kv_cache_usage_perc` peak, by contrast, becomes *more* meaningful under load -- concurrency is exactly when the KV cache is actually stressed.

The rule of thumb: use concurrency to get through a large dataset faster and to compare models on the per-run API metrics; drop back to `concurrency: 1` whenever you need trustworthy per-run vLLM cache numbers.

## The metrics file

Alongside the four artifacts, each run writes a `metrics.json` capturing what the run cost and whether it produced everything expected. Here is a real example from the hello-world sanity run:

```json
{
  "task": "add-contributing-guide",
  "repo": "https://github.com/octocat/Hello-World",
  "ref": "master",
  "complexity": "low",
  "tags": [
    "sanity-check",
    "docs",
    "hello-world"
  ],
  "model": "qwen3.6-35b",
  "provider": "endpoint",
  "endpoint": "http://127.0.0.1:8000",
  "aws_region": null,
  "artifacts_produced": 4,
  "artifacts_expected": 4,
  "generation_tokens_per_sec": 124.0,
  "metrics_that_matter": {
    "note": "Headline metrics resolved to the best available source for each; see 'sources'. Values drawn from vllm_prometheus carry its single-tenant, server-wide caveat.",
    "input_tokens": 364184,
    "output_tokens": 9388,
    "cache_read_tokens": 251287,
    "cache_write_tokens": 112897,
    "latency_seconds": 75.7,
    "num_turns": 17,
    "generation_tokens_per_sec": 124.0,
    "prefix_cache_hit_rate": 0.5302,
    "sources": {
      "input_tokens": "claude_api.usage.input_tokens",
      "cache_read_tokens": "vllm_prometheus.counters.vllm:prompt_tokens_cached_total",
      "prefix_cache_hit_rate": "vllm_prometheus.derived.prefix_cache_hit_rate"
    }
  },
  "input_tokens": 364184,
  "output_tokens": 9388,
  "latency_seconds": 75.7,
  "num_turns": 17,
  "total_cost_usd": 2.058595,
  "is_error": false,
  "session_id": "78df4f2a-6598-4add-a253-287354c207bd",
  "vllm_prometheus": {
    "available": true,
    "source": "vllm_prometheus_window",
    "note": "Window delta of server-wide vLLM metrics; accurate only if this run was the sole traffic on the endpoint during its execution. Gauges are an instantaneous post-run reading and typically read idle between tasks.",
    "derived": {
      "prefix_cache_hit_rate": 0.5302,
      "prompt_tokens_cached_rate": 0.69
    },
    "counters": {
      "vllm:generation_tokens_total": 9388,
      "vllm:prefix_cache_queries_total": 372184,
      "vllm:prefix_cache_hits_total": 197340,
      "vllm:prompt_tokens_total": 364184,
      "vllm:prompt_tokens_cached_total": 251287,
      "vllm:num_preemptions_total": 0,
      "vllm:request_success_total": 17
    },
    "histograms": {
      "vllm:e2e_request_latency_seconds": { "count": 17, "sum": 74.8, "mean": 4.4 },
      "vllm:time_to_first_token_seconds": { "count": 17, "sum": 1.9, "mean": 0.114 }
    },
    "gauges": {
      "vllm:kv_cache_usage_perc": 0.0,
      "vllm:num_requests_running": 0
    },
    "gauges_sampled": {
      "available": true,
      "source": "vllm_prometheus_poll",
      "note": "Peak/mean of gauges sampled every 1.0s while the run was in flight. Peak still reflects total server load under the single-tenant assumption, not this run in isolation.",
      "interval_seconds": 1.0,
      "gauges": {
        "vllm:kv_cache_usage_perc": { "peak": 0.047, "mean": 0.031, "samples": 74 },
        "vllm:num_requests_running": { "peak": 1.0, "mean": 0.9, "samples": 74 },
        "vllm:num_requests_waiting": { "peak": 0.0, "mean": 0.0, "samples": 74 }
      }
    }
  }
}
```

(The `counters`, `histograms`, `gauges`, and the `metrics_that_matter.sources` map above are abbreviated -- the harness records **every** `vllm:` family and a source for every headline metric, not just these.)

**Start with `metrics_that_matter`.** This is a curated headline block that answers "how did this run perform?" without the reader having to know which source owns each number. For every metric it picks the best available source and records that choice in a parallel `sources` map: token counts and turns come from the model API when it reports them, cache-token counts fall back to vLLM's server-side counters when the API is silent (as vLLM's Anthropic route is), and `prefix_cache_hit_rate` is the rate the harness derives from vLLM's counters. `generation_tokens_per_sec` is `output_tokens / latency_seconds`. KV-cache utilization is **intentionally excluded** from this block: on a serial, single-tenant benchmark it barely varies (it tracks one request's working set as a fraction of the pool, not anything the benchmark controls), so it cannot discriminate between runs; the sampled peak/mean still lives in `vllm_prometheus.gauges_sampled` as capacity telemetry, and it becomes a headline concern only under concurrent load. Every value sourced from `vllm_prometheus` inherits that block's single-tenant caveat (below). Under `provider: bedrock` there is no vLLM server, so the vLLM fallbacks and `prefix_cache_hit_rate` are dropped and this block reports only what the model API returned (cache-token counts included, since Bedrock reports them per request).

The remaining top-level fields report **only what the model API returned** for the run. That is deliberate: `cache_read_tokens` and `cache_creation_tokens` are omitted entirely rather than reported as `0`, because vLLM's Anthropic-compatible `/v1/messages` usage does not emit per-request cache-token fields. A `0` there would read as "no caching happened," which is misleading -- prefix caching is in fact active on the server. Reporting only the fields the API actually returns keeps the top-level record honest.

Everything vLLM exposes is instead reported in the nested `vllm_prometheus` block, scraped from the server's Prometheus `/metrics` endpoint. This block **deliberately duplicates** some top-level numbers (e.g. token counts) -- because it is namespaced under `vllm_prometheus` and every key keeps its full `vllm:` metric name, there is never any ambiguity about where a number came from: the top level is the model API's per-request accounting, this block is vLLM's server-side view. Rather than curate a subset, the harness scrapes the entire `vllm:` surface, so nothing is omitted and new vLLM metrics appear automatically. When an endpoint exposes no `vllm:` metrics (e.g. a non-vLLM backend behind `provider: endpoint`, such as the LiteLLM proxy), `available` is `false` and the groups are empty. Under `provider: bedrock` there is no server to scrape at all, so the whole `vllm_prometheus` block is **omitted** from the record (and `prefix_cache_hit_rate` drops out of `metrics_that_matter`) rather than written as an empty stub.

The block is organized by Prometheus metric type, plus a small derived summary:

| Group | Reported value | Notes |
| --- | --- | --- |
| `derived` | `prefix_cache_hit_rate`, `prompt_tokens_cached_rate` | The headline cache rates the harness computes, since vLLM does not publish them. In vLLM v1 a prefix-cache hit **is** a KV-cache hit -- there is no separate KV-hit counter. |
| `counters` | **window delta** of each counter | The change over the run: generation/prompt tokens, prefix-cache queries/hits, preemptions, request successes, and so on. Each keyed by its full `vllm:` name. |
| `histograms` | per-family `count`, `sum`, and derived `mean` | All window deltas, so `mean` is the mean over *this run's* requests (e.g. mean TTFT, mean end-to-end latency). Raw `_bucket` lines are omitted. |
| `gauges` | an **instantaneous** post-run reading | Gauges are point-in-time (e.g. `kv_cache_usage_perc`, `num_requests_running`); between serial tasks they typically read idle (`0`), because the KV cache drains the moment a request finishes. See `gauges_sampled` for the in-flight peak. |
| `gauges_sampled` | `peak`, `mean`, and `samples` per gauge | The harness polls the gauge endpoint in a background thread every `interval_seconds` **while the run is in flight**, so `peak` reflects what actually happened during the run -- matching what the vLLM server log prints -- rather than the idle post-run reading. `available` is `false` if no gauge was sampled (endpoint unreachable). |

A metric present in neither snapshot is reported as `null` (distinct from `0`, which means it happened zero times during the run). Prometheus `_created` timestamp series are dropped as noise.

> **Single-tenant assumption -- read this before trusting these numbers.** The vLLM Prometheus metrics are **server-wide and cumulative**; they carry no per-request or per-session label. The `vllm_prometheus` block is a window delta of those metrics, so every counter and histogram equals *this run's* activity **only if the run was the sole traffic on the endpoint while it executed**. If any other request hit the same vLLM server during the run -- another benchmark task, a concurrent client, a health probe -- its tokens, latencies, and hits are folded into these numbers and the block over-counts. Gauges are worse still: they reflect whatever the server is doing at the instant of the scrape, not the run. Run benchmarks against a dedicated, otherwise-idle endpoint when these numbers matter. The `note` field in every block restates this caveat inline.

Because the file records the task, model, and provider (endpoint or Amazon Bedrock) next to the token, latency, throughput, and turn-count numbers, the same task can be run against many models and the resulting `metrics.json` files compared side by side -- so you can see how each model differs in cost, speed, and how many turns it took to produce the artifacts. On a failed run the file also carries an `error` string and `api_error_status`, so a failure is diagnosable without re-running the task.

These metrics measure the *cost* of a run, not the *quality* of what it produced. The judge (next section) scores the four artifacts and adds those results to this same `metrics.json` under an `evaluation` key, so a single file holds both what a run cost and how good its output was -- the basis for ranking models on the benchmark.

## Scoring the artifacts (the judge)

The harness measures cost; the judge measures quality. It reads the four artifacts a run produced (`github-issue.md`, `lld.md`, `review.md`, `testing.md`), scores each against a fixed rubric, and writes an `eval.json` next to them (and mirrors the same object into `metrics.json` under `evaluation`). A model never judges its own work in-band: judging is a separate pass over the artifacts on disk.

There are two judge backends that share one scoring core:

- **[scripts/codex_judge.py](../scripts/codex_judge.py) -- the agentic judge (recommended).** Runs `codex exec` non-interactively with the candidate's repository checked out as a **read-only working root**, so the judge can open the real source with its own file tools and verify the factual claims in `lld.md`/`testing.md` (paths, symbols, APIs, commands) before scoring. This grounding is the point: an artifact that cites a file or function that does not exist is caught.
- **[scripts/llm_as_judge.py](../scripts/llm_as_judge.py) -- the direct judge.** Makes one stateless Amazon Bedrock (Mantle Responses API) request with the four artifacts embedded in the prompt and scores them in isolation, with no repository access. Faster and cheaper, but it can only judge internal consistency, not whether the artifacts match the real code.

Both share [scripts/judge_common.py](../scripts/judge_common.py): the rubric prompt ([scripts/judge_prompt.txt](../scripts/judge_prompt.txt)), the strict score schema, reply validation, and the atomic `eval.json` writer. So the two backends produce identically-shaped, identically-validated output and stay directly comparable.

### The rubric

Every artifact is scored on four criteria, each an integer from 0 to 25: **completeness**, **correctness**, **specificity**, and **risk_awareness**. An artifact's `total` is the sum of its four criteria (0-100). A task's `task_score` is the arithmetic mean of the four artifact totals (0-100), rounded to two places. The wrapper -- not the model -- recomputes and validates every total and the mean, and rejects a reply whose arithmetic or echoed identifiers do not match, so a malformed or inconsistent score never lands on disk.

### The eval path through the codex judge

Running [scripts/codex_judge.py](../scripts/codex_judge.py) against one artifact folder walks these steps:

1. **Render the prompt.** Load the four artifacts and render [scripts/judge_prompt.txt](../scripts/judge_prompt.txt) with them, plus the task and repository context. The folder's `metrics.json` supplies the task/candidate identifiers and the default context.
2. **Resolve and clone the repository.** Read `repo` and `ref` from the folder's `metrics.json` and clone that repository at that ref into a reusable, content-addressed checkout under the clone root (default `/tmp/swe-judge-repos`). A checkout that already resolves to the ref is reused as-is, so repeated runs do not re-clone; a partial or mismatched one is removed and re-cloned. Passing `--repo <path>` uses an existing local checkout instead. **A missing `metrics.json`, or one without a `repo`/`ref`, fails loudly before codex runs** -- repo grounding is not silently skipped.
3. **Run codex.** Invoke `codex exec --json --sandbox read-only --cd <checkout>` with the rendered prompt on stdin. Read-only is enforced by the sandbox; the judge can inspect the repository but cannot modify it.
4. **Validate.** Parse codex's final message against the shared Pydantic schema, re-check every total and the mean, and confirm the echoed task/candidate ids match the submission. Codex never writes `eval.json` itself -- the wrapper does, after validation -- so the arithmetic and identifier guarantees hold regardless of what the model emits.
5. **Write outputs.** Atomically write `eval.json` into the folder and mirror the same object into `metrics.json` under `evaluation`.

### Running the codex judge

The judge defaults to the `openai.gpt-5.6-sol` model at `high` reasoning effort, so a scoring run needs only the artifact folder:

```bash
cd benchmarks/scripts
uv run python codex_judge.py \
    --folder ../swe-benchmark-data/claude-opus-4-8/mcp-gateway-registry/remove-efs-from-terraform-aws-ecs
```

`codex exec` streams nothing to the terminal until it finishes (it buffers and prints only the final message), so a multi-minute run at `high` effort on a real repository looks idle when it is really working -- give it a few minutes.

To score many folders at once, pass `--recursive` and point `--folder` at a top-level directory. The judge walks that directory recursively, treats every subdirectory that contains a `metrics.json` as one artifact folder, and judges each in turn. A folder that fails (missing `repo`/`ref`, a codex or clone failure, invalid scores) is logged and skipped so one bad folder never aborts the batch; combine with `--no-overwrite` to resume a run and skip folders that already have an `eval.json`:

```bash
cd benchmarks/scripts
# Judge every model, task, and repo already collected under swe-benchmark-data.
uv run python codex_judge.py --recursive --no-overwrite \
    --folder ../swe-benchmark-data
```

Common overrides:

| Flag | Default | Description |
| --- | --- | --- |
| `--folder` | -- | Artifact folder to score (required). Must contain the four artifacts and a `metrics.json` with `repo`/`ref`. With `--recursive`, a top-level directory to search instead. |
| `--recursive` | (single folder) | Treat `--folder` as a top-level directory: recursively judge every subdirectory that contains a `metrics.json`. Cannot be combined with `--repo`. |
| `--repo` | (clone from `metrics.json`) | Use this local repository checkout as-is instead of cloning. |
| `--model` | `openai.gpt-5.6-sol` | Codex model id. Also settable via `JUDGE_MODEL`. |
| `--reasoning-effort` | `high` | One of `none`, `low`, `medium`, `high`, `xhigh`, `max`. Also settable via `JUDGE_REASONING_EFFORT`. |
| `--clone-root` | `/tmp/swe-judge-repos` | Parent directory for reusable judge checkouts. Also settable via `JUDGE_CLONE_ROOT`. |
| `--sandbox` | `read-only` | Codex sandbox policy. Leave `read-only` for judging. |
| `--timeout-seconds` | `900` | Wall-clock cap for the codex run. |
| `--no-overwrite` | (overwrite) | Fail instead of replacing an existing `eval.json`. |

Scoring a folder that the harness did not create (so it has no `metrics.json`) needs one file with just the two fields the clone requires -- the judge fills in the task and candidate identifiers from the folder names:

```json
{
  "repo": "https://github.com/agentic-community/mcp-gateway-registry",
  "ref": "1.24.4"
}
```

### What the judge records

The `eval.json` (and the mirrored `metrics.json.evaluation`) holds the per-artifact criteria, each artifact `total`, the `task_score`, a one-sentence `verdict`, and a `judge` metadata block. The `judge` block records the model, provider (`codex-exec`), the checkout it grounded against (`repo_root`, `repo_ref`), `reasoning_effort`, and the run's cost -- `token_usage` (input/output/cached/reasoning tokens, parsed from codex's `--json` stream) and `duration_ms` (wall-clock latency):

```json
{
  "task": "remove-efs-from-terraform-aws-ecs",
  "model": "claude-opus-4-8",
  "scores": {
    "github_issue": { "completeness": 19, "correctness": 14, "specificity": 22, "risk_awareness": 20, "total": 75, "notes": "..." },
    "lld":          { "completeness": 19, "correctness": 14, "specificity": 23, "risk_awareness": 20, "total": 76, "notes": "..." },
    "review":       { "completeness": 20, "correctness": 19, "specificity": 21, "risk_awareness": 23, "total": 83, "notes": "..." },
    "testing":      { "completeness": 17, "correctness": 9,  "specificity": 16, "risk_awareness": 18, "total": 60, "notes": "..." }
  },
  "task_score": 73.5,
  "verdict": "The artifacts are detailed and repository-aware, but ...",
  "judge": {
    "model": "openai.gpt-5.6-sol",
    "provider": "codex-exec",
    "repo_grounded": true,
    "repo_root": "/tmp/swe-judge-repos/mcp-gateway-registry-d67f0fcba86dda58",
    "repo_ref": "1.24.4",
    "reasoning_effort": "high",
    "token_usage": { "input_tokens": 1329307, "cached_input_tokens": 1221569, "output_tokens": 10962, "reasoning_output_tokens": 5375 },
    "duration_ms": 222629
  }
}
```

Because `eval.json` records the task, candidate model, and judge next to the scores, the same task scored across many candidate models can be compared side by side -- the basis for the model leaderboard.

## Development workflow

Run these from the `benchmarks/` directory before committing:

```bash
uv run ruff check scripts/ tests/
uv run mypy scripts/dataset_loader.py scripts/runner_config.py
uv run bandit -r scripts/
uv run python -m unittest discover -s tests
```

## Repository layout

```
benchmarks/
├── config/             # Runner config YAML files (runner.example.yaml)
├── dataset/            # Benchmark dataset YAML files
├── docs/               # This reference and the three per-path operational guides
├── scripts/            # Loaders, the run harness, the judges, and shell wrappers
├── tests/              # Unit tests
├── pyproject.toml      # Dependencies and tooling config
└── README.md
```
