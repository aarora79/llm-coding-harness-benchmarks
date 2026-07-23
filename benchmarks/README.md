# Benchmark harness

This directory holds the benchmark harness that drives [Claude Code](https://docs.claude.com/en/docs/claude-code) through real-world software-engineering tasks non-interactively, records what each run cost (tokens, latency, turns), and scores the artifacts it produces for quality.

For the concepts -- what the benchmark measures, the three model-hosting paths, the run flow, and the worked-example results -- start at the [top-level README](../README.md).

## Layout

```
benchmarks/
├── config/    # runner.example.yaml, and litellm-mantle.yaml for the Path 2 proxy
├── dataset/   # benchmark dataset YAML files (hello-world, mcp-gateway-registry)
├── docs/      # the shared harness reference and one setup guide per hosting path
├── scripts/   # the run harness, dataset/config loaders, the judges, the proxy launcher
├── tests/     # unit tests
└── swe-benchmark-data/  # artifacts + metrics.json + eval.json from runs (worked example)
```

## Where to go next

- **[docs/harness-reference.md](docs/harness-reference.md)** -- the shared mechanics used by every path: prerequisites, the dataset format, the dataset loader, the runner config, running the harness, the metrics file, the judge, and the development workflow.
- **Pick a hosting path** (each guide ends with a copy-pasteable run command):
  - [docs/path-anthropic-on-bedrock.md](docs/path-anthropic-on-bedrock.md) -- Path 1: Anthropic models directly on Amazon Bedrock.
  - [docs/path-open-weight-on-bedrock-litellm.md](docs/path-open-weight-on-bedrock-litellm.md) -- Path 2: open-weight models on Amazon Bedrock via a LiteLLM proxy.
  - [docs/path-self-hosted-vllm.md](docs/path-self-hosted-vllm.md) -- Path 3: self-hosted open-weight models on EC2 with vLLM.
- **[docs/end-to-end-self-hosted-run.md](docs/end-to-end-self-hosted-run.md)** -- a full run-book that ties Path 3 together end to end: pre-flight checks, serve the model, capture GPU metrics into DuckDB, run the benchmark, and score with the judge.

## One-command end-to-end run

[scripts/run-e2e-benchmark.sh](scripts/run-e2e-benchmark.sh) wraps the whole flow -- pre-flight and error checks (including clearing stale artifact folders that would stall the headless run), the benchmark harness over a dataset, and the codex judge -- behind three inputs, failing loudly at the first problem and printing the tail/status command for each step. It does not start the vLLM server or the LiteLLM proxy; bring those up first (they are long-lived services).

```bash
cd benchmarks
# provider is one of: bedrock | litellm | vllm
./scripts/run-e2e-benchmark.sh --provider vllm --model qwen3-coder-30b \
    --dataset dataset/mcp-gateway-registry.yaml --yes
./scripts/run-e2e-benchmark.sh --help
```

The **`/benchmark` skill** drives this same script interactively -- run it from Claude Code when you want to be prompted for the provider, model, and dataset and walked through each step.

## Quick start

```bash
cd benchmarks
uv sync
cp config/runner.example.yaml config/runner.yaml
# then follow one of the path guides above
```
