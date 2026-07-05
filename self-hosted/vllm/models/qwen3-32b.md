# Qwen3-32B (dense) — serving guidelines

> Per-model serving notes for the vLLM path. See the [directory README](../README.md) for the full install and configuration reference; this file only covers what is specific to **this** model.

| | |
|---|---|
| **HF repo** | `Qwen/Qwen3-32B` |
| **Model card** | [huggingface.co/Qwen/Qwen3-32B](https://huggingface.co/Qwen/Qwen3-32B) |
| **Type** | **dense** — 32.8B, **every parameter active per token** |
| **BF16 weights** | ~66 GB |
| **Fits 4×L40S (184 GB)?** | ✅ |
| **Tool-call parser** | `hermes` (**not** `qwen3_coder`) |
| **Native context** | 32768 (32K), extendable to 128K via YaRN |
| **Role** | dense-model quality baseline vs. the MoE default |

## Serve it

The two defaults you **must** override for this model are the parser (`hermes`, not the coder default) and the model/name:

```bash
cd self-hosted/vllm/scripts
MODEL=Qwen/Qwen3-32B SERVED_NAME=qwen3-32b TOOL_PARSER=hermes ./vllm-serve.sh
```

Fully spelled out (recommended for a reproducible benchmark run):

```bash
MODEL="Qwen/Qwen3-32B" \
SERVED_NAME="qwen3-32b" \
TP=4 \
PORT=8000 \
MAX_MODEL_LEN=32768 \
GPU_MEM_UTIL=0.90 \
TOOL_PARSER="hermes" \
  ./vllm-serve.sh
```

## Dense vs. the MoE default — read this first

Every one of the 32.8B parameters activates on every token. That is roughly **10× the per-token compute** of the 3B-active Qwen3-Coder-30B MoE default. Practical consequences:

- **Lower throughput** under the same concurrency, and **higher cost per token** in the strategy doc's model — this is the whole point of keeping it as a comparison baseline, not the default.
- The weights are slightly larger (~66 GB), so a bit less VRAM is left for the KV cache than with the 30B MoE — expect somewhat lower max concurrency at the same context length.

## Tuning notes

- **Tool calling:** the dense Qwen3 *chat* models use the **`hermes`** parser. Using `qwen3_coder` here will mis-parse tool calls. Setting `TOOL_PARSER=none` gives a plain completion server (no agentic clients).
- **Context window — genuinely 32K native, the exception in this folder.** Per the [HF model card](https://huggingface.co/Qwen/Qwen3-32B), Qwen3-32B is **32768 (32K) native**, validated to **131072 (128K)** with YaRN (the three MoE models here are 256K-native instead — this dense model is the one that actually needs rope scaling to go long). `MAX_MODEL_LEN` is a hard ceiling you set; the examples pin it to 32768. Two regimes:
  - **≤32768 — no `ROPE_SCALING`.** Just serve at native; raise/lower `MAX_MODEL_LEN` within the native window freely.
  - **Up to 128K — enable YaRN.** Past the 32K native window you must add rope scaling:
    ```bash
    MODEL=Qwen/Qwen3-32B SERVED_NAME=qwen3-32b TOOL_PARSER=hermes \
      MAX_MODEL_LEN=131072 ROPE_SCALING=4 ./vllm-serve.sh
    ```
    `ROPE_SCALING=4` = 4 × the 32768 native window (use `2` for 64K). **Tradeoffs:** 4× the window ≈ ¼ the concurrency (KV cache scales linearly with context), and YaRN is static so it can degrade short-prompt quality — leave it off unless you truly need >32K. 131072 is the card's validated ceiling; do not push past it. See [Long context past 32K](../README.md#long-context-and-rope_scaling-yarn).

## Naming note

This is the model referred to loosely as "the 32B." It is a **dense** model and is distinct from [Qwen3.6-35B-A3B](qwen3.6-35b-a3b.md), which is a 35B **MoE** from the 3.6 generation. If you want the 3.6-generation model, use that file instead.
