# Judge Results

Scores are 0-100 task averages from the four artifact totals in each `judge.json`.

## Final Matrix

| Task | Opus 4.8 | Kimi K2¹ | Kimi-K2.7-Code | GLM-5.2 | Qwen Coder Next | Qwen 3.6 35B | Devstral 123B | MiniMax M2.5 |
|------|---:|---:|---:|---:|---:|---:|---:|---:|
| remove-efs-from-terraform-aws-ecs | **90.25** | 86.75 | 76.75 | 80.75 | 68.75 | 70.75 | 77.50 | 64.25 |
| ssrf-hardening-outbound-url-validation | **88.00** | 80.50 | 78.25 | 82.25 | 78.50 | 82.50 | 63.25 | 70.00 |
| migrate-ecs-env-vars-to-secrets-manager | **89.25** | 87.00 | 75.25 | 60.25 | 82.25 | 78.75 | 68.00 | 70.25 |
| replace-keycloak-db-password-with-rds-iam | **85.75** | 80.25 | 64.25 | 68.25 | 76.50 | 66.25 | 62.25 | 65.00 |
| remove-faiss | **89.00** | 90.50 | 73.50 | 76.50 | 78.75 | 75.00 | 68.50 | 67.50 |

¹ Kimi K2 combined: K2-Thinking for remove-efs, ssrf, remove-faiss; K2.5 for migrate-ecs, replace-keycloak (K2-Thinking backend hung mid-benchmark, K2.5 substituted).

## Leaderboard

| Rank | Model | Provider | Avg Score | Tasks |
|---:|---|---|---:|---:|
| 1 | Claude Opus 4.8 | Anthropic (Bedrock) | **88.45** | 5 |
| 2 | Kimi K2 (combined)¹ | Moonshot AI (Bedrock proxy) | **84.60** | 5 |
| 3 | Qwen3 Coder Next | Qwen (Bedrock proxy) | 76.95 | 5 |
| 4 | Qwen 3.6 35B | Qwen (self-hosted, 4×L40S) | 74.65 | 5 |
| 5 | GLM-5.2 (FP8) | Zhipu AI (self-hosted, 8×H200) | 73.60 | 5 |
| 6 | Kimi-K2.7-Code | Moonshot AI (self-hosted, 8×H200) | 73.60 | 5 |
| 7 | Mistral Devstral 2 123B | Mistral (Bedrock proxy) | 67.90 | 5 |
| 8 | MiniMax M2.5 | MiniMax (Bedrock proxy) | 67.40 | 5 |

## Synthesis

**Opus 4.8 wins every row except one** (Kimi K2-Thinking edges it on remove-faiss with 90.50 vs 89.00). The gap to second place is 3.85 points — meaningful but not insurmountable.

**Kimi K2 (Bedrock) vs Kimi-K2.7-Code (self-hosted):** The Bedrock-hosted K2 scored 84.60 vs the self-hosted K2.7-Code at 73.60 — an 11-point gap on the same model family. This likely reflects infrastructure issues during the self-hosted run (context overflow, tool-call parser problems, subagent routing failures) rather than raw model capability.

**Self-hosted frontier models (GLM-5.2, Kimi-K2.7-Code) landed mid-pack**, not at the frontier tier their parameter counts suggest. Both scored 73.60 — below even Qwen Coder Next (76.95) which ran via managed Bedrock with zero infrastructure friction. The serving complexity (context management, thinking parsers, CUDA compilation) penalized the self-hosted path.

**Difficulty tiers held:** remove-efs and remove-faiss (bounded cleanup, enumerable surfaces) were easiest. SSRF was moderate. Secrets Manager and RDS IAM were hardest — punishing models that hallucinated AWS patterns or hand-waved Keycloak internals.

**The budget tier is genuinely capable:** Qwen 3.6 35B (a 3B-active MoE on a $4.50/hr instance) scored 74.65 — within 1 point of frontier-class 744B and 1,058B models on $55-85/hr hardware. For bounded tasks, the cost/quality tradeoff strongly favors smaller self-hosted models.
