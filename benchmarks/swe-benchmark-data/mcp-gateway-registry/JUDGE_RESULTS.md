# SWE Skill Judge Results

GPT-judged scores for the 5-task × 6-model SWE benchmark against `mcp-gateway-registry` @ tag `1.24.4`. Each artifact bundle (`github-issue.md` + `lld.md` + `review.md` + `testing.md`) was scored 0–100 on Completeness, Correctness, Specificity, and Risk Awareness; the cell score is the average of those four artifact scores. The judge ran in a fresh ChatGPT session with the prompt in [`docs-local/JUDGE_PROMPT.md`](../../../docs-local/JUDGE_PROMPT.md).

Per-cell JSON (with criterion breakdowns + judge notes) lives next to each artifact at `{task}/{model}/judge-gpt.json`.

## Score matrix

All cells are percentages (0–100%), averaged across the 4 artifacts per (task × model). Bold = top score in row.

| Task | Opus 4.8 | Kimi¹ | Devstral 123B | MiniMax M2.5 | Qwen Coder Next | Qwen 3.6 35B | Task avg |
|------|---------:|------:|--------------:|-------------:|----------------:|-------------:|---------:|
| `remove-faiss` | **90.8%** | 87.8% ᵀ | 77.8% | 73.5% | 80.8% | 80.2% | 81.8% |
| `remove-efs-from-terraform-aws-ecs` | **90.8%** | 83.5% ᵀ | 83.8% | 76.0% | 80.2% | 75.2% | 81.6% |
| `ssrf-hardening-outbound-url-validation` | **90.0%** | 66.2% ᵀ | 70.5% | 69.2% | 85.8% | 71.2% | 75.5% |
| `migrate-ecs-env-vars-to-secrets-manager` | **90.5%** | 87.0% ⁵ | 75.0% | 78.5% | 80.8% | 63.0% | 79.1% |
| `replace-keycloak-db-password-with-rds-iam` | **87.8%** | 86.2% ⁵ | 72.8% | 76.2% | 71.5% | 58.8% | 75.5% |

¹ **Kimi variant per task:** ᵀ = `kimi-k2-thinking`, ⁵ = `kimi-k2.5`. Tasks 1–3 used K2 Thinking; mid-benchmark its Bedrock backend started hanging requests indefinitely (smoke-test curl timed out after 75s) while K2.5 responded in <1s on the same proxy, so K2.5 was substituted for tasks 4–5.

## Per-model leaderboard

| Rank | Model | Avg score | # tasks |
|-----:|-------|----------:|--------:|
| 🥇 1 | Claude Opus 4.8 | **89.95%** | 5 |
| 🥈 2 | **Kimi (combined)** | **82.15%** | 5 (3 × K2-Thinking + 2 × K2.5) |
| 🥉 3 | Qwen Coder Next | 79.80% | 5 |
| 4 | Mistral Devstral 2 123B | 75.95% | 5 |
| 5 | MiniMax M2.5 | 74.70% | 5 |
| 6 | Qwen 3.6 35B | 69.70% | 5 |

## Per-task averages (lower = harder for the field)

| Task | Avg | Why this difficulty |
|------|----:|---------------------|
| `ssrf-hardening-outbound-url-validation` | **75.5%** | Tied hardest by score — security tasks reward enumeration depth (private IPs, DNS rebinding, redirect handling) that several non-frontier models did not enumerate |
| `replace-keycloak-db-password-with-rds-iam` | **75.5%** | Tied hardest — RDS IAM auth pattern is unfamiliar to most models; several hallucinated infeasible AWS mechanics |
| `migrate-ecs-env-vars-to-secrets-manager` | 79.1% | ECS `secrets` blocks are familiar, but Terraform state and conditional-secret edge cases separated stronger designs from weaker ones |
| `remove-efs-from-terraform-aws-ecs` | 81.6% | Bounded Terraform-only scope |
| `remove-faiss` | 81.8% | Easiest after adding Qwen 3.6 — mostly bounded removal |

## Synthesis

