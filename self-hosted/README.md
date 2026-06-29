# Claude Code on Amazon EC2 with a Self-Hosted Open-Source Model

[![License: MIT-0](https://img.shields.io/badge/License-MIT--0-yellow.svg)](LICENSE)
[![Ollama](https://img.shields.io/badge/Ollama-compatible-blue)](https://ollama.com)
[![Model: Qwen 3.5](https://img.shields.io/badge/Model-Qwen%203.5--35B-orange)](https://ollama.com/library/qwen3.5)

> **This is sample code intended for demonstration and learning purposes only.**
> It is not meant for production use. Review and harden all scripts, configurations,
> and IAM permissions before using in any production or sensitive environment.

Run Claude Code — Anthropic's AI coding assistant — backed by an open-source model on
Amazon EC2. Your code stays inside your AWS account, costs are predictable, and you keep
full control over the model.

```mermaid
flowchart TD
    subgraph Local["Your Machine"]
        CC["Claude Code CLI<br/>ANTHROPIC_BASE_URL=<br/>localhost:11434"]
    end

    subgraph AWS["AWS"]
        EC2["Amazon EC2 GPU Instance (g6e.xlarge)<br/>Ollama serving Qwen 3.5-35B<br/>NVIDIA L40S, 45GB VRAM"]
    end

    CC -- "SSH tunnel (encrypted, no open ports)<br/>localhost:11434 → EC2:11434" --> EC2

    classDef agent fill:#E5E7EB,stroke:#6B7280,color:#111827
    classDef ec2 fill:#FFF3E0,stroke:#FF9900,color:#1F2937
    class CC agent
    class EC2 ec2
```

Claude Code is configured with `ANTHROPIC_BASE_URL=http://localhost:11434` and
talks to the local end of an SSH tunnel; the tunnel transparently forwards every
request to the Ollama server on the EC2 instance. Ollama accepts Anthropic
Messages requests natively (no proxy or format translation needed). No API keys
on the wire, no public ingress — the only network path in is SSH.

## Why Run on EC2?

| | Cloud API (Anthropic / Amazon Bedrock) | Self-Hosted on EC2 |
| --- | --- | --- |
| Pricing | Per token — variable cost | Fixed hourly EC2 rate |
| Data residency | Sent to external API | Stays in your AWS account |
| Model choice | Provider-managed | Any open-source model |
| Network path | Public internet | Within your VPC |
| Compliance controls | Limited | Full — IAM, VPC, CloudTrail |

Heavy coding sessions with many tool calls (file reads, edits, bash commands) add up fast
on pay-per-token APIs. At ~117 tokens/sec on a g6e.xlarge, a self-hosted model is
cost-effective for sustained daily use.

## Choosing an EC2 Instance

| Instance | GPU | VRAM | On-Demand | Best For |
| --- | --- | --- | --- | --- |
| g6e.xlarge | NVIDIA L40S | 45GB | ~$1.86/hr | 35B models, recommended |
| g5.xlarge | NVIDIA A10G | 24GB | ~$1.01/hr | 7B-13B models |
| g4dn.xlarge | NVIDIA T4 | 16GB | ~$0.53/hr | 7B models, budget option |
| p3.2xlarge | NVIDIA V100 | 16GB | ~$3.06/hr | Older generation |

## Prerequisites

- AWS account with EC2 access
- AWS CLI configured (`aws configure`)
- Claude Code installed locally (`npm install -g @anthropic-ai/claude-code`)
- An EC2 key pair

## What's Inside

| File | What it does |
| --- | --- |
| [scripts/ec2-setup.sh](scripts/ec2-setup.sh) | One-shot EC2 setup: installs Ollama, pulls model, verifies GPU |
| [scripts/tunnel.sh](scripts/tunnel.sh) | Opens and closes the SSH tunnel between your machine and EC2 |
| [scripts/claude-local.sh](scripts/claude-local.sh) | Launches Claude Code pointed at the local model, restores config on exit |
| [scripts/bench.sh](scripts/bench.sh) | Benchmarks local model vs Amazon Bedrock side by side |
| [BENCHMARK.md](BENCHMARK.md) | Full benchmark guide: tasks, verification, and result interpretation |
| [config/settings.template.json](config/settings.template.json) | Claude Code configuration template |
| [SETUP-GUIDE.md](SETUP-GUIDE.md) | Advanced walkthrough using llama.cpp directly (more tuning control) |

## Quick Start

### Step 1 — Launch the EC2 instance

Use the **Deep Learning Base OSS Nvidia Driver AMI (Ubuntu 22.04)** — NVIDIA drivers and
CUDA come pre-installed.

```bash
aws ec2 run-instances \
  --region us-east-1 \
  --image-id ami-014135eb43056a305 \
  --instance-type g6e.xlarge \
  --key-name <your-key-pair> \
  --security-group-ids <your-sg> \
  --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":100,"VolumeType":"gp3"}}]' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=llm-server}]'
```

Security group: port 22 inbound from your IP only. The model port never needs to be
open — access is via SSH tunnel.

### Step 2 — Set up the model server on EC2

SSH in and run the setup script:

```bash
curl -fsSL https://raw.githubusercontent.com/shekharprateek/claude-code-on-amazon-ec2/main/scripts/ec2-setup.sh | bash
```

Or clone and run:

```bash
git clone https://github.com/shekharprateek/claude-code-on-amazon-ec2 ~/claude-code-on-amazon-ec2
bash ~/claude-code-on-amazon-ec2/scripts/ec2-setup.sh
```

This installs Ollama, pulls Qwen 3.5-35B (~22GB on first run), and verifies the model
is running on GPU. To use a smaller model: `MODEL=qwen3.5:7b bash ec2-setup.sh`

### Step 3 — Connect from your machine

```bash
export G6E_IP=<your-ec2-public-ip>
export G6E_KEY=~/.ssh/<your-key>.pem
./scripts/tunnel.sh start
```

### Step 4 — Run Claude Code

```bash
./scripts/claude-local.sh
```

Or run `/install` inside Claude Code for a guided setup experience.

See [SETUP-GUIDE.md](SETUP-GUIDE.md) for the full walkthrough including GPU monitoring,
spot instance setup, and cost optimization.

## Cost Optimization

**Stop when not in use** — model weights persist on EBS and reload in seconds:

```bash
aws ec2 stop-instances --instance-ids <instance-id>
aws ec2 start-instances --instance-ids <instance-id>
```

**Spot instances** save up to 70% for interruptible workloads:

```bash
aws ec2 request-spot-instances \
  --instance-count 1 \
  --type one-time \
  --launch-specification \
    '{"ImageId":"ami-014135eb43056a305","InstanceType":"g6e.xlarge","KeyName":"<key>","SecurityGroupIds":["<sg>"]}'
```

## Monitor GPU Usage

Live on the instance:

```bash
nvidia-smi --query-gpu=utilization.gpu,memory.used,temperature.gpu \
  --format=csv --loop=1
```

## Benchmark vs Amazon Bedrock

Run 9 coding tasks against both backends and compare quality and speed:

```bash
./scripts/bench.sh both
```

9 tasks across 3 tiers (simple → medium → complex), each with an automated pass/fail verifier.
See [BENCHMARK.md](BENCHMARK.md) for the full task descriptions, how verification works,
and how to read the results.

## Tear Down

```bash
aws ec2 terminate-instances --region us-east-1 --instance-ids <instance-id>
aws ec2 delete-security-group --region us-east-1 --group-id <sg-id>
```

## See Also

- **[Claude Code Multi-Model on Amazon Bedrock](https://github.com/shekharprateek/claude-code-multi-model-bedrock)** — Run Claude Code with 45 models from 11 providers (Qwen, DeepSeek, Mistral, Kimi, MiniMax, and more) via Amazon Bedrock. No GPU needed — fully managed.
