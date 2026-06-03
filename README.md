# Claude Code Multi-Model

[![License: MIT-0](https://img.shields.io/badge/License-MIT--0-yellow.svg)](LICENSE)
[![Bedrock](https://img.shields.io/badge/Amazon-Bedrock-blue)](https://docs.aws.amazon.com/bedrock/latest/userguide/models-endpoint-availability.html)
[![Models: 43+](https://img.shields.io/badge/Models-43%2B%20from%2012%20providers-orange)](./)

> **This is sample code intended for demonstration and learning purposes only.**
> It is not meant for production use. Review and harden all scripts, configurations,
> and IAM permissions before using in any production or sensitive environment.

## Overview

[Claude Code](https://docs.anthropic.com/en/docs/claude-code) is Anthropic's
command-line coding agent. By default it talks to Anthropic's own models. This
repository shows how to run Claude Code against **any foundation model on Amazon
Bedrock** — including non-Anthropic models such as Qwen, DeepSeek, Kimi, Mistral,
and others — so you can pick the model that best fits each task instead of being
limited to one provider.

It does this without modifying Claude Code. Claude Code speaks the Anthropic
Messages API; most Bedrock models speak the OpenAI Chat Completions API. A small
[LiteLLM](https://github.com/BerriAI/litellm) proxy sits in between and translates
the two, so Claude Code "just works" with whichever model you select. Native
Anthropic models on Bedrock are called directly, with no proxy.

Two deployment paths are provided:

| Path | Models | Cost Model | Best For |
|------|--------|------------|----------|
| [**Bedrock**](bedrock/) | 43 models from 12 providers | Pay-per-token | Model variety, zero infrastructure |
| [**Self-Hosted (EC2)**](self-hosted/) | Any Ollama/vLLM model | Fixed hourly GPU cost | Data sovereignty, air-gapped, unlimited tokens |

**What you get:**

- Run Claude Code with **43 Bedrock models** (5 native Anthropic + 38 third-party via Bedrock), or any open-source model you self-host on EC2
- A one-command **LiteLLM proxy** that handles Anthropic↔OpenAI translation, tool calling, and streaming
- An interactive **model picker** and per-model launch scripts
- A reproducible **HumanEval benchmark** to compare model quality before you route work to a cheaper model (see [below](#benchmark))

## Why this repo exists

A coding agent session is token-heavy: tool calls, file reads, edits, and reasoning
steps all consume input and output tokens. On Amazon Bedrock, frontier models cost
roughly **5–20x more per token** than the cheapest non-Anthropic models on the same
endpoint (see the cost columns in the [Benchmark](#benchmark) table). Running every
task on a frontier model is therefore the most expensive default; running every
task on the cheapest model risks worse output. The interesting question is how
much quality you actually lose by routing routine tasks to a cheaper model — and
that depends on the task and the model.

This repository exists to make that question answerable with data, not opinion:

- It lets Claude Code run against **any** of 43 Bedrock models, not just Anthropic ones, so the same agent harness can be measured across the cost range
- It includes a **HumanEval pass@1 benchmark** that re-runs all 164 tasks through the agent for each model, with per-token cost listed alongside
- It also supports a **self-hosted EC2 path** for the case where the per-token model is the wrong cost shape (very high volume, or data must stay in your VPC)

The benchmark numbers below are evidence, not advertising — single-run pass@1 on
164 small Python tasks. Use them as a starting point and run your own evaluation
on workloads that look like yours before routing real traffic.

## Architecture

```text
                         ┌─────────────────────────────┐
                         │      Claude Code CLI        │
                         │  (Anthropic Messages API)   │
                         └──────────┬──────────────────┘
                                    │
                 ┌──────────────────┼──────────────────┐
                 │                  │                   │
         ┌───────▼──────┐  ┌───────▼──────┐  ┌────────▼─────────┐
         │ Native       │  │ LiteLLM      │  │ SSH tunnel       │
         │ (no proxy)   │  │ Proxy        │  │ (no proxy)       │
         │              │  │              │  │                  │
         │ Claude Opus  │  │ 38 third-    │  │ localhost:11434  │
         │ Claude Sonnet│  │ party models │  │ → EC2:11434      │
         │ Claude Haiku │  │ (Anthropic → │  │                  │
         │              │  │  OpenAI fmt) │  │                  │
         └──────┬───────┘  └──────┬───────┘  └────────┬─────────┘
                │                 │                    │
         ┌──────▼───────┐  ┌─────▼────────┐  ┌───────▼─────────┐
         │              │  │              │  │ EC2 GPU         │
         │ Amazon       │  │ Amazon       │  │ instance with   │
         │ Bedrock      │  │ Bedrock      │  │ Ollama          │
         │              │  │              │  │ (open-source    │
         │              │  │              │  │  model)         │
         └──────────────┘  └──────────────┘  └─────────────────┘
```

## Benchmark

We measured model quality on the public [HumanEval](https://github.com/openai/human-eval)
benchmark (164 tasks), driving each task through Claude Code backed by each model
and scoring with standard `pass@1`:

| Model | pass@1 | Input $/1M | Output $/1M |
| --- | --- | --- | --- |
| Claude Sonnet 4.6 | 97.6% | $3.00 | $15.00 |
| Kimi K2.5 | 96.3% | $0.60 | $3.00 |
| DeepSeek V3.2 | 94.5% | $0.62 | $1.85 |
| Qwen Coder Next | 91.5% | $0.50 | $1.20 |
| Qwen Coder 30B | 90.9% | $0.15 | $0.62 |

Budget models reach 93–99% of the frontier model's pass rate at a fraction of
the cost. Prices are on-demand Standard-tier rates for US East from the
[Amazon Bedrock pricing page](https://aws.amazon.com/bedrock/pricing/) at the
time of writing. Full method, caveats, and reproduce steps in
[bedrock/README.md](bedrock/README.md#benchmark-humaneval).

## Prerequisites

- An **AWS account** with [Amazon Bedrock model access](https://console.aws.amazon.com/bedrock/home#/modelaccess) enabled for the models you want to use
- **AWS credentials** configured locally (`aws configure`, an IAM role, or AWS SSO)
- **[Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code)** installed
- **Python 3.9+** (for the LiteLLM proxy and Bedrock token generation)
- For the self-hosted path: permission to launch an **EC2 GPU instance** (e.g. `g6e.xlarge`)

> The OpenAI-compatible Bedrock endpoint used for third-party models is currently available in **`us-east-1`**.

## Get Started

Pick a path and follow its README — each one has full setup, configuration, and
a worked example:

- **[bedrock/README.md](bedrock/README.md)** — Bedrock path. Start the LiteLLM
  proxy and run Claude Code against any of the 43 models with `claude-model.sh`.
- **[self-hosted/README.md](self-hosted/README.md)** — Self-hosted path. Provision
  a GPU instance, install Ollama, open an SSH tunnel, and run Claude Code against
  a model in your VPC.

## Comparison

| | Bedrock | Self-Hosted (EC2) |
|---|---|---|
| **Models** | 43 from 12 providers | Any GGUF/HF model |
| **Pricing** | Per-token ($0.15-$15/M) | Per-hour ($0.84-$4.60/hr GPU) |
| **Setup time** | 5 minutes | 15-20 minutes |
| **Latency** | Varies by model (a few sec to minutes/task) | Depends on GPU + model size |
| **Data location** | AWS Bedrock service | Your VPC, your instance |
| **Best when** | Variable workload, model variety | Fixed workload, data sovereignty |
| **Break-even** | < ~2M tokens/hour | > ~2M tokens/hour |

## Repository Structure

```text
claude-code-multi-model/
├── README.md                  ← You are here
├── LICENSE                    MIT-0
├── CODE_OF_CONDUCT.md
├── CONTRIBUTING.md
├── SECURITY.md
├── SUPPORT.md
├── THIRD_PARTY                Third-party dependency attributions
├── .github/                   Issue and pull-request templates
├── bedrock/                   ← Bedrock path (38 third-party + 5 Anthropic)
│   ├── README.md              Full Bedrock setup guide + benchmark
│   ├── scripts/               setup-proxy.sh, claude-model.sh, mantle-token.sh
│   ├── config/                litellm-config.yaml, claude-proxy-settings.json
│   └── benchmark/             HumanEval runner (humaneval_runner.py) + pass@1 results
└── self-hosted/               ← EC2 self-hosted path (Ollama/vLLM)
    ├── README.md              Full EC2 setup guide
    ├── SETUP-GUIDE.md         Step-by-step GPU instance provisioning
    ├── scripts/               setup.sh, run.sh, tunnel.sh
    └── config/                litellm-config.yaml, model configs
```

## See Also

- [HumanEval](https://github.com/openai/human-eval) — the public benchmark used above
- [Claude Code docs](https://docs.anthropic.com/en/docs/claude-code) — Official Claude Code documentation

## License

This library is licensed under the MIT-0 License. See the [LICENSE](LICENSE) file.
