# Qwen3-Coder-Next (80B) — serving guidelines

> Per-model serving notes for the vLLM path. See the [directory README](../README.md) for the full install and configuration reference; this file only covers what is specific to **this** model.

| | |
|---|---|
| **HF repo** | `Qwen/Qwen3-Coder-Next` |
| **Model card** | [huggingface.co/Qwen/Qwen3-Coder-Next](https://huggingface.co/Qwen/Qwen3-Coder-Next) |
| **Type** | MoE — 79.6B total, **3B active per token** |
| **BF16 weights** | ~160 GB |
| **Fits 4×L40S (184 GB)?** | ✅ **tight** — leaves only ~24 GB for KV cache + CUDA graphs |
| **Tool-call parser** | `qwen3_coder` |
| **Native context** | **262144 (256K)** — but VRAM caps you *far* below this (see below) |
| **Role** | largest model that fits the node — top-tier quality, low concurrency |

## Serve it

This model needs **two** non-defaults, both because it is large *and* a hybrid Mamba model on a VRAM-tight node: reduce the context window (`MAX_MODEL_LEN`) so the KV cache fits, and cap concurrent sequences (`MAX_NUM_SEQS`) so vLLM can allocate a Mamba state-cache block per sequence:

```bash
cd self-hosted/vllm/scripts
MODEL=Qwen/Qwen3-Coder-Next SERVED_NAME=qwen3-coder-next \
  MAX_MODEL_LEN=16384 MAX_NUM_SEQS=128 ./vllm-serve.sh
```

Fully spelled out (recommended for a reproducible benchmark run):

```bash
MODEL="Qwen/Qwen3-Coder-Next" \
SERVED_NAME="qwen3-coder-next" \
TP=4 \
PORT=8000 \
MAX_MODEL_LEN=16384 \
MAX_NUM_SEQS=128 \
GPU_MEM_UTIL=0.90 \
TOOL_PARSER="qwen3_coder" \
  ./vllm-serve.sh
```

Note: `--enable-prefix-caching` is automatically added by `vllm-serve.sh`.

## The tight-fit warning — read this first

At ~160 GB of BF16 weights on 184 GB of total VRAM, only ~24 GB remains for the KV cache and CUDA graphs **after** vLLM's `--gpu-memory-utilization 0.90` reservation. That has real consequences:

- **Keep `MAX_MODEL_LEN` low** (16384 is a safe starting point). The full 32768 may not leave enough KV cache to boot, or will cap concurrency to a handful of requests.
- **Watch the boot log** for the `Available KV cache memory` / `Maximum concurrency` lines (see [PagedAttention + KV cache](../README.md#pagedattention--kv-cache)). If concurrency reports near 1×, lower `MAX_MODEL_LEN` further.
- **Do not naively raise `GPU_MEM_UTIL`** past `0.90` here — the weights already dominate, and the CUDA-graph capture (~1 GiB) plus activation memory need the remaining headroom. Pushing it risks an OOM at capture time.
- **Cap `MAX_NUM_SEQS` — this model is a hybrid Mamba architecture.** Qwen3-Coder-Next has Mamba / linear-attention (GDN) layers, and vLLM allocates **one Mamba state-cache block per in-flight sequence**. On this VRAM-tight node there are only ~134 such blocks, but vLLM's default `max_num_seqs` is 256, so boot aborts at CUDA-graph capture with:
  > `ValueError: max_num_seqs (256) exceeds available Mamba cache blocks (134). Each decode sequence requires one Mamba cache block, so CUDA graph capture cannot proceed.`

  Set `MAX_NUM_SEQS` at or below the block count the error prints (the serve commands above use `128`). This is a *different* limit from `MAX_MODEL_LEN` — the KV cache and the Mamba cache are separate pools, and on this model both bind. The block count scales with `GPU_MEM_UTIL` and `MAX_MODEL_LEN`, so if you change either, re-read the number in the error and adjust `MAX_NUM_SEQS` to match.

Despite only 3B active parameters per token (fast, MoE economics), this model's value is **quality at the top end**, not concurrency. Use the 30B coder default when you need throughput.

### Disk: ~160 GB of weights won't fit on the root disk

Separate from VRAM: the **download** is ~160 GB, and the DLAMI root disk is only ~193 GB — nowhere near enough once anything else (e.g. the 30B, at ~57 GB) is also cached. A naive run fills the root disk and the download stalls with `Not enough free disk space`. `vllm-serve.sh` handles this by defaulting `HF_HOME` to the DLAMI's large local-NVMe scratch (`/opt/dlami/nvme/hf-cache`, 3.5 TB here) when it exists — the boot log prints `Weights cache: <dir> (<free>)` so you can confirm before the download starts. **Caveat:** that NVMe scratch is **ephemeral** — wiped on instance stop/terminate — so the 160 GB re-downloads after any stop. If you need the cache to survive a stop, set `HF_HOME` to a path on a persistent EBS volume with ≥200 GB free instead.

## Tuning notes

- **Tool calling:** `qwen3_coder` parser (same family as the 30B coder). Required for agentic clients.
- **Context window — native 256K, but VRAM is the binding constraint, not the model.** Per the [HF model card](https://huggingface.co/Qwen/Qwen3-Coder-Next) this model is **262144 (256K) native**, extensible to ~1M with YaRN — but that is irrelevant here: with only ~24 GB of VRAM left after the 160 GB of weights, the KV cache caps you *far* below native. That is why the serve command pins `MAX_MODEL_LEN=16384`, the opposite of the other MoE models where the window is a throughput choice. **You will almost never raise it** — 16384 is already near the ceiling this node's KV cache allows for this model, and `ROPE_SCALING` is pointless (you're nowhere near the 256K native window, so there is nothing to extend). **Tradeoff:** if you need a genuinely long window, use [Qwen3.6-35B-A3B](qwen3.6-35b-a3b.md) or [Qwen3-Coder-30B](qwen3-coder-30b.md) instead — both are also 256K-native but leave far more KV-cache room. See [Long context past 32K](../README.md#long-context-and-rope_scaling-yarn).
- **Concurrency:** the lowest of any model in this folder, by design — the weights leave little KV cache.
