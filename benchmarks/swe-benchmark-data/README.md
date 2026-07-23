# SWE Benchmark Data

This directory holds the inputs and outputs of an LLM software-engineering benchmark. Each run takes a real-world problem inside a real repository and asks a specific model (driven by the `/swe` skill) to produce a GitHub issue spec, a low-level design, an expert review, and a testing plan. Multiple models can attempt the same problem so their artifacts can be compared side-by-side. **The skill stops at design and review - it does not implement the change.**

## Directory Layout

```
benchmarks/swe-benchmark-data/
├── README.md                           # This file
└── {model-name}/
    └── {repo-name}/
        ├── {problem-name}/
        │   ├── github-issue.md        # GitHub issue specification
        │   ├── lld.md                 # Low-level design
        │   ├── review.md              # Multi-persona expert review
        │   ├── testing.md             # Testing plan
        │   ├── eval.json              # Evaluation scores (from independent judge)
        │   └── metrics.json           # Token/cost/throughput metrics (if collected)
        └── {next-problem-name}/
            └── ...
```

Results are grouped by model first, so each model's full set of runs lives together; the same `{repo-name}/{problem-name}` under different `{model-name}/` folders lets models be compared on one problem.

**Generated artifacts are local, not committed.** The whole `swe-benchmark-data/` tree is gitignored; the only exception force-committed into this repo is the trivial `Hello-World/` example (under each model that has attempted it). Your own runs -- including any against `mcp-gateway-registry` -- stay on your machine, so you can benchmark freely without risking an accidental commit. Publish results yourself if you want them shared.

## How to Set Up a Benchmark Repository Locally

The target repository's source is **not** stored here; the harness (and the `/swe` skill) clone it at the documented tag into a temporary directory (e.g. under `/tmp`) at run time. To clone one by hand for local exploration:

```bash
git clone --branch <tag> --depth 1 https://github.com/<owner>/<name>.git /tmp/<repo-name>
```

Use `--depth 1` to keep the checkout small. If you later need full history, run `git fetch --unshallow` from inside the clone.

---

## Benchmark Repositories

Each section below documents one target repository. To benchmark a model on one of its tasks, clone the repo at the listed tag and run `/swe` against the task description.

### 1. mcp-gateway-registry

| Field | Value |
|-------|-------|
| Source | https://github.com/agentic-community/mcp-gateway-registry |
| Tag | `1.24.4` |
| Clone (at run time) | temporary, e.g. `/tmp/mcp-gateway-registry` |
| Artifact path | `benchmarks/swe-benchmark-data/{model-name}/mcp-gateway-registry/{problem-name}/` |

#### Setup

```bash
git clone --branch 1.24.4 --depth 1 https://github.com/agentic-community/mcp-gateway-registry.git /tmp/mcp-gateway-registry
```

#### Tasks

The tasks below are run with multiple models via the `/swe` skill. For each `{model-name}`, the resulting artifacts land at `benchmarks/swe-benchmark-data/{model-name}/mcp-gateway-registry/{problem-name}/`.

| # | Problem name (folder) | Issue | Difficulty | Description |
|---|-----------------------|-------|-----------|-------------|
| 1 | `remove-faiss` | [#1285](https://github.com/agentic-community/mcp-gateway-registry/issues/1285) / [#452](https://github.com/agentic-community/mcp-gateway-registry/issues/452) | Medium | Remove FAISS from the codebase and documentation. FAISS is obsolete in this repo. Delete all FAISS imports, dependencies, configuration, and references in docs. Replace any remaining vector-search needs with the maintained DocumentDB hybrid search alternative already used elsewhere in the repo. |
| 2 | `remove-efs-from-terraform-aws-ecs` | [#1286](https://github.com/agentic-community/mcp-gateway-registry/issues/1286) | Medium | Remove EFS from `terraform/aws-ecs/`. EFS is obsolete in this deployment. Delete the EFS file system, mount targets, security groups, and any task-definition volume mounts that reference it. Update `variables.tf`, `terraform.tfvars.example`, and module wiring. Verify `terraform validate` and `terraform plan` still succeed. |
| 3 | `ssrf-hardening-outbound-url-validation` | [#1282](https://github.com/agentic-community/mcp-gateway-registry/issues/1282) | Medium | SSRF hardening: validate outbound URLs on agent card fetch (health check + pull-card endpoints). The model must identify vulnerable endpoints that make outbound HTTP requests based on user-supplied URLs, propose URL validation (deny internal/private IPs, allowlists), and design input sanitization to prevent SSRF attacks. |
| 4 | `migrate-ecs-env-vars-to-secrets-manager` | [#1134](https://github.com/agentic-community/mcp-gateway-registry/issues/1134) | High | Migrate sensitive ECS environment variables to AWS Secrets Manager. Identify which env vars in the ECS task definitions contain secrets (DB passwords, API keys, OAuth client secrets, admin passwords), create Secrets Manager resources in Terraform, update ECS task definitions to pull from Secrets Manager via the `secrets` block instead of passing plaintext via `environment`, and update the IAM task execution role to allow reading those secrets. |
| 5 | `replace-keycloak-db-password-with-rds-iam` | [#1303](https://github.com/agentic-community/mcp-gateway-registry/issues/1303) | High | Replace the Keycloak database password with RDS IAM authentication. The repo uses an Aurora MySQL cluster for Keycloak; remove static DB credentials from Terraform and ECS config, enable IAM database authentication on the Aurora MySQL cluster, update the Keycloak ECS task to generate short-lived IAM auth tokens via `rds:GenerateDBAuthToken`, and update IAM roles/policies accordingly. |

#### How to Run a Task with `/swe`

```
/swe

# When prompted by the skill:
# - repo-name   : mcp-gateway-registry
# - problem-name: remove-faiss              (use the kebab-case name from the table)
# - model-name  : claude-opus-4-8           (or whichever model is being benchmarked)
```

The skill will create `benchmarks/swe-benchmark-data/claude-opus-4-8/mcp-gateway-registry/remove-faiss/` and populate it with `github-issue.md`, `lld.md`, `review.md`, and `testing.md`. Re-run with a different `model-name` to add a sibling model folder for direct comparison. The skill does not implement the change - that is a separate step the user can take with the design package as input.

#### Scoring

Each of the 4 artifacts is scored 0–100 by an independent ChatGPT session
(cross-lineage judge). Within each artifact, the judge applies the same
4-criterion rubric — each criterion worth 25 points, summing to 100 per
artifact:

| Criterion | 0–25 each | What the judge evaluates |
|-----------|-----------|--------------------------|
| **Completeness** | 25 | Did the artifact identify all affected files, deps, and components? |
| **Correctness** | 25 | Are the proposed changes technically right? Would the design actually work? |
| **Specificity** | 25 | Concrete file paths, code snippets, resource names — not hand-waving? |
| **Risk awareness** | 25 | Rollback plan, backwards-compat, deployment cutover, edge cases? |

**Artifact total = sum of 4 criteria (0–100).**
**Task score = mean of the 4 artifact totals.**

Results are reported in a 5×6 matrix (rows = tasks, columns = models). Per-cell
JSON with criterion breakdowns and judge notes lives at
`{model}/{repo}/{task}/eval.json`. The published matrix and per-model leaderboard
for this repo are in the [top-level README](../../README.md#results-a-worked-example).
