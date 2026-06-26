# Low-Level Design: Migrate remaining sensitive ECS env vars to AWS Secrets Manager

*Created: 2026-06-25*
*Author: Claude (claude-opus-4-8)*
*Status: Draft*

## Table of Contents
1. [Overview](#overview)
2. [Codebase Analysis](#codebase-analysis)
3. [Architecture](#architecture)
4. [Data Models](#data-models)
5. [API / CLI Design](#api--cli-design)
6. [Configuration Parameters](#configuration-parameters)
7. [New Dependencies](#new-dependencies)
8. [Implementation Details](#implementation-details)
9. [Observability](#observability)
10. [Scaling Considerations](#scaling-considerations)
11. [File Changes](#file-changes)
12. [Testing Strategy](#testing-strategy)
13. [Alternatives Considered](#alternatives-considered)
14. [Rollout Plan](#rollout-plan)
15. [Open Questions](#open-questions)
16. [References](#references)

## Overview

### Problem Statement

`terraform/aws-ecs` already integrates AWS Secrets Manager: a dedicated KMS key, ~15 secrets defined in `modules/mcp-gateway/secrets.tf`, an execution-role policy that scopes `secretsmanager:GetSecretValue` to specific ARNs, and task definitions that inject those secrets via the ECS container `secrets` block. However, ~13 genuinely sensitive values are still passed to containers as plaintext via the `environment` block. ECS renders `environment` values directly into the task definition, so these secrets appear in `ecs:DescribeTaskDefinition` output, in task-definition revision history, and in Terraform state. This design migrates those remaining plaintext secrets onto the existing Secrets Manager pathway.

The work is deliberately a pattern extension, not a new mechanism. Every new resource mirrors an existing one in `secrets.tf` and every new IAM grant mirrors an existing entry in `iam.tf`.

### Goals

- Move every remaining secret-bearing `environment` entry into the container `secrets` block, sourced from AWS Secrets Manager.
- Keep secret material out of rendered task definitions and (as far as practical) out of state.
- Grant the ECS task execution role least-privilege `secretsmanager:GetSecretValue` on exactly the new secret ARNs.
- Preserve runtime behavior: containers receive the same env var names with the same values; no application code changes.
- Handle optional/empty secrets gracefully (no dangling resources, no references to non-existent secrets).

### Non-Goals

- No automatic rotation for these externally-managed third-party tokens (follow-up; documented via `#checkov:skip=CKV2_AWS_57`).
- No change to the Keycloak task definition (already uses SSM + Secrets Manager).
- No change to application/runtime code.
- No re-classification of non-secret config (hostnames, ports, flags stay in `environment`).
- Not addressing RDS IAM auth (issue #1303).

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `terraform/aws-ecs/modules/mcp-gateway/secrets.tf` | Defines KMS key + all `aws_secretsmanager_secret`/`_version` resources | New secrets are added here, copying existing blocks |
| `terraform/aws-ecs/modules/mcp-gateway/iam.tf` | `aws_iam_policy.ecs_secrets_access` (execution-role secret access) + ECS exec policies | Extend the `Resource = concat(...)` list with new ARNs |
| `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf` | auth-server, registry, mcpgw, demo task definitions; `environment` + `secrets` blocks | Move plaintext entries from `environment` to `secrets` |
| `terraform/aws-ecs/modules/mcp-gateway/observability.tf` | Grafana service + post-install sidecar; both consume `GF_SECURITY_ADMIN_PASSWORD` | Migrate Grafana admin password; add `secrets` to both containers |
| `terraform/aws-ecs/modules/mcp-gateway/locals.tf` | `name_prefix`, `common_tags` | Naming/tagging conventions for new resources |
| `terraform/aws-ecs/modules/mcp-gateway/variables.tf` | Secret-bearing input variables (all already `sensitive = true`) | Inputs that feed the new secret versions |
| `terraform/aws-ecs/variables.tf` / `terraform.tfvars.example` | Root module variable surface + example | Docs/example updates |
| `terraform/aws-ecs/ecs.tf` | Cluster execution role + DocumentDB secret policy | Confirms execution-role naming (`*-task-execution`) and the existing per-secret grant pattern |
| `terraform/aws-ecs/keycloak-ecs.tf` | Keycloak task (SSM + SM) | Out of scope; confirms the established `valueFrom` JSON-key syntax |

### Existing Patterns Identified

1. **Secret resource pattern** (`secrets.tf`): each secret is an `aws_secretsmanager_secret` (with `name_prefix`, `description`, `recovery_window_in_days = 0`, `kms_key_id = aws_kms_key.secrets.id`, `tags = local.common_tags`, and a `#checkov:skip=CKV2_AWS_57` justification) plus an `aws_secretsmanager_secret_version`. Externally-supplied values use `lifecycle { ignore_changes = [secret_string] }`; generated values use `random_password`.
   - Follow this exactly for every new secret.

2. **Conditional secret pattern**: optional secrets use `count = var.<feature>_enabled ? 1 : 0` and are referenced as `aws_secretsmanager_secret.<name>[0].arn` (see `entra_client_secret`, `okta_*`, `metrics_api_key`).
   - Use this for secrets that are only meaningful when a feature flag is on.

3. **Always-present-with-sentinel pattern**: `embeddings_api_key` is created unconditionally and its version uses `var.embeddings_api_key != "" ? var.embeddings_api_key : "not-configured"`, so the secret always exists even when unset.
   - Use this where the consuming code path is always wired (e.g. `REGISTRY_API_TOKEN`, federation, ANS), so the `secrets` block can reference a stable ARN without per-field `count` gymnastics.

4. **`secrets` block injection** (`ecs-services.tf` lines 413, 1288): `secrets = concat([ {name, valueFrom}, ... ], <conditional lists> )`. Whole-value secrets use `valueFrom = <arn>`; JSON-keyed secrets use `valueFrom = "${arn}:key::"`.
   - New whole-value secrets use the bare ARN form.

5. **Execution-role grant pattern** (`iam.tf` lines 15-36): `Resource = concat([ <always> ], var.<flag> ? [ <conditional arn> ] : [], ...)`. KMS decrypt is granted once on `aws_kms_key.secrets.arn` and covers all secrets that use that key.
   - Append new ARNs to this list with matching conditionality. No KMS change needed because new secrets reuse `aws_kms_key.secrets`.

6. **`environment` vs `secrets` separation**: containers already keep non-secret config in `environment` and secret material in `secrets`. This change moves the misclassified entries to the correct block.

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| `aws_kms_key.secrets` | Uses | All new secrets set `kms_key_id` to this key; execution role already has `kms:Decrypt` on it |
| `aws_iam_policy.ecs_secrets_access` | Extends | Add new ARNs to the `secretsmanager:GetSecretValue` resource list |
| ECS task execution role (`${var.name}-task-execution`) | Depends on | The role the ECS agent uses to fetch secrets at container start; `ecs_secrets_access` is attached to it |
| `terraform-aws-modules/ecs/aws//modules/service` | Uses | Renders `environment` and `secrets` into the container definition |
| auth-server / registry / grafana containers | Modifies | `environment` entries removed; `secrets` entries added |

### Constraints and Limitations Discovered

- **`sensitive = true` is not enough.** All target variables are already `sensitive = true`; that only suppresses CLI/plan display. The secret still renders into the task definition and state. Only `secrets`/`valueFrom` removes it from the task definition.
- **Secret values still touch state.** When Terraform sets `secret_string` from a variable, the value is stored in state. `lifecycle { ignore_changes = [secret_string] }` (existing convention) lets operators set the real value out-of-band (console/CLI/init script) so the long-lived secret need not live in state. This design adopts that convention; the example tfvars keep placeholders.
- **Grafana password is consumed twice** - by the `grafana` container (`GF_SECURITY_ADMIN_PASSWORD`) and by the `grafana-config` sidecar, which interpolates `$${GF_SECURITY_ADMIN_PASSWORD}` inside a shell command. Secrets injected via the `secrets` block surface as ordinary env vars, so the sidecar's `$${...}` reference keeps working - but **both** container definitions must list the secret in their own `secrets` block.
- **`MONGODB_CONNECTION_STRING` already has a Secrets Manager path.** Only the plaintext fallback (`var.mongodb_connection_string != "" && var.mongodb_connection_string_secret_arn == ""`) needs removal. To avoid a hard breaking change for existing deployments that set the plaintext var, the design optionally creates a managed secret from `var.mongodb_connection_string` rather than silently dropping support (see Step 6).
- **`terraform-aws-modules/ecs/aws` rejects duplicate env names.** A name must appear in either `environment` or `secrets`, never both. The migration must delete the `environment` entry in the same change that adds the `secrets` entry.
- **Reserved-env-name lists exist** (`charts/*/reserved-env-names.txt`, Terraform validation). These cover Docker/Helm/Terraform `*_extra_env` collision checks. Since the migrated names were already chart-managed env vars, they should already be present; verify and add any missing names so `extra_env` cannot shadow a secret.

## Architecture

### System Context Diagram

```
                         Terraform apply
                               |
        +----------------------+-----------------------+
        |                      |                       |
        v                      v                       v
 aws_secretsmanager_   aws_iam_policy.        aws_ecs_task_definition
 secret.<new>          ecs_secrets_access     (environment + secrets)
   (KMS: secrets key)    GetSecretValue on       secrets[].valueFrom = ARN
        |                 new ARNs                    |
        |                      |                      |
        +----------+-----------+----------------------+
                   |
                   v
        ECS task execution role  ---- at container start ---->  AWS Secrets Manager
        (${var.name}-task-execution)        GetSecretValue + KMS Decrypt
                   |
                   v
        Container env (SECRET injected as env var, NOT in task def JSON)
```

### Sequence Diagram (container start, after migration)

```
ECS Service        ECS Agent (exec role)     Secrets Manager        KMS            Container
    |                     |                        |                 |                |
    | launch task         |                        |                 |                |
    |-------------------->|                        |                 |                |
    |                     | GetSecretValue(ARN)    |                 |                |
    |                     |----------------------->|                 |                |
    |                     |                        | Decrypt(blob)   |                |
    |                     |                        |---------------->|                |
    |                     |                        |<----------------|                |
    |                     |<-----------------------|                 |                |
    |                     | inject as env var GF_SECURITY_ADMIN_PASSWORD=...          |
    |                     |--------------------------------------------------------->|
    |                     |                        |                 |   app reads os.environ
```

The only change from today's flow is that more env vars now arrive via this Secrets-Manager path instead of being baked into the task definition.

### Component Diagram

```
modules/mcp-gateway/
  secrets.tf   --[defines]--> aws_secretsmanager_secret.{registry_api_token, registry_api_keys,
                                federation_static_token, federation_encryption_key, ans_api_key,
                                ans_api_secret, registration_webhook_auth_token,
                                registration_gate_auth_credential, registration_gate_oauth2_client_secret,
                                github_pat, github_app_private_key, grafana_admin_password,
                                mongodb_connection_string}
  iam.tf       --[grants ]--> ecs_secrets_access.Resource += new ARNs
  ecs-services.tf --[wires]--> auth-server.secrets, registry.secrets (valueFrom)
  observability.tf --[wires]--> grafana.secrets, grafana-config.secrets (valueFrom)
```

## Data Models

Terraform/HCL change; there are no application data models. The "models" here are the secret resource shape and the container `secrets` entry shape.

### New secret resource (template, unconditional + sentinel variant)

```hcl
# Registry static API token (Bearer token for the Registry API)
#checkov:skip=CKV2_AWS_57:Operator-managed static API token, rotation requires coordinated client update
resource "aws_secretsmanager_secret" "registry_api_token" {
  name_prefix             = "${local.name_prefix}-registry-api-token-"
  description             = "Static Bearer token for the Registry API"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "registry_api_token" {
  secret_id     = aws_secretsmanager_secret.registry_api_token.id
  secret_string = var.registry_api_token != "" ? var.registry_api_token : "not-configured"

  lifecycle {
    ignore_changes = [secret_string]
  }
}
```

### Container `secrets` entry (whole-value)

```hcl
{
  name      = "REGISTRY_API_TOKEN"
  valueFrom = aws_secretsmanager_secret.registry_api_token.arn
},
```

### Conditional secret (only when a feature flag gates the consuming code path)

```hcl
resource "aws_secretsmanager_secret" "github_app_private_key" {
  count                   = var.github_app_id != "" ? 1 : 0
  name_prefix             = "${local.name_prefix}-github-app-private-key-"
  description             = "GitHub App PEM private key"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}
```

> Decision rule: use the **sentinel** variant for secrets whose env var is always emitted by the container (so the `secrets` block always needs a valid ARN); use the **conditional** variant where the env var itself is only emitted under a feature flag, and append the ARN to both the `secrets` block and the IAM list under the same condition. The default in this design is the sentinel variant, because the audited containers emit these env vars unconditionally today.

## API / CLI Design

No application API or CLI changes. The operator-facing "interface" is Terraform:

**Invocation:**
```bash
cd terraform/aws-ecs
terraform init
terraform plan -out=tfplan
terraform apply tfplan
```

**Expected plan output (excerpt):**
```
# module.mcp_gateway.aws_secretsmanager_secret.registry_api_token will be created
# module.mcp_gateway.aws_secretsmanager_secret_version.registry_api_token will be created
# module.mcp_gateway.aws_iam_policy.ecs_secrets_access will be updated in-place
#   ~ policy = ... (adds registry_api_token ARN)
# module.mcp_gateway...aws_ecs_task_definition.registry will be updated
#   - environment { name = "REGISTRY_API_TOKEN" ... }   # removed
#   + secrets     { name = "REGISTRY_API_TOKEN" valueFrom = "<arn>" }  # added
```

**Error cases:**
- Referencing `aws_secretsmanager_secret.x[0].arn` for a secret created with `count` while the flag is off -> index error. Mitigation: gate the `secrets`/IAM reference with the same condition.
- Same env name in both `environment` and `secrets` -> module/validation error. Mitigation: remove the `environment` entry in the same commit.

## Configuration Parameters

### New Environment Variables

None. No new env vars are introduced. The set of env vars delivered to containers is unchanged; only the delivery channel for the listed names moves from `environment` to `secrets`.

### New / Changed Input Variables

No new Terraform variables are required - every value already has a `sensitive = true` variable. One **optional** convenience variable could be added if operators want a generated default for `registry_api_token` (mirroring `secret_key`), but the default design reuses the existing variable with a `"not-configured"` sentinel and is therefore additive-free.

### Settings / Config Class Updates

Not applicable (no application settings class involved).

### Deployment Surface Checklist

| Surface | File | Action |
|---------|------|--------|
| Terraform secret defs | `modules/mcp-gateway/secrets.tf` | Add the new secret + version resources |
| Terraform IAM | `modules/mcp-gateway/iam.tf` | Add new ARNs to `ecs_secrets_access` |
| Terraform task defs | `modules/mcp-gateway/ecs-services.tf`, `observability.tf` | Move entries `environment` -> `secrets` |
| Example tfvars | `terraform/aws-ecs/terraform.tfvars.example` | Update comments: values now stored in Secrets Manager |
| Operator docs | `terraform/aws-ecs/OPERATIONS.md`, `README.md` | Document secret names + how to set values out-of-band |
| Reserved env names | `charts/*/reserved-env-names.txt` | Verify migrated names are present (over-rejection preferred) |

## New Dependencies

This change uses only existing dependencies. No new Terraform providers, modules, or packages. It reuses `aws_kms_key.secrets`, the AWS provider's `aws_secretsmanager_secret`/`aws_secretsmanager_secret_version`, and the existing ECS service module.

## Implementation Details

### Step-by-Step Plan (for a future implementer)

#### Step 1: Inventory and freeze the target list

**File:** none (analysis)

Confirm the exact set of secret-bearing `environment` entries (this design's table). Re-grep before editing, since line numbers drift:

```bash
cd terraform/aws-ecs/modules/mcp-gateway
grep -nE 'name *= *"(REGISTRY_API_TOKEN|REGISTRY_API_KEYS|FEDERATION_STATIC_TOKEN|FEDERATION_ENCRYPTION_KEY|ANS_API_KEY|ANS_API_SECRET|REGISTRATION_WEBHOOK_AUTH_TOKEN|REGISTRATION_GATE_AUTH_CREDENTIAL|REGISTRATION_GATE_OAUTH2_CLIENT_SECRET|GITHUB_PAT|GITHUB_APP_PRIVATE_KEY|GF_SECURITY_ADMIN_PASSWORD|MONGODB_CONNECTION_STRING)"' ecs-services.tf observability.tf
```

#### Step 2: Add new secret resources

**File:** `modules/mcp-gateway/secrets.tf` (append near related existing secrets)

For each of the 12 standalone secrets, add an `aws_secretsmanager_secret` + `aws_secretsmanager_secret_version` pair following the template in [Data Models](#data-models). Use the sentinel variant by default. Suggested names (all `name_prefix = "${local.name_prefix}-<slug>-"`):

| Secret resource | slug | secret_string source |
|-----------------|------|----------------------|
| `registry_api_token` | `registry-api-token` | `var.registry_api_token` w/ sentinel |
| `registry_api_keys` | `registry-api-keys` | `var.registry_api_keys` w/ sentinel |
| `federation_static_token` | `federation-static-token` | `var.federation_static_token` w/ sentinel |
| `federation_encryption_key` | `federation-encryption-key` | `var.federation_encryption_key` w/ sentinel |
| `ans_api_key` | `ans-api-key` | `var.ans_api_key` w/ sentinel |
| `ans_api_secret` | `ans-api-secret` | `var.ans_api_secret` w/ sentinel |
| `registration_webhook_auth_token` | `registration-webhook-auth-token` | `var.registration_webhook_auth_token` w/ sentinel |
| `registration_gate_auth_credential` | `registration-gate-auth-credential` | `var.registration_gate_auth_credential` w/ sentinel |
| `registration_gate_oauth2_client_secret` | `registration-gate-oauth2-client-secret` | `var.registration_gate_oauth2_client_secret` w/ sentinel |
| `github_pat` | `github-pat` | `var.github_pat` w/ sentinel |
| `github_app_private_key` | `github-app-private-key` | `var.github_app_private_key` w/ sentinel |
| `grafana_admin_password` | `grafana-admin-password` | `var.grafana_admin_password` (only when `var.enable_observability`; use `count`) |

Each gets a `#checkov:skip=CKV2_AWS_57:<reason>` line consistent with existing entries. Use `lifecycle { ignore_changes = [secret_string] }` on every version so operators can set the real value out-of-band without state drift.

`grafana_admin_password` should be **conditional** (`count = var.enable_observability ? 1 : 0`) because the Grafana service itself only exists under that flag.

#### Step 3: Handle `MONGODB_CONNECTION_STRING` plaintext fallback

**File:** `modules/mcp-gateway/secrets.tf`, `ecs-services.tf`

Two acceptable options; the design recommends Option A:

- **Option A (recommended, no behavior loss):** When `var.mongodb_connection_string != "" && var.mongodb_connection_string_secret_arn == ""`, create a managed secret `aws_secretsmanager_secret.mongodb_connection_string` (with `count`) holding `var.mongodb_connection_string`, and reference it from the `secrets` block. The plaintext `environment` fallback at `ecs-services.tf` lines 403-407 and 1278-1282 is removed. Operators who already pass `mongodb_connection_string_secret_arn` are unaffected.
- **Option B (strict):** Drop the plaintext-var path entirely and require `mongodb_connection_string_secret_arn`. Simpler, but a breaking change for deployments using the plaintext var. Document loudly if chosen.

#### Step 4: Move auth-server entries `environment` -> `secrets`

**File:** `modules/mcp-gateway/ecs-services.tf` (auth-server container, `environment` ~lines 97-411, `secrets` concat starting ~line 413)

Delete the `environment` blocks for `REGISTRY_API_TOKEN`, `REGISTRY_API_KEYS`, `FEDERATION_STATIC_TOKEN`, `FEDERATION_ENCRYPTION_KEY`, `ANS_API_KEY`, `ANS_API_SECRET`, and the plaintext `MONGODB_CONNECTION_STRING`. Add matching `{ name, valueFrom }` entries into the first array of the `secrets = concat([...], ...)`:

```hcl
secrets = concat(
  [
    { name = "SECRET_KEY",               valueFrom = aws_secretsmanager_secret.secret_key.arn },
    # ... existing entries ...
    { name = "REGISTRY_API_TOKEN",       valueFrom = aws_secretsmanager_secret.registry_api_token.arn },
    { name = "REGISTRY_API_KEYS",        valueFrom = aws_secretsmanager_secret.registry_api_keys.arn },
    { name = "FEDERATION_STATIC_TOKEN",  valueFrom = aws_secretsmanager_secret.federation_static_token.arn },
    { name = "FEDERATION_ENCRYPTION_KEY",valueFrom = aws_secretsmanager_secret.federation_encryption_key.arn },
    { name = "ANS_API_KEY",              valueFrom = aws_secretsmanager_secret.ans_api_key.arn },
    { name = "ANS_API_SECRET",           valueFrom = aws_secretsmanager_secret.ans_api_secret.arn },
  ],
  # existing conditional lists (Entra/Okta/Auth0/Mongo) unchanged ...
)
```

#### Step 5: Move registry entries `environment` -> `secrets`

**File:** `modules/mcp-gateway/ecs-services.tf` (registry container, `environment` ~lines 698-1286, `secrets` concat ~line 1288)

Delete the `environment` blocks for `REGISTRY_API_TOKEN`, `REGISTRY_API_KEYS`, `FEDERATION_STATIC_TOKEN`, `FEDERATION_ENCRYPTION_KEY`, `ANS_API_KEY`, `ANS_API_SECRET`, `REGISTRATION_WEBHOOK_AUTH_TOKEN`, `REGISTRATION_GATE_AUTH_CREDENTIAL`, `REGISTRATION_GATE_OAUTH2_CLIENT_SECRET`, `GITHUB_PAT`, `GITHUB_APP_PRIVATE_KEY`, and the plaintext `MONGODB_CONNECTION_STRING`. Add the corresponding `valueFrom` entries to the registry `secrets` concat, mirroring Step 4.

Leave non-secret GitHub config (`GITHUB_APP_ID`, `GITHUB_APP_INSTALLATION_ID`, `GITHUB_API_BASE_URL`, `GITHUB_EXTRA_HOSTS`) and non-secret gate config (URLs, header names, scopes, client_id) in `environment`.

#### Step 6: Migrate Grafana admin password (both containers)

**File:** `modules/mcp-gateway/observability.tf`

The `grafana` container has an `environment` array (~line 544) and the `grafana-config` sidecar has its own (~line 642), both containing `GF_SECURITY_ADMIN_PASSWORD`. Remove both `environment` entries and add a `secrets` block to **each** container:

```hcl
secrets = [
  { name = "GF_SECURITY_ADMIN_PASSWORD", valueFrom = aws_secretsmanager_secret.grafana_admin_password[0].arn },
]
```

The sidecar's shell reference `$${GF_SECURITY_ADMIN_PASSWORD}` continues to resolve because injected secrets become normal env vars. Confirm neither container already declares a `secrets` block (the grafana container does not today; if a future change adds one, append rather than overwrite).

#### Step 7: Extend the execution-role IAM policy

**File:** `modules/mcp-gateway/iam.tf` (`aws_iam_policy.ecs_secrets_access`, resource list lines 15-36)

Append the new ARNs to the `Resource = concat(...)` list:

```hcl
Resource = concat(
  [
    aws_secretsmanager_secret.secret_key.arn,
    # ... existing always-on ARNs ...
    aws_secretsmanager_secret.registry_api_token.arn,
    aws_secretsmanager_secret.registry_api_keys.arn,
    aws_secretsmanager_secret.federation_static_token.arn,
    aws_secretsmanager_secret.federation_encryption_key.arn,
    aws_secretsmanager_secret.ans_api_key.arn,
    aws_secretsmanager_secret.ans_api_secret.arn,
    aws_secretsmanager_secret.registration_webhook_auth_token.arn,
    aws_secretsmanager_secret.registration_gate_auth_credential.arn,
    aws_secretsmanager_secret.registration_gate_oauth2_client_secret.arn,
    aws_secretsmanager_secret.github_pat.arn,
    aws_secretsmanager_secret.github_app_private_key.arn,
  ],
  var.enable_observability ? [aws_secretsmanager_secret.grafana_admin_password[0].arn] : [],
  var.mongodb_connection_string != "" && var.mongodb_connection_string_secret_arn == "" ? [aws_secretsmanager_secret.mongodb_connection_string[0].arn] : [],
  # ... existing conditional lists unchanged ...
)
```

The existing `kms:Decrypt`/`kms:DescribeKey` statement on `aws_kms_key.secrets.arn` already covers the new secrets - **no KMS policy change needed** because every new secret uses that key.

> Note: the Grafana service may run under a different task execution role than the main `ecs_secrets_access`-attached role. The implementer must confirm which execution role the Grafana service uses (it is defined in `observability.tf` via the same `terraform-aws-modules/ecs/aws` module) and ensure `ecs_secrets_access` (or an equivalent grant) is attached to it. If Grafana uses a separate role, add an equivalent statement there. This is the single most error-prone point in the change - see review.md.

#### Step 8: Update reserved-env-name lists and docs

**Files:** `charts/*/reserved-env-names.txt`, `terraform.tfvars.example`, `OPERATIONS.md`, `README.md`

Verify the migrated names already appear in the reserved lists (they were chart-managed before). Update the example tfvars comments to say these values are stored in Secrets Manager and may be set out-of-band, and document the new secret names and the "set the real value in the console after first apply" workflow in `OPERATIONS.md`.

### Error Handling

- **Count/index mismatches:** any `[0]` reference must be guarded by the same condition used on the resource `count`. Validate with `terraform plan` under both flag states.
- **Duplicate env name:** removing the `environment` entry and adding the `secrets` entry must happen together; otherwise the ECS module errors on duplicate names.
- **Empty secret values:** the sentinel (`"not-configured"`) guarantees a non-empty `secret_string` (Secrets Manager rejects empty strings) and a stable ARN for the `secrets` block.

### Logging

Terraform/infra change - no application logging. The implementer should rely on `terraform plan` diffs and `aws ecs describe-task-definition` output as the verification signal (see testing.md). Application logs must continue to avoid printing these secret values (existing CLAUDE.md rule).

## Observability

- **Plan-time:** `terraform plan` is the primary signal - it must show new SM resources, an in-place IAM policy update, and task-definition diffs moving names out of `environment` and into `secrets`.
- **Post-apply:** `aws ecs describe-task-definition` should show the migrated names only under `secrets` with `valueFrom` ARNs; `aws secretsmanager list-secrets` should show the new secrets; CloudTrail `GetSecretValue` events from the execution role confirm the runtime path.
- **Drift:** because versions use `ignore_changes = [secret_string]`, operator-set values do not show as drift on subsequent plans.

## Scaling Considerations

- **Per-secret quota / API calls:** each container start triggers one `GetSecretValue` per referenced secret. Adding ~12 secrets to two services is well within Secrets Manager rate limits; no batching needed.
- **Cost:** Secrets Manager bills per secret per month plus per 10k API calls. ~12-14 new secrets is a negligible cost increase. Note it in docs.
- **Cold start:** marginally more secret fetches at task start; the agent fetches them in parallel and the added latency is sub-second. No horizontal-scaling impact.
- **Single KMS key:** all secrets share `aws_kms_key.secrets`; KMS decrypt volume rises slightly but stays far below limits.

## File Changes

### New Files

| File Path | Description |
|-----------|-------------|
| (none) | All changes are additions to existing files |

### Modified Files

| File Path | Lines | Change Description |
|-----------|-------|--------------------|
| `modules/mcp-gateway/secrets.tf` | ~+140 | Add 12-13 secret + version resource pairs |
| `modules/mcp-gateway/iam.tf` | ~+15 | Append new ARNs to `ecs_secrets_access` resource list |
| `modules/mcp-gateway/ecs-services.tf` | ~-60 / +30 | Remove plaintext `environment` entries (auth-server + registry); add `secrets` entries; remove Mongo plaintext fallback |
| `modules/mcp-gateway/observability.tf` | ~-4 / +8 | Remove 2 plaintext entries; add `secrets` block to grafana + grafana-config |
| `terraform/aws-ecs/terraform.tfvars.example` | ~+15 | Update comments: values stored in Secrets Manager |
| `terraform/aws-ecs/OPERATIONS.md` | ~+30 | Document new secret names + out-of-band set workflow |
| `terraform/aws-ecs/README.md` | ~+10 | Note expanded Secrets Manager coverage |
| `charts/*/reserved-env-names.txt` | ~0-+5 | Verify/add migrated names |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| New Terraform (secrets) | ~140 |
| New Terraform (IAM) | ~15 |
| Modified Terraform (task defs) | ~70 net |
| Docs | ~55 |
| **Total** | **~280** |

## Testing Strategy

See `./testing.md`. In summary: `terraform validate` + `terraform plan` under default and all-features-enabled configs; grep the rendered plan/task-definition JSON to prove zero secret literals remain in `environment`; backwards-compat checks for deployments leaving optional secrets empty; a deploy-and-verify pass via `aws ecs describe-task-definition` and a smoke test that the services still authenticate.

## Alternatives Considered

### Alternative 1: SSM Parameter Store (SecureString) instead of Secrets Manager

**Description:** Store these secrets as SSM SecureString parameters, as the Keycloak task already does for some values.

**Pros:** Cheaper (no per-secret monthly charge); already used by Keycloak.

**Cons:** The dominant pattern in the module under change is Secrets Manager (one KMS key, one IAM policy, ~15 existing secrets). Splitting these secrets into SSM would fork the pattern, complicate the IAM policy (different actions/ARNs), and reduce consistency.

**Why Rejected:** Consistency with the established module pattern outweighs the small cost saving. Issue #1134 is explicitly about Secrets Manager.

### Alternative 2: One consolidated JSON secret with many keys

**Description:** Put all migrated secrets into a single `aws_secretsmanager_secret` as a JSON blob and reference fields via `valueFrom = "${arn}:KEY::"`.

**Pros:** Fewer resources; one ARN in IAM.

**Cons:** Coarser access control (any consumer gets all keys); harder to rotate individually; mixes unrelated secrets (GitHub PAT next to Grafana password); diverges from the existing one-secret-per-concern convention. Conditional creation of subsets becomes awkward.

**Why Rejected:** Per-secret resources match the existing convention and give finer-grained IAM and rotation control.

### Alternative 3: Application reads from Secrets Manager directly at runtime (the literal task-table wording)

**Description:** Change application code to call Secrets Manager via the AWS SDK at startup instead of reading env vars.

**Pros:** Secrets never appear as env vars at all (defends against `/proc/<pid>/environ` reads inside the container).

**Cons:** Requires application code changes across multiple services, new task-**role** (not execution-role) permissions, error handling, caching, and a larger blast radius. Higher risk for the same primary benefit (keeping secrets out of the task definition and state).

**Why Rejected:** The ECS `secrets` block achieves the issue's security goal (no plaintext in task def / state / describe output) with zero code change and lower risk. The in-app SDK approach is a reasonable future hardening step but is out of scope here. This is the deliberate divergence from the benchmark table's "update application code" phrasing, called out in the issue.

### Comparison Matrix

| Criteria | Chosen (`secrets` block, per-secret) | Alt 1 (SSM) | Alt 2 (one JSON secret) | Alt 3 (in-app SDK) |
|----------|--------------------------------------|-------------|-------------------------|--------------------|
| Consistency w/ existing module | High | Low | Medium | Low |
| Code change required | None | None | None | High |
| IAM granularity | High | Medium | Low | High |
| Rotation flexibility | High | Medium | Low | High |
| Cost | Medium | Low | Low | Medium |
| Risk | Low | Medium | Medium | High |

## Rollout Plan

- **Phase 0 (pre-work):** Inventory confirmation; decide Option A vs B for Mongo.
- **Phase 1 (implementation, out of scope for this skill):** Add secrets, wire `secrets` blocks, extend IAM, remove plaintext entries.
- **Phase 2 (validation):** `terraform validate` + `terraform plan` (default + all-features); grep task-def JSON; helm reserved-name check. See testing.md.
- **Phase 3 (staged apply):** Apply to a non-prod stack first. After apply, set the real secret values in the Secrets Manager console (because versions use `ignore_changes`), then force a new deployment so tasks pick up the values. Smoke-test auth, federation, GitHub skill fetch, Grafana login.
- **Phase 4 (prod):** Apply during a maintenance window; the task-definition revision changes, so services roll. Keep the prior revision for fast rollback (`aws ecs update-service --task-definition <prev-revision>`).
- **Rollback:** revert the Terraform change and re-apply, or roll the ECS service back to the previous task-definition revision. Secrets can remain (harmless) or be deleted.

## Open Questions

1. **Grafana execution role:** does the Grafana service share the `ecs_secrets_access`-attached execution role, or a distinct one created inside `observability.tf`? This determines whether Step 7 needs a second IAM grant. (Implementer must confirm before apply.)
2. **Mongo fallback:** Option A (managed secret from the plaintext var) vs Option B (drop plaintext support)? Recommend A to avoid breaking existing deployments; confirm with maintainers.
3. **Generated defaults:** should `registry_api_token`/`federation_static_token` get a `random_password` default (like `secret_key`) instead of a `"not-configured"` sentinel, so a fresh stack is secure-by-default? Out of scope but worth a follow-up.
4. **State hygiene:** do maintainers want the long-lived values kept entirely out of state (set only via console) or is variable-driven seeding acceptable? The design supports both via `ignore_changes`.

## References

- AWS docs: [Specifying sensitive data using Secrets Manager (ECS)](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/specifying-sensitive-data-secrets.html)
- Existing pattern: `terraform/aws-ecs/modules/mcp-gateway/secrets.tf`, `iam.tf`
- Existing JSON-key `valueFrom` usage: `terraform/aws-ecs/keycloak-ecs.tf`
- Issue #1134 (this task); related #1303, #1026
