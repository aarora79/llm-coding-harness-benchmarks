# GLM-5.2 — serving guidelines

> Per-model serving notes for the vLLM path. See the [directory README](../README.md) for the full install and configuration reference; this file only covers what is specific to **this** model.

| | |
|---|---|
| **HF repo** | `zai-org/GLM-5.2-FP8` (FP8 quantized) / `zai-org/GLM-5.2` (BF16) |
| **Model card** | [huggingface.co/zai-org/GLM-5.2](https://huggingface.co/zai-org/GLM-5.2) |
| **Type** | MoE — 744B total, **40B active per token** (IndexShare sparse attention) |
| **FP8 weights** | ~750 GB |
| **BF16 weights** | ~1,500 GB (does NOT fit 8×H200) |
| **Minimum hardware** | 8×H200 141GB (p5en.48xlarge) or 8×H100 80GB (p5.48xlarge, FP8 only, tight) |
| **Fits 4×L40S (184 GB)?** | ❌ |
| **Tool-call parser** | `glm47` |
| **Reasoning parser** | `glm47` |
| **Native context** | **1,000,000 (1M)** — IndexShare reduces per-token FLOPs by 2.9× at 1M |
| **Role** | Frontier open-source coding model — 81.0 on Terminal-Bench 2.1, 62.1 on SWE-bench Pro |

## Serve it

GLM-5.2-FP8 on 8×H200 (p5en.48xlarge). The model requires `--trust-remote-code` and benefits from setting `CUDA_HOME` explicitly for DeepGemm kernel JIT compilation.

```bash
MODEL="zai-org/GLM-5.2-FP8" \
SERVED_NAME="glm-5.2" \
TP=8 \
PORT=8000 \
MAX_MODEL_LEN=300000 \
GPU_MEM_UTIL=0.95 \
TOOL_PARSER="glm47" \
REASONING_PARSER="glm47" \
EXTRA_ARGS="--trust-remote-code" \
  ./vllm-serve.sh
```

Or the raw vLLM command (what actually runs on the instance):

```bash
cd self-hosted/vllm
mkdir -p logs
export CUDA_HOME=/usr/local/cuda
export PATH=/usr/local/cuda/bin:$PATH
export LD_LIBRARY_PATH=/usr/local/cuda/lib64:${LD_LIBRARY_PATH:-}

vllm serve zai-org/GLM-5.2-FP8 \
  --tensor-parallel-size 8 \
  --host 127.0.0.1 \
  --port 8000 \
  --served-model-name glm-5.2 claude-sonnet-4-20250514 us.anthropic.claude-opus-4-6-v1 \
  --max-model-len 300000 \
  --gpu-memory-utilization 0.95 \
  --enable-auto-tool-choice --tool-call-parser glm47 \
  --reasoning-parser glm47 \
  --enable-prefix-caching \
  --trust-remote-code \
  2>&1 | tee logs/vllm-serve.log
```

## Instance and access

| | |
|---|---|
| **Instance type** | p5en.48xlarge (8×H200 141GB, 1.13 TB VRAM) |
| **Region** | us-east-2 |
| **Cost** | ~$85/hr on-demand, ~$55/hr via capacity block |
| **SSH** | `ssh -i ~/.ssh/qwen36-key.pem ubuntu@<IP>` |
| **Tunnel** | `ssh -i ~/.ssh/qwen36-key.pem -L 8000:127.0.0.1:8000 ubuntu@<IP>` |

## Context window reality

The model natively supports 1M tokens, but KV cache VRAM limits the practical maximum:

| `max-model-len` | Fits? | Notes |
|----------------|-------|-------|
| 300,000 | ✅ | Current config — 300K at 0.95 mem util |
| 307,840 | ✅ | Absolute max per vLLM's estimate |
| 1,000,000 | ❌ | Needs ~86 GiB KV cache per GPU, only 26.5 GiB free |

To get full 1M context, you'd need either FP8 KV cache quantization (`--kv-cache-dtype fp8`) or more GPUs (16×H200 across 2 nodes).

## Thinking / reasoning effort

GLM-5.2 supports controlling thinking via `reasoning_effort` (two levels):
- **`max`** (default) — full deep thinking, best quality
- **`high`** — reduced thinking budget, lower latency
- **`enable_thinking=false`** — disable entirely (in chat template kwargs)

The `--reasoning-parser glm47` flag separates thinking into a `"type": "thinking"` content block so it doesn't leak into visible output.

## Tool calling

Uses the `glm47` parser. Tool calls are returned as structured `tool_use` blocks via the Anthropic messages API (`/v1/messages`). Confirmed working with Claude Code via the `apiKeyHelper` auth method.

## Tuning notes

- **DeepGemm JIT:** GLM-5.2 uses DeepGemm kernels that require `nvcc` at runtime. Ensure `CUDA_HOME` points to a valid CUDA installation with `bin/nvcc`. On the Ubuntu 24.04 DLAMI, this is `/usr/local/cuda`.
- **libcudart:** FlashInfer JIT also needs `libcudart.so` in the linker path. If you hit `cannot find -lcudart`, run: `sudo ln -sf /usr/local/cuda/lib64/libcudart.so /usr/lib/x86_64-linux-gnu/libcudart.so`
- **Download speed:** The FP8 model is ~750 GB (282 safetensor files). Without `HF_TOKEN`, downloads are rate-limited. Set a token for faster downloads.
- **Startup time:** First boot takes ~15–20 minutes (download + weight loading + torch.compile + CUDA graph capture). Subsequent boots (weights cached) take ~5–8 minutes.
- **Prefix caching:** Enabled by default. Very effective for `/swe` and `/implement` benchmarks where the system prompt + skill instructions are constant across turns.

## Comparison with other frontier models

| Model | Params (active) | FP8 size | Terminal-Bench 2.1 | SWE-bench Pro |
|-------|----------------|----------|-------------------|---------------|
| GLM-5.2 | 744B (40B) | 750 GB | 81.0 | 62.1 |
| Claude Opus 4.8 | Closed | — | 85.0 | — |
| Kimi K2 | 1,026B (?) | ~1 TB | — | — |
| DeepSeek V3 | 685B (37B) | ~685 GB | — | — |
