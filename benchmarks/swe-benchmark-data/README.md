# SWE Benchmark Data

This directory holds the inputs and outputs of an LLM software-engineering benchmark. Each run takes a real-world problem inside a real repository and asks a specific model (driven by the `/swe` skill) to produce a GitHub issue spec, a low-level design, an expert review, and a testing plan. Multiple models can attempt the same problem so their artifacts can be compared side-by-side. **The skill stops at design and review - it does not implement the change.**

## Directory Layout

```
benchmarks/swe-benchmark-data/
├── README.md                       # This file
└── {repo-name}/
    ├── repo/                       # Cloned source (gitignored - cloned by contributor, never committed)
    ├── {problem-name}/
    │   ├── {model-name-A}/         # Artifacts produced by model A on this problem
    │   │   ├── github-issue.md
    │   │   ├── lld.md
    │   │   ├── review.md
    │   │   └── testing.md
    │   └── {model-name-B}/         # Artifacts produced by model B on the same problem
    │       └── ...
    └── {next-problem-name}/
        └── ...
```

The `repo/` checkout under each `{repo-name}/` is **not** stored in this repository. It is added to `.gitignore` so contributors clone their own copy at the right tag before invoking `/swe`. This avoids carrying large third-party trees and keeps the per-tag history pinned by the contributor, not by this repo.

## How to Set Up a Benchmark Repository Locally

Clone each target repository at the documented tag inside its `{repo-name}/` folder. Clone into a `repo/` subdirectory so artifacts and source never collide:

```bash
cd benchmarks/swe-benchmark-data/{repo-name}
git clone --branch <tag> --depth 1 https://github.com/<owner>/<name>.git repo
```

Use `--depth 1` to keep the checkout small. If you later need full history, run `git fetch --unshallow` from inside `repo/`.

---

## Benchmark Repositories

Each section below documents one target repository. To benchmark a model on one of its tasks, clone the repo at the listed tag and run `/swe` against the task description.

### 1. mcp-gateway-registry

| Field | Value |
|-------|-------|
| Source | https://github.com/agentic-community/mcp-gateway-registry |
| Tag | `1.24.4` |
| Local path | `benchmarks/swe-benchmark-data/mcp-gateway-registry/repo/` |
| Artifact path | `benchmarks/swe-benchmark-data/mcp-gateway-registry/{problem-name}/{model-name}/` |

#### Setup

```bash
cd benchmarks/swe-benchmark-data/mcp-gateway-registry
git clone --branch 1.24.4 --depth 1 https://github.com/agentic-community/mcp-gateway-registry.git repo
```

#### Tasks

The tasks below are run with multiple models via the `/swe` skill. For each `{model-name}`, the resulting artifacts land at `benchmarks/swe-benchmark-data/mcp-gateway-registry/{problem-name}/{model-name}/`.

| # | Problem name (folder) | Description |
|---|-----------------------|-------------|
| 1 | `remove-faiss` | Remove FAISS from the codebase and documentation. FAISS is obsolete in this repo. Delete all FAISS imports, dependencies, configuration, and references in docs. Replace any remaining vector-search needs with the maintained alternative already used elsewhere in the repo. |
| 2 | `remove-efs-from-terraform-aws-ecs` | Remove EFS from `terraform/aws-ecs/`. EFS is obsolete in this deployment. Delete the EFS file system, mount targets, security groups, and any task-definition volume mounts that reference it. Update `variables.tf`, `terraform.tfvars.example`, and module wiring. Verify `terraform validate` and `terraform plan` still succeed. |

##### Future Enhancements

Additional tasks will be added one by one as enhancements are scoped. Each new task gets its own `{problem-name}` folder (kebab-case) and a row in the table above.

#### How to Run a Task with `/swe`

```
/swe

# When prompted by the skill:
# - repo-name   : mcp-gateway-registry
# - problem-name: remove-faiss              (use the kebab-case name from the table)
# - model-name  : claude-opus-4-7           (or whichever model is being benchmarked)
```

The skill will create `benchmarks/swe-benchmark-data/mcp-gateway-registry/remove-faiss/claude-opus-4-7/` and populate it with `github-issue.md`, `lld.md`, `review.md`, and `testing.md`. Re-run with a different `model-name` to add a sibling folder for direct comparison. The skill does not implement the change - that is a separate step the user can take with the design package as input.