**Opus 4.8 wins every row, but not by much.** Opus scored 87.8–90.8 on every task (average 89.95). The gap to the second-place model on each row is 3–24 points, not the 10–25× cost ratio would suggest. The "good enough for most tasks" thesis is supported by the data.

**Kimi (combined) is a clear #2, not a tossup with the others.** Across the 5 tasks (3 K2-Thinking + 2 K2.5), Kimi averaged 82.15 — well above the mid/budget cluster at 74–80. Its weakest cell was SSRF at 66.25 (K2-Thinking under-enumerated edge cases). On Tasks 4 and 5 where K2.5 ran instead, scores were 87.0 and 86.2 — closer to Opus than to the rest of the field. **Caveat:** the K2-Thinking → K2.5 substitution means Kimi's row is not a single-model evaluation, so the 82.15 should be read as "the Kimi family did this well across two variants."

**The mid/budget tier splits into two bands.** Qwen Coder Next, Devstral, and MiniMax cluster tightly at 74–80, while Qwen 3.6 35B lands lower at 69.70. Qwen Coder Next has the highest average (79.80) but does not dominate row-by-row:

| Task | Winner among mid/budget | Margin |
|------|-------------------------|-------:|
| remove-faiss | Qwen | +3 |
| remove-efs | **Devstral** | +3.5 |
| ssrf-hardening | Qwen | +15.3 |
| migrate-secrets | Qwen | +2.3 |
| keycloak-iam | **MiniMax** | +3.5 |

Qwen wins 3 of 5, but Devstral and MiniMax each take one task. Qwen's average advantage is **driven mostly by the SSRF outlier** (85.75 vs ~70 for the others on that row) — strip SSRF out and the three models are within 2 points of each other.

**Qwen has a coder-specialist sweet spot.** On SSRF (a security-coding task), Qwen scored 85.75 — second only to Opus and ahead of Kimi K2-Thinking. The judge's notes call out a "Strong SSRF matrix including private ranges, redirects, DNS, and feature flags." On the AWS-infrastructure-heavy tasks (Keycloak IAM, ECS secrets), Qwen scored lower — the judge flagged "questionable Docker/Helm scope" and "impossible or non-idiomatic ideas such as Lambda valueFrom for ECS secrets." Qwen is excellent at code, weaker at AWS-specific design.

**Qwen 3.6 35B is usable on bounded cleanup, but falls off on AWS-heavy security design.** It scored 80.25 on FAISS removal and 75.25 on EFS removal, both mostly inventory-and-delete tasks. It dropped to 63.0 on ECS Secrets Manager and 58.75 on Keycloak IAM because the artifacts stayed detailed while missing key AWS mechanics: Terraform state leakage through `secret_string`, conditional counted resources, RDS IAM auth attributes, token generation in the Keycloak image, and token lifecycle.

**The "medium" SSRF task remains one of the hardest.** After adding Qwen 3.6, SSRF ties Keycloak-IAM at 75.5. Difficulty as humans judge it (number of layers touched) is not the same as difficulty as models experience it (number of edge cases to enumerate). Worth keeping for the talk.

**20× cost spread → roughly 21-point quality spread.** At the top of the field, the budget models really are good enough for routine tasks. The cost-quality tradeoff is favorable for cheap models on bounded refactors and code-heavy work, less favorable on AWS-specific infrastructure where frontier reasoning earns its premium.

## How to reproduce

1. Run `/swe` for every `(task × model)` cell — see [`benchmarks/swe-benchmark-data/README.md`](../README.md) for commands.
2. Paste the judge prompt section of `docs-local/JUDGE_PROMPT.md` into a fresh ChatGPT (GPT-5.x) session.
3. For each cell, submit `Task: <slug>` + `Model: <slug>` + the 4 artifacts. The judge returns one JSON object per cell.
4. Save each JSON as `{task}/{model}/judge-gpt.json`; aggregate into this file.

The artifacts themselves are checked in under `mcp-gateway-registry/{task}/{model}/`, so a reader can audit the input → judge JSON → matrix chain end-to-end.
