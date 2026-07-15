# Kimi-K2.7-Code — serving guidelines

> Per-model serving notes for the vLLM path. See the [directory README](../README.md) for the full install and configuration reference; this file only covers what is specific to **this** model.

| | |
|---|---|
| **HF repo** | `moonshotai/Kimi-K2.7-Code` |
| **Model card** | [huggingface.co/moonshotai/Kimi-K2.7-Code](https://huggingface.co/moonshotai/Kimi-K2.7-Code) |
| **Type** | MoE — 1,058.6B total, compressed-tensors (FP8/Marlin quantized) |
| **Weights on disk** | ~1 TB |
| **Minimum hardware** | 8×H200 141GB (p5en.48xlarge) |
| **Fits 4×L40S (184 GB)?** | ❌ |
| **Tool-call parser** | `kimi_k2` |
| **Reasoning parser** | `kimi_k2` |
| **Native context** | **131,072 (128K)** |
| **Role** | Frontier code-focused model from the Kimi K2 family — coding-optimized variant of K2.6 |

## Serve it

Kimi-K2.7-Code on 8×H200 (p5en.48xlarge). Requires `--trust-remote-code` and benefits from `CUDA_HOME` set for DeepGemm JIT.

```bash
MODEL="moonshotai/Kimi-K2.7-Code" \
SERVED_NAME="kimi-k2.7-code" \
TP=8 \
PORT=8000 \
MAX_MODEL_LEN=131072 \
GPU_MEM_UTIL=0.90 \
TOOL_PARSER="kimi_k2" \
REASONING_PARSER="kimi_k2" \
EXTRA_ARGS="--trust-remote-code" \
  ./vllm-serve.sh
```

Or the raw vLLM command (what actually runs on the instance):

```bash
export CUDA_HOME=/usr/local/cuda
export PATH=/usr/local/cuda/bin:$HOME/vllm-env/bin:$PATH
export LD_LIBRARY_PATH=/usr/local/cuda/lib64:${LD_LIBRARY_PATH:-}
export HF_TOKEN=<your-token>

vllm serve moonshotai/Kimi-K2.7-Code \
  --tensor-parallel-size 8 \
  --host 127.0.0.1 \
  --port 8000 \
  --served-model-name kimi-k2.7-code claude-sonnet-4-20250514 us.anthropic.claude-opus-4-6-v1 \
  --max-model-len 131072 \
  --gpu-memory-utilization 0.90 \
  --enable-auto-tool-choice --tool-call-parser kimi_k2 \
  --reasoning-parser kimi_k2 \
  --enable-prefix-caching \
  --trust-remote-code
```

## Instance and access

| | |
|---|---|
| **Instance type** | p5en.48xlarge (8×H200 141GB, 1.13 TB VRAM) |
| **Region** | us-east-2 |
| **Cost** | ~$85/hr on-demand, ~$55/hr via capacity block |
| **SSH** | `ssh -i ~/.ssh/qwen36-key.pem ubuntu@<IP>` |
| **Tunnel** | `ssh -i ~/.ssh/qwen36-key.pem -L 8000:127.0.0.1:8000 ubuntu@<IP>` |

## Disk requirements

The model weights are ~1TB on disk. Ensure at least **1.5TB free** before downloading (HF uses temp files during download requiring ~2× the final size). The instance EBS should be 2TB.

If disk fills during download:
```bash
# Delete other cached models to free space
rm -rf ~/.cache/huggingface/hub/models--zai-org--GLM-5.2-FP8
```

## Quantization

Kimi-K2.7-Code uses `compressed-tensors` format (Marlin WNA16 MoE backend). vLLM detects this automatically — no `--quantization` flag needed. The log should show:
```
Using CompressedTensorsWNA16MarlinMoEMethod
Using 'MARLIN' WNA16 MoE backend
```

## Thinking / reasoning

Uses the `kimi_k2` reasoning parser. Thinking is separated into a `"type": "thinking"` content block, keeping visible output clean. The model has strong reasoning capabilities for complex code tasks.

## Tool calling

Uses the `kimi_k2` tool parser. Tool calls are returned as structured `tool_use` blocks via the Anthropic messages API (`/v1/messages`).

## Tuning notes

- **Slow tokenizer warning:** Expected — Kimi K2 uses a custom tokenizer without a fast Rust implementation. Does not affect inference speed, only tokenization of prompts.
- **DeepGemm:** Same as GLM-5.2 — needs `CUDA_HOME` pointing at nvcc. Ensure `ninja` is installed in the venv.
- **HF_TOKEN:** Strongly recommended for faster downloads. Without it, the 1TB download is rate-limited.
- **Startup time:** First boot downloads ~1TB + weight loading + torch.compile. Allow 20-30 minutes. Subsequent boots (weights cached): ~8-10 minutes.
- **Attention backend:** Uses `FLASH_ATTN_MLA` (Multi-head Latent Attention) — same efficient attention as DeepSeek V3 family.
- **Prefix caching:** Enabled by default. Effective for repeated system prompts across benchmark runs.

## Comparison

| Model | Params (active) | Disk size | Architecture |
|-------|----------------|-----------|--------------|
| Kimi-K2.7-Code | 1,058B MoE | ~1 TB | DeepSeek V3 + MLA |
| Kimi-K2.6 | 1,058B MoE | ~1 TB | Same arch, general-purpose |
| GLM-5.2-FP8 | 744B (40B active) | ~750 GB | DeepSeek V3 + IndexShare |
| DeepSeek-V3-0324 | 685B MoE | ~685 GB FP8 | Original DeepSeek V3 |
