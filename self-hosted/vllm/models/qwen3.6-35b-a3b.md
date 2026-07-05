# Qwen3.6-35B-A3B — serving guidelines

> Per-model serving notes for the vLLM path. See the [directory README](../README.md) for the full install and configuration reference; this file only covers what is specific to **this** model.

| | |
|---|---|
| **HF repo** | `Qwen/Qwen3.6-35B-A3B` |
| **Model card** | [huggingface.co/Qwen/Qwen3.6-35B-A3B](https://huggingface.co/Qwen/Qwen3.6-35B-A3B) |
| **Type** | MoE — 35.9B total, **3B active per token** |
| **BF16 weights** | ~72 GB |
| **Fits 4×L40S (184 GB)?** | ✅ |
| **Tool-call parser** | `qwen3_coder` (verify against the model card at serve time) |
| **Native context** | **262144 (256K)** — extensible to ~1M with YaRN |
| **Role** | 3.6-generation MoE — newer than the 30B coder default, same 3B-active economics |

## Serve it

This model is **256K-native**, so we default it to a **200K (200000) window** — below native (no rope scaling needed) yet a very long context. Set `MAX_MODEL_LEN` explicitly since it is far above the script's own 32768 default:

```bash
cd self-hosted/vllm/scripts
MODEL=Qwen/Qwen3.6-35B-A3B SERVED_NAME=qwen3.6-35b MAX_MODEL_LEN=200000 ./vllm-serve.sh
```

Fully spelled out (recommended for a reproducible benchmark run):

```bash
MODEL="Qwen/Qwen3.6-35B-A3B" \
SERVED_NAME="qwen3.6-35b" \
TP=4 \
PORT=8000 \
MAX_MODEL_LEN=200000 \
GPU_MEM_UTIL=0.90 \
TOOL_PARSER="qwen3_coder" \
  ./vllm-serve.sh
```

> **Why 200000 and not the full 256K?** `MAX_MODEL_LEN` is a hard ceiling you set — it does not auto-expand to native. 200K stays just under the 256K native window (so no rope scaling) while leaving a little KV-cache headroom; the full 256K would consume so much KV cache it may not boot at useful concurrency. If you hit OOM or see `Maximum concurrency ... 1x` at boot, lower it (e.g. `131072` or `65536`). See the long-context tuning note below.

## How it compares

Same **3B-active MoE economics** as the 30B coder default — per-token compute tracks the 3B active count, not the 35.9B total — so throughput and cost per token stay in the favorable regime the [strategy cost model](../../../README.md) depends on. The extra ~11 GB of weights over the 30B (~72 GB vs ~61 GB) leaves marginally less room for the KV cache, so expect slightly lower max concurrency at the same context length. Use this file's model when you want the newer 3.6-generation quality without giving up the MoE throughput advantage.

## Tuning notes

- **Tool-call parser:** `qwen3_coder` is the expected parser for this MoE family, but **confirm against the HF model card** when you first serve it — if agentic tool calls fail to parse, try `hermes`. Run `vllm serve --help` for the full parser list.
- **Long context — this model is native 256K, so most of the time you need NO rope scaling.** Per the [HF model card](https://huggingface.co/Qwen/Qwen3.6-35B-A3B), the native window is **262144 tokens (256K)**, extensible to ~1,010,000 tokens with YaRN. That is very different from the 32K-native Qwen3 models in this folder:
  - **Up to 256K — just raise `MAX_MODEL_LEN`, no `ROPE_SCALING`.** YaRN is static and can hurt short-prompt quality, so only enable it when you genuinely exceed native. On 4×L40S the real limit here is KV-cache VRAM, not the model — 256K context will not fit at meaningful concurrency, so pick the largest window your VRAM allows and stop there. To test a longer window (e.g. 128K, comfortably below native):
    ```bash
    MODEL=Qwen/Qwen3.6-35B-A3B SERVED_NAME=qwen3.6-35b \
      MAX_MODEL_LEN=131072 ./vllm-serve.sh
    ```
    The exact `vllm serve` command this builds — export the two DLAMI fixes first (the wrapper sets these for you) or the engine may fail to boot on this node:
    ```bash
    export VLLM_USE_FLASHINFER_SAMPLER=0
    export CUDA_HOME=/opt/pytorch/cuda

    ~/vllm-env/bin/vllm serve Qwen/Qwen3.6-35B-A3B \
      --tensor-parallel-size 4 \
      --host 127.0.0.1 \
      --port 8000 \
      --served-model-name qwen3.6-35b \
      --max-model-len 131072 \
      --gpu-memory-utilization 0.90 \
      --enable-auto-tool-choice --tool-call-parser qwen3_coder \
      --enable-prefix-caching
    ```
  - **Past 256K (up to ~1M) — enable YaRN.** The model card's base config is `factor 4.0` on `original_max_position_embeddings 262144` (→ ~1,010,000 tokens); use a smaller factor for a smaller target (e.g. `2.0` for ~512K). This is academic on this node — 4×L40S has nowhere near the VRAM to hold a >256K KV cache. **Do not use the bare-number `ROPE_SCALING=4` shorthand here** — it would infer the wrong native window (`MAX_MODEL_LEN ÷ 4`). Pass the full JSON so `original_max_position_embeddings` is the real 262144, alongside the target length:
    ```bash
    MODEL=Qwen/Qwen3.6-35B-A3B SERVED_NAME=qwen3.6-35b MAX_MODEL_LEN=1010000 \
      ROPE_SCALING='{"rope_type":"yarn","factor":4.0,"original_max_position_embeddings":262144}' \
      ./vllm-serve.sh
    ```
    That passes the raw flag `--rope-scaling '{"rope_type":"yarn","factor":4.0,"original_max_position_embeddings":262144}'`. Keep the JSON single-quoted so the shell doesn't split it.
  - **KV-cache reality:** context length is 4× the KV cache per request when you 4× the window, so concurrency drops proportionally. Because this is a 3B-active MoE (~72 GB weights), it still leaves more KV-cache room than the dense [Qwen3-32B](qwen3-32b.md) or the tight-fit [Qwen3-Coder-Next](qwen3-coder-next.md) — the better long-context choice of the three — but VRAM, not the model's native window, is your ceiling here. If vLLM reports `Maximum concurrency ... 1x` or can't fit even one request's KV cache at boot, lower `MAX_MODEL_LEN`. See [PagedAttention + KV cache](../README.md#pagedattention--kv-cache) and [Long context past 32K](../README.md#long-context-and-rope_scaling-yarn).

  > **Heads-up on the `ROPE_SCALING` bare-number shorthand.** The shorthand assumes native = `MAX_MODEL_LEN ÷ factor` and hardcodes `original_max_position_embeddings` from that — which is wrong for this model (native is 262144, not the 32768 the shorthand infers). For any YaRN config here, pass the **full JSON** so `original_max_position_embeddings` is the real 262144:
  > ```bash
  > ROPE_SCALING='{"rope_type":"yarn","factor":4.0,"original_max_position_embeddings":262144}'
  > ```
- **Concurrency:** between the 30B coder and the dense 32B — more headroom than the dense model (3B active), slightly less than the 30B (larger weights).

## Naming note

Distinct from [Qwen3-32B](qwen3-32b.md), which is a **dense** 32.8B model from the earlier generation. This is a 35B **MoE** from the 3.6 generation — different architecture and different economics.
