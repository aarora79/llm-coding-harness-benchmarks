# Low-Level Design: Migrate remaining sensitive ECS env vars to AWS Secrets Manager

*Created: 2026-07-23*
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

## Overview

### Problem Statement

The ECS Terraform stack (`terraform/aws-ecs/`) still injects a tier of sensitive values into the `auth-server`, `registry`, and `grafana` containers as plaintext `environment` entries. Although the source Terraform variables are `sensitive = true`, anything placed in a container `environment` block is rendered into the task-definition JSON and persisted in Terraform state as cleartext. This defeats encryption at rest, read auditing, and rotation.

The repo has already migrated the first tier of secrets (`SECRET_KEY`, DocumentDB credentials, Keycloak client/admin/M2M secrets, Entra/Okta/Auth0 client secrets, `METRICS_API_KEY`, OTLP headers) to AWS Secrets Manager via the container `secrets` block. This design migrates the remaining plaintext secrets using the identical established pattern, and adds a single feature flag so operators can fall back to the plaintext path during migration.

### Goals

- Move all remaining plaintext sensitive `environment` values on `auth-server`, `registry`, and `grafana` into AWS Secrets Manager, consumed via the ECS `secrets` block `valueFrom`.
- Reuse the existing KMS key (`aws_kms_key.secrets`) and the existing IAM policy (`aws_iam_policy.ecs_secrets_access`) rather than creating parallel infrastructure.
- Provide a `var.use_secrets_manager_for_env` flag (default `true`) that toggles between the Secrets Manager path and the legacy plaintext `environment` path, mutually exclusive per variable.
- Require zero application code changes; the app already reads every value from a same-named process environment variable.

### Non-Goals

- No Helm/EKS or non-ECS surface changes.
- No automatic rotation of these app secrets (they remain non-rotatable, externally managed).
- No change to how the application reads configuration.
- No automatic migration of the free-form `*_extra_env` passthrough variables.

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `terraform/aws-ecs/modules/mcp-gateway/secrets.tf` | Defines `aws_kms_key.secrets`, alias, and all existing `aws_secretsmanager_secret`/`_version` resources | New secrets are added here following the same conventions |
| `terraform/aws-ecs/modules/mcp-gateway/iam.tf` | Defines `aws_iam_policy.ecs_secrets_access` with the `GetSecretValue` `Resource` concat-list and the `kms:Decrypt` statement | New secret ARNs are appended to the `Resource` concat list |
| `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf` | `container_definitions` maps for auth-server and registry, each with `environment = concat(...)` and `secrets = concat(...)` | Move the target vars from `environment` to `secrets` (toggle-gated) |
| `terraform/aws-ecs/modules/mcp-gateway/observability.tf` | `container_definitions` for metrics-service, grafana, grafana-config | Add a `secrets` block to grafana / grafana-config for the admin password |
| `terraform/aws-ecs/modules/mcp-gateway/variables.tf` | Declares all `sensitive = true` source variables and service policy attachments | Add `use_secrets_manager_for_env`; source vars already exist |
| `terraform/aws-ecs/modules/mcp-gateway/locals.tf` | Builds shared locals used by container definitions | Add helper locals for the toggled env/secrets fragments |
| `terraform/aws-ecs/keycloak-ecs.tf`, `keycloak-database.tf` | Keycloak uses its own raw `aws_ecs_task_definition` + its own exec role | Not in scope (already fully on Secrets Manager/SSM) |
| `registry/core/config.py` | Pydantic `BaseSettings`, `case_sensitive=False`, env overrides `.env` | Confirms fallback is transparent to the app |
| `auth_server/server.py` | Direct `os.environ.get(...)` reads at import time | Confirms env injection works identically for both paths |

### Existing Patterns Identified

1. **Secrets Manager secret + version pair.** Every secret in `secrets.tf` is an `aws_secretsmanager_secret` (with `name_prefix`, `recovery_window_in_days = 0`, `kms_key_id = aws_kms_key.secrets.id`, `tags = local.common_tags`, and a `#checkov:skip=CKV2_AWS_57` justification) plus an `aws_secretsmanager_secret_version` holding the value. Optional secrets use `count = <condition> ? 1 : 0`. Follow this exactly.
   - Files: `modules/mcp-gateway/secrets.tf:91-102` (secret_key), `:340-355` (metrics_api_key, count-gated).

2. **Container `secrets = concat(...)` block.** Each service's container object builds `secrets` as a `concat` of a base list and conditional lists. Entries are `{ name = "ENV_NAME", valueFrom = <arn> }`, and for JSON secrets the `valueFrom` uses the `":jsonkey::"` suffix.
   - Files: `modules/mcp-gateway/ecs-services.tf:413-480` (auth), `:1288-1365` (registry).

3. **Single shared GetSecretValue policy.** `aws_iam_policy.ecs_secrets_access` (`iam.tf:4-52`) lists every readable secret ARN in a `concat(...)` `Resource` list and is attached to BOTH the execution and task roles of every module service via the ECS service module's `tasks_iam_role_policies` / `task_exec_iam_role_policies` maps (`ecs-services.tf:51-64`). Add new ARNs here, gated by the same conditions used to create them.

4. **KMS decrypt via key policy.** `aws_kms_key.secrets` key policy grants `kms:Decrypt`/`kms:DescribeKey` to any in-account role whose ARN matches `role/*task-exec*` (`secrets.tf:34-41`), and the policy also grants `kms:Decrypt` on that key ARN explicitly (`iam.tf:38-47`). Reused as-is.

5. **App reads secrets from env by name.** `registry/core/config.py` uses Pydantic `BaseSettings` with `case_sensitive=False` and env precedence over `.env` (`config.py:56-60`); `auth_server/server.py` reads `os.environ.get("REGISTRY_API_TOKEN", "")` etc. ECS-injected `secrets` appear as normal env vars, so both paths are transparent.

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| `aws_kms_key.secrets` | Uses | Encrypts every new secret; no change to the key |
| `aws_iam_policy.ecs_secrets_access` | Extends | Append new secret ARNs to the `Resource` concat list |
| auth-server `container_definitions` | Modifies | Move 6 vars from `environment` to `secrets` (toggle-gated) |
| registry `container_definitions` | Modifies | Move 13 vars from `environment` to `secrets` (toggle-gated) |
| grafana / grafana-config `container_definitions` | Modifies | Add a new `secrets` block for the admin password |
| ECS service module role attachments | Confirms | Ensure grafana service attaches `ecs_secrets_access` to its exec + task roles |

### Constraints and Limitations Discovered

- **ECS forbids the same variable name in both `environment` and `secrets`.** The task definition fails registration with `Duplicate environment variable name` if a name appears in both. The toggle must therefore be mutually exclusive per variable, not additive.
- **Grafana has no `secrets` block today** and its service module attachment must be verified to include `ecs_secrets_access` (the other module services attach it explicitly at `ecs-services.tf:51-64`; grafana in `observability.tf` must do the same).
- **Grafana-config runs the password inside a shell command** (`GURL="http://admin:$${GF_SECURITY_ADMIN_PASSWORD}@localhost:3000"`, `observability.tf:630`). Injecting the value via `secrets` still exposes it to that container's process as an env var, so the command works unchanged; only the source of the env var changes.
- **JSON vs raw secrets.** The existing single-value secrets (e.g. `secret_key`, `metrics_api_key`) store a raw string and are referenced by bare ARN. The Keycloak/DocumentDB secrets store JSON and use `":key::"`. New secrets here are single values, so store them as raw strings and reference by bare ARN (simpler, matches `secret_key`).
- **`GITHUB_APP_PRIVATE_KEY` is a multi-line PEM.** Secrets Manager stores it verbatim; ECS injects it as-is. No encoding change needed. The app reads it via Pydantic and does `settings.github_app_private_key.replace("\\n", "\n")` (`registry/services/github_auth.py:129`), which is a no-op for a real multi-line PEM and expands escaped `\n` in a single-line PEM, so both forms work. This must be confirmed in the smoke test, not assumed.
- **Grafana admin password defaults to empty.** `var.grafana_admin_password` is declared with `default = ""` (`modules/mcp-gateway/variables.tf:1175-1180`), NOT a non-empty default. Its secret must therefore be gated on `var.enable_observability` (the grafana service itself only exists when observability is enabled, `observability.tf:482`) so a non-observability deployment does not create an orphan secret or fail on an empty `secret_string`. The other vars also default to `""` and their secrets are `count`-gated on non-empty.
- **`aws_secretsmanager_secret_version` rejects an empty `secret_string`.** Every optional secret must be `count`-gated on its source var being non-empty, or `apply` fails. This applies to the grafana password too: gating only on `enable_observability` is insufficient if the operator enables observability but leaves the password empty. See Step 2 for the grafana handling (generate a `random_password` when empty, mirroring `secret_key`/`metrics_api_key`).
- **Never inject a truthy placeholder for an empty secret (critical).** A tempting way to satisfy the non-empty `secret_string` rule is a sentinel like `var.x != "" ? var.x : "not-configured"`. This is WRONG for these variables and would be a security regression. The application gates several of these credentials on truthiness (`if REGISTRY_API_TOKEN:` at `auth_server/server.py:380`, `if not REGISTRY_API_TOKEN or not hmac.compare_digest(...)` at `:2592`, `if settings.github_pat:` at `registry/services/github_auth.py:110`, and `Fernet(key.encode())` for `FEDERATION_ENCRYPTION_KEY`). A non-empty sentinel flips these from "feature off / falsy" to "configured with the literal value `not-configured`", which (a) makes `not-configured` a VALID admin bearer token on the federation-token admin endpoint, (b) sends `Authorization: Bearer not-configured` to GitHub/webhook/ANS endpoints, and (c) raises a Fernet error on every federation call. Therefore the design uses `count`-gating so an empty var produces an ABSENT env var (Pydantic falls back to its `""`/`None` field default, `os.getenv(x, "")` returns `""` - both falsy, matching today's behavior). This preserves the app's truthiness semantics exactly.
- **Empty-value semantic change in the secrets-on path (must smoke-test).** In the default (`use_secrets_manager_for_env = true`) path, an empty optional var yields NO env var at all (secret not created, secrets fragment filtered out), whereas today the container receives `X=""` inline. For every one of the 13 consumers this is benign because they read via Pydantic field defaults (`""`/`None`) or `os.getenv(x, "")`, all of which are falsy and equivalent to `""`. This equivalence must be verified in the smoke test (see `testing.md`), because a consumer that distinguished "set-but-empty" from "unset" would observe a difference. The plaintext fallback path (`false`) still emits `X=""` unconditionally, so it remains byte-for-byte identical to today.
- **Terraform state confidentiality (out of scope but load-bearing).** Even on the Secrets Manager path, `aws_secretsmanager_secret_version.secret_string = var.<name>` stores the value in Terraform state in cleartext. The root stack currently declares `terraform {}` with no `backend` block (`main.tf`), so state defaults to a local unencrypted file. This means the migration moves secrets out of the task-definition JSON but NOT out of state unless an encrypted remote backend (S3 + SSE-KMS + versioning + TLS-only bucket policy + state locking) is configured. Configuring the backend is out of scope for this issue (it is a repo-wide concern), but it is flagged as a hard prerequisite for the migration's confidentiality claim to hold - see Open Questions.

## Architecture

### System Context Diagram

```
                      Terraform apply
                            |
        +-------------------+--------------------+
        |                   |                    |
        v                   v                    v
  aws_secretsmanager   aws_iam_policy      aws_ecs_task_definition
   _secret (+version)  .ecs_secrets_access   (auth / registry / grafana)
        |                   |                    |
        | encrypted by      | grants             | secrets[].valueFrom = ARN
        v                   | GetSecretValue     v
  aws_kms_key.secrets  <----+--------------  ECS agent (task startup)
        ^                                         |
        | kms:Decrypt (task-exec role)            | injects as env vars
        +-----------------------------------------+
                                                  v
                                     Container process env
                                  (SECRET_KEY, GITHUB_PAT, ...)
                                                  |
                                                  v
                                   App reads os.environ / Pydantic
                                   (identical for plaintext fallback)
```

### Sequence Diagram (task startup, Secrets Manager path)

```
ECS control plane        ECS agent (host)         Secrets Manager        Container
      |                        |                        |                    |
      |-- run task ----------->|                        |                    |
      |                        |-- GetSecretValue(ARN)->|                    |
      |                        |   (task-exec role)     |                    |
      |                        |<-- secret value -------|                    |
      |                        |-- kms:Decrypt -------->| (KMS)              |
      |                        |-- start container, inject env vars -------->|
      |                        |                        |    process reads   |
      |                        |                        |    os.environ ---->|
```

### Component Diagram (toggle logic in the module)

```
var.use_secrets_manager_for_env (bool, default true)
        |
        v
 locals.tf: per-service split
   local.auth_secret_env       = flag ? [] : [ {name,value}, ... ]   # plaintext fallback list
   local.auth_secret_secrets   = flag ? [ {name,valueFrom}, ... ] : []
        |                                   |
        v                                   v
 ecs-services.tf                      ecs-services.tf
   environment = concat(              secrets = concat(
     [...non-secret...],                [...existing migrated secrets...],
     local.auth_secret_env)             local.auth_secret_secrets)
```

## Data Models

This change is Terraform infrastructure; there are no Python/Pydantic models. The relevant "data models" are the ECS `secrets` entry shape and the Secrets Manager resource shape.

### ECS secrets entry (existing shape, reused)

```hcl
# Raw single-value secret: reference the bare ARN
{ name = "GITHUB_PAT", valueFrom = aws_secretsmanager_secret.github_pat[0].arn }

# JSON secret (not used by new secrets here, shown for contrast):
{ name = "KC_DB_PASSWORD", valueFrom = "${aws_secretsmanager_secret.keycloak_db_secret.arn}:password::" }
```

### Secrets Manager resource (new, follows `secret_key` pattern)

```hcl
#checkov:skip=CKV2_AWS_57:External/app-managed secret, not rotatable via Secrets Manager
resource "aws_secretsmanager_secret" "github_pat" {
  count                   = var.github_pat != "" ? 1 : 0
  name_prefix             = "${local.name_prefix}-github-pat-"
  description             = "GitHub personal access token for the registry"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "github_pat" {
  count         = var.github_pat != "" ? 1 : 0
  secret_id     = aws_secretsmanager_secret.github_pat[0].id
  secret_string = var.github_pat
}
```

## API / CLI Design

No new HTTP endpoints or CLI commands. The only operator-facing surface is the new Terraform variable:

```hcl
# terraform.tfvars
use_secrets_manager_for_env = true   # false restores the legacy plaintext environment path
```

Applying is the standard workflow:

```bash
cd terraform/aws-ecs
terraform plan  -var-file=terraform.tfvars
terraform apply -var-file=terraform.tfvars
```

**Error cases:**
- If a variable name is left in both `environment` and `secrets` (implementation bug), ECS task registration fails with `Duplicate environment variable name`. The toggle design prevents this by construction.
- If the execution role lacks `GetSecretValue`/`kms:Decrypt` for a referenced ARN, the task fails to start with `ResourceInitializationError: unable to pull secrets`. Mitigated by adding every new ARN to `ecs_secrets_access` under the same conditional used to create it.

## Configuration Parameters

### New Terraform Variables

| Variable Name | Type | Default | Required | Description |
|---------------|------|---------|----------|-------------|
| `use_secrets_manager_for_env` | `bool` | `true` | No | When true, sensitive values are injected via the ECS `secrets` block from AWS Secrets Manager. When false, they are injected as plaintext `environment` entries (legacy fallback). |

All source secret variables (`grafana_admin_password`, `auth0_management_api_token`, `registry_api_token`, `registry_api_keys`, `federation_static_token`, `federation_encryption_key`, `ans_api_key`, `ans_api_secret`, `registration_webhook_auth_token`, `registration_gate_auth_credential`, `registration_gate_oauth2_client_secret`, `github_pat`, `github_app_private_key`) already exist in `modules/mcp-gateway/variables.tf` and are declared `sensitive = true`. No new source variables are needed.

### Variable definition (add to `modules/mcp-gateway/variables.tf`)

```hcl
variable "use_secrets_manager_for_env" {
  description = "Inject sensitive values via the ECS secrets block from AWS Secrets Manager (true) or as plaintext environment entries (false, legacy fallback)."
  type        = bool
  default     = true
}
```

The root stack must pass this through to the module. Add a matching root variable in `terraform/aws-ecs/variables.tf` and wire it in `main.tf` (`use_secrets_manager_for_env = var.use_secrets_manager_for_env`).

### Deployment Surface Checklist

- [ ] `terraform/aws-ecs/modules/mcp-gateway/variables.tf` (new module variable)
- [ ] `terraform/aws-ecs/variables.tf` (new root variable)
- [ ] `terraform/aws-ecs/main.tf` (pass-through into the module)
- [ ] `terraform/aws-ecs/terraform.tfvars.example` (documented default `true`)
- [ ] `terraform/aws-ecs/README.md` / `OPERATIONS.md` (document the flag and the fallback procedure)
- [ ] No Docker Compose / Helm surface (out of scope; document as N/A)

## New Dependencies

This change uses only existing dependencies (the AWS provider, `terraform-aws-modules/ecs`, and the already-present `aws_kms_key.secrets`). No new providers, modules, or Python packages are required.

## Implementation Details

### Step-by-Step Plan (for a future implementer)

The 13 target variables split into three groups by scope:

- **Grafana only:** `GF_SECURITY_ADMIN_PASSWORD` (grafana, grafana-config).
- **auth-server + registry:** `AUTH0_MANAGEMENT_API_TOKEN`, `REGISTRY_API_TOKEN`, `REGISTRY_API_KEYS`, `FEDERATION_STATIC_TOKEN`, `FEDERATION_ENCRYPTION_KEY`, `ANS_API_KEY`, `ANS_API_SECRET`.
- **registry only:** `REGISTRATION_WEBHOOK_AUTH_TOKEN`, `REGISTRATION_GATE_AUTH_CREDENTIAL`, `REGISTRATION_GATE_OAUTH2_CLIENT_SECRET`, `GITHUB_PAT`, `GITHUB_APP_PRIVATE_KEY`.

#### Step 1: Add the feature-flag variable

**Files:** `modules/mcp-gateway/variables.tf`, `terraform/aws-ecs/variables.tf`, `terraform/aws-ecs/main.tf`
See the variable definition above; wire the pass-through in `main.tf`.

#### Step 2: Create the Secrets Manager secrets

**File:** `modules/mcp-gateway/secrets.tf` (append a new section, ~13 secret+version pairs)

For each target variable, add a `count`-gated secret + version. All the auth/registry secrets are `count = var.<name> != "" ? 1 : 0`. Example for the auth0 management token:

```hcl
#checkov:skip=CKV2_AWS_57:Auth0 Management API token managed in the Auth0 dashboard, not rotatable via Secrets Manager
resource "aws_secretsmanager_secret" "auth0_management_api_token" {
  count                   = var.auth0_management_api_token != "" ? 1 : 0
  name_prefix             = "${local.name_prefix}-auth0-mgmt-api-token-"
  description             = "Auth0 Management API token"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "auth0_management_api_token" {
  count         = var.auth0_management_api_token != "" ? 1 : 0
  secret_id     = aws_secretsmanager_secret.auth0_management_api_token[0].id
  secret_string = var.auth0_management_api_token
}
```

Repeat for: `registry_api_token`, `registry_api_keys`, `federation_static_token`, `federation_encryption_key`, `ans_api_key`, `ans_api_secret`, `registration_webhook_auth_token`, `registration_gate_auth_credential`, `registration_gate_oauth2_client_secret`, `github_pat`, `github_app_private_key`.

**Grafana admin password (special handling).** `var.grafana_admin_password` defaults to `""` (`variables.tf:1175-1180`), and the grafana service only exists when `enable_observability` is true (`observability.tf:482`). Gate the secret on `enable_observability`, and never store an empty `secret_string`: when the operator leaves the password blank, generate one with `random_password` (mirroring `secret_key`/`metrics_api_key` at `secrets.tf:83-102`/`:330-355`) so grafana always gets a strong password and `apply` never fails on an empty value:

```hcl
resource "random_password" "grafana_admin_password" {
  count   = var.enable_observability && var.grafana_admin_password == "" ? 1 : 0
  length  = 32
  special = false
}

#checkov:skip=CKV2_AWS_57:Grafana admin password rotation requires coordinated restart
resource "aws_secretsmanager_secret" "grafana_admin_password" {
  count                   = var.enable_observability ? 1 : 0
  name_prefix             = "${local.name_prefix}-grafana-admin-password-"
  description             = "Grafana admin password"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "grafana_admin_password" {
  count     = var.enable_observability ? 1 : 0
  secret_id = aws_secretsmanager_secret.grafana_admin_password[0].id
  secret_string = var.grafana_admin_password != "" ? var.grafana_admin_password : random_password.grafana_admin_password[0].result
}
```

Note: if a random password is generated, operators retrieve it from Secrets Manager (or a Terraform output marked `sensitive`) to log in to Grafana. Document this in `OPERATIONS.md`.

#### Step 3: Build toggle helper locals

**File:** `modules/mcp-gateway/locals.tf` (new locals)

Define, per service, two lists: the plaintext-fallback `environment` fragment and the Secrets Manager `secrets` fragment. Each variable contributes to exactly one of the two, chosen by the flag. Gate each optional entry on the same non-empty condition used to create its secret so a missing secret is never referenced.

```hcl
locals {
  use_sm = var.use_secrets_manager_for_env

  # ---- auth-server + registry shared secrets ----
  shared_secret_specs = {
    AUTH0_MANAGEMENT_API_TOKEN = { value = var.auth0_management_api_token, arn = try(aws_secretsmanager_secret.auth0_management_api_token[0].arn, "") }
    REGISTRY_API_TOKEN         = { value = var.registry_api_token,         arn = try(aws_secretsmanager_secret.registry_api_token[0].arn, "") }
    REGISTRY_API_KEYS          = { value = var.registry_api_keys,          arn = try(aws_secretsmanager_secret.registry_api_keys[0].arn, "") }
    FEDERATION_STATIC_TOKEN    = { value = var.federation_static_token,    arn = try(aws_secretsmanager_secret.federation_static_token[0].arn, "") }
    FEDERATION_ENCRYPTION_KEY  = { value = var.federation_encryption_key,  arn = try(aws_secretsmanager_secret.federation_encryption_key[0].arn, "") }
    ANS_API_KEY                = { value = var.ans_api_key,                arn = try(aws_secretsmanager_secret.ans_api_key[0].arn, "") }
    ANS_API_SECRET             = { value = var.ans_api_secret,             arn = try(aws_secretsmanager_secret.ans_api_secret[0].arn, "") }
  }

  # Plaintext fallback: emitted only when the flag is OFF. Preserves prior behavior
  # (all names were previously always present, even when empty).
  shared_secret_env = local.use_sm ? [] : [
    for name, spec in local.shared_secret_specs : { name = name, value = spec.value }
  ]

  # Secrets Manager path: emitted only when the flag is ON and the secret exists.
  shared_secret_secrets = local.use_sm ? [
    for name, spec in local.shared_secret_specs : { name = name, valueFrom = spec.arn }
    if spec.arn != ""
  ] : []

  # ---- registry-only secrets (same construction) ----
  registry_only_specs = {
    REGISTRATION_WEBHOOK_AUTH_TOKEN        = { value = var.registration_webhook_auth_token,        arn = try(aws_secretsmanager_secret.registration_webhook_auth_token[0].arn, "") }
    REGISTRATION_GATE_AUTH_CREDENTIAL      = { value = var.registration_gate_auth_credential,      arn = try(aws_secretsmanager_secret.registration_gate_auth_credential[0].arn, "") }
    REGISTRATION_GATE_OAUTH2_CLIENT_SECRET = { value = var.registration_gate_oauth2_client_secret, arn = try(aws_secretsmanager_secret.registration_gate_oauth2_client_secret[0].arn, "") }
    GITHUB_PAT                             = { value = var.github_pat,                             arn = try(aws_secretsmanager_secret.github_pat[0].arn, "") }
    GITHUB_APP_PRIVATE_KEY                 = { value = var.github_app_private_key,                 arn = try(aws_secretsmanager_secret.github_app_private_key[0].arn, "") }
  }
  registry_only_env = local.use_sm ? [] : [
    for name, spec in local.registry_only_specs : { name = name, value = spec.value }
  ]
  registry_only_secrets = local.use_sm ? [
    for name, spec in local.registry_only_specs : { name = name, valueFrom = spec.arn } if spec.arn != ""
  ] : []

  # ---- grafana (secret is count-gated on enable_observability) ----
  grafana_secret_env = local.use_sm ? [] : [{ name = "GF_SECURITY_ADMIN_PASSWORD", value = var.grafana_admin_password }]
  grafana_secret_secrets = (local.use_sm && var.enable_observability) ? [
    { name = "GF_SECURITY_ADMIN_PASSWORD", valueFrom = aws_secretsmanager_secret.grafana_admin_password[0].arn }
  ] : []
}
```

Note on fallback fidelity: when `use_sm = false`, the plaintext lists reproduce the previous behavior. There is one intentional refinement: optional empty secrets are no longer created (Secrets Manager path), but the plaintext path still emits the (possibly empty) env entry to remain byte-for-byte identical to today. Because the previous code always emitted these names inline, the fallback list emits them unconditionally too.

#### Step 4: Remove the target vars from `environment` and add the fallback + secrets fragments

**File:** `modules/mcp-gateway/ecs-services.tf`

For the auth-server container (`environment = concat(...)` ending near `:410`):
- Delete the inline `{ name = "AUTH0_MANAGEMENT_API_TOKEN", value = ... }`, `REGISTRY_API_TOKEN`, `REGISTRY_API_KEYS`, `FEDERATION_STATIC_TOKEN`, `FEDERATION_ENCRYPTION_KEY`, `ANS_API_KEY`, `ANS_API_SECRET` blocks.
- Append `local.shared_secret_env` to the `environment = concat(...)`.
- Append `local.shared_secret_secrets` to the `secrets = concat(...)` at `:413-480`.

For the registry container (`environment` near `:670-1285`, `secrets` at `:1288-1365`):
- Delete the inline shared secrets (same 7 names) AND the 5 registry-only names.
- Append `local.shared_secret_env` and `local.registry_only_env` to `environment = concat(...)`.
- Append `local.shared_secret_secrets` and `local.registry_only_secrets` to `secrets = concat(...)`.

Example (registry `secrets` tail):

```hcl
      secrets = concat(
        [
          { name = "SECRET_KEY", valueFrom = aws_secretsmanager_secret.secret_key.arn },
          # ... existing migrated secrets ...
        ],
        # ... existing conditional idp/observability lists ...
        local.shared_secret_secrets,
        local.registry_only_secrets,
      )
```

#### Step 5: Add a `secrets` block to grafana / grafana-config

**File:** `modules/mcp-gateway/observability.tf`

- grafana container (`:528`): remove the inline `{ name = "GF_SECURITY_ADMIN_PASSWORD", value = var.grafana_admin_password }` from `environment`; add `secrets = local.grafana_secret_secrets` and append `local.grafana_secret_env` to `environment`.
- grafana-config container (`:616`): same treatment. The shell command at `:630` reads `$${GF_SECURITY_ADMIN_PASSWORD}` from the env, which is populated identically whether via `environment` or `secrets`.

#### Step 6: Extend the IAM policy `Resource` list

**File:** `modules/mcp-gateway/iam.tf` (`aws_iam_policy.ecs_secrets_access`, `Resource = concat(...)` at `:15-36`)

Append the new ARNs, gated by the same non-empty conditions used to create them (and grafana's unconditionally):

```hcl
Resource = concat(
  [
    aws_secretsmanager_secret.secret_key.arn,
    # ... existing base ARNs ...
  ],
  # ... existing conditional lists ...
  var.enable_observability                  ? [aws_secretsmanager_secret.grafana_admin_password[0].arn] : [],
  var.auth0_management_api_token            != "" ? [aws_secretsmanager_secret.auth0_management_api_token[0].arn] : [],
  var.registry_api_token                    != "" ? [aws_secretsmanager_secret.registry_api_token[0].arn] : [],
  var.registry_api_keys                     != "" ? [aws_secretsmanager_secret.registry_api_keys[0].arn] : [],
  var.federation_static_token               != "" ? [aws_secretsmanager_secret.federation_static_token[0].arn] : [],
  var.federation_encryption_key             != "" ? [aws_secretsmanager_secret.federation_encryption_key[0].arn] : [],
  var.ans_api_key                           != "" ? [aws_secretsmanager_secret.ans_api_key[0].arn] : [],
  var.ans_api_secret                        != "" ? [aws_secretsmanager_secret.ans_api_secret[0].arn] : [],
  var.registration_webhook_auth_token       != "" ? [aws_secretsmanager_secret.registration_webhook_auth_token[0].arn] : [],
  var.registration_gate_auth_credential     != "" ? [aws_secretsmanager_secret.registration_gate_auth_credential[0].arn] : [],
  var.registration_gate_oauth2_client_secret != "" ? [aws_secretsmanager_secret.registration_gate_oauth2_client_secret[0].arn] : [],
  var.github_pat                            != "" ? [aws_secretsmanager_secret.github_pat[0].arn] : [],
  var.github_app_private_key                != "" ? [aws_secretsmanager_secret.github_app_private_key[0].arn] : [],
)
```

**Avoiding triplication (recommended refinement).** The non-empty predicate is otherwise written in three places per secret: the secret `count`, the locals filter, and the IAM `Resource` list. To prevent drift (a forgotten IAM line yields a silent `unable to pull secrets` at task start), derive the IAM `Resource` additions from the same `local.*_specs` maps used in Step 3:

```hcl
# Instead of 13 hand-written conditional lines, one comprehension per group:
[for name, spec in local.shared_secret_specs   : spec.arn if spec.arn != ""],
[for name, spec in local.registry_only_specs   : spec.arn if spec.arn != ""],
var.enable_observability ? [aws_secretsmanager_secret.grafana_admin_password[0].arn] : [],
```

A future implementer may go further and drive the secret resources themselves via `for_each` over a single spec map (see Alternatives), collapsing all three usages to one source of truth. That diverges from the current one-resource-per-secret style in `secrets.tf`, so it is offered as an option, not mandated.

#### Step 7: Attach the secrets policy to the grafana execution role (REQUIRED, not optional)

**File:** `modules/mcp-gateway/observability.tf` (grafana `module "ecs_service_grafana"`, `task_exec_iam_role_policies` at `:505-513`)

Confirmed by inspection: the grafana service currently attaches ONLY `EcsExecTaskExecution` to its execution role and does NOT attach `ecs_secrets_access`:

```hcl
# observability.tf:505-513 (current)
create_task_exec_iam_role = true
task_exec_iam_role_policies = {
  EcsExecTaskExecution = aws_iam_policy.ecs_exec_task_execution.arn
}
```

The ECS agent uses the EXECUTION role (not the task role) to resolve `secrets[].valueFrom` at container init. Without the grant, every grafana task launch fails with `ResourceInitializationError: unable to pull secrets ... AccessDeniedException`. Add the attachment (execution role only; the task role does not need it, see the note below):

```hcl
task_exec_iam_role_policies = {
  EcsExecTaskExecution = aws_iam_policy.ecs_exec_task_execution.arn
  SecretsManagerAccess = aws_iam_policy.ecs_secrets_access.arn
}
```

Also confirm the grafana execution role name matches the KMS key policy's `role/*task-exec*` pattern (`secrets.tf:39`); the community ECS module names it `${name}-task-exec-*`, which matches (consistent with the already-working metrics-service).

**Least-privilege note (from security review):** the existing module services attach `ecs_secrets_access` to BOTH the execution role and the task role (`ecs-services.tf:51-64`). Only the execution role needs `GetSecretValue`/`kms:Decrypt`; the running application reads injected env vars and never calls the Secrets Manager API directly (confirmed - no boto3 Secrets Manager usage in runtime app code). Attaching the read policy to the task role widens the runtime blast radius (a compromised container could enumerate the whole secret catalog via the task-role credentials on the ECS metadata endpoint). For grafana, attach the policy to the execution role ONLY. Whether to also remove the task-role attachment from auth/registry is tracked as a follow-up hardening item (see Open Questions), since it is a change to an existing pattern rather than part of this migration.

### Error Handling

- **Missing IAM permission:** surfaces as an ECS `ResourceInitializationError` at task start. Prevented by Step 6/7.
- **Duplicate env name:** prevented by construction (a name is in exactly one of `environment`/`secrets` per the flag).
- **Empty optional secret:** never created (count-gated) and never referenced (the `secrets` fragment filters `if spec.arn != ""`).

### Logging

Terraform `plan`/`apply` output is the primary signal. No application logging changes. Operators should confirm via CloudTrail `GetSecretValue` events that the execution role reads each secret at task start (see Observability).

## Observability

### Tracing / Metrics / Logging Points

- **CloudTrail:** every `secretsmanager:GetSecretValue` is logged with the calling role, secret ARN, and timestamp; this is the audit trail the migration is meant to provide.
- **KMS:** `kms:Decrypt` events on `aws_kms_key.secrets` correlate with secret reads.
- **ECS:** task `stoppedReason` shows `ResourceInitializationError: unable to pull secrets or registry auth` if a permission or ARN is wrong.
- **CloudWatch alarms:** existing alarm stack (`cloudwatch-alarms.tf`) covers task health; no new alarms required, though an optional metric filter on failed secret pulls could be added later (out of scope).

## Scaling Considerations

- Secret reads occur only at task start/restart, not per request, so there is negligible steady-state load and no bottleneck. Secrets Manager `GetSecretValue` default quota (thousands/sec) far exceeds task-launch frequency.
- ECS caches the injected values as env vars for the container lifetime; a secret rotation requires a task restart to take effect (acceptable and consistent with the existing migrated secrets, which are marked non-rotatable).
- No caching layer is added; the existing model is retained.

## File Changes

### New Files

None. All changes extend existing files.

### Modified Files

| File Path | Lines | Change Description |
|-----------|-------|--------------------|
| `terraform/aws-ecs/modules/mcp-gateway/secrets.tf` | ~+90 | 13 secret + version resource pairs (12 count-gated, grafana unconditional) |
| `terraform/aws-ecs/modules/mcp-gateway/iam.tf` | ~+15 | Append 13 ARNs to `ecs_secrets_access` `Resource` concat list |
| `terraform/aws-ecs/modules/mcp-gateway/locals.tf` | ~+45 | Toggle helper locals for env/secrets fragments |
| `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf` | ~-40 / +8 | Remove inline plaintext entries; append fallback + secrets fragments (auth + registry) |
| `terraform/aws-ecs/modules/mcp-gateway/observability.tf` | ~-6 / +8 | grafana/grafana-config secrets block; confirm policy attachment |
| `terraform/aws-ecs/modules/mcp-gateway/variables.tf` | ~+6 | New `use_secrets_manager_for_env` variable |
| `terraform/aws-ecs/variables.tf` | ~+6 | Root pass-through variable |
| `terraform/aws-ecs/main.tf` | ~+1 | Pass flag into module |
| `terraform/aws-ecs/terraform.tfvars.example` | ~+2 | Document the flag |
| `terraform/aws-ecs/README.md` (or `OPERATIONS.md`) | ~+15 | Document the migration and fallback procedure |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| New Terraform (secrets + locals + IAM + vars) | ~160 |
| Modified/removed Terraform (task defs, pass-through) | ~60 |
| Docs | ~20 |
| Tests (terraform validate/plan checks, no app tests) | ~0 app / manual |
| **Total** | **~240** |

## Testing Strategy

See `./testing.md` for the full plan. Summary: `terraform validate` + `terraform plan` with the flag on and off (fallback fidelity), a task-definition inspection to confirm each var appears in exactly one block, an IAM policy check that every referenced ARN is granted, a live task-start smoke test verifying the app boots and reads each value, and backwards-compatibility verification that `use_secrets_manager_for_env = false` produces no net change from the pre-migration plan.

## Alternatives Considered

### Alternative 1: One combined JSON secret for all app secrets

**Description:** Store all 13 values as keys in a single `aws_secretsmanager_secret` and reference each with `":key::"`.
**Pros:** One resource, one ARN in IAM, fewer Terraform objects.
**Cons:** Coarse-grained access (any reader gets all values); a change to one value rewrites the whole secret version; diverges from the existing one-secret-per-value pattern already used for `secret_key`, `metrics_api_key`, etc.
**Why Rejected:** Breaks the established least-privilege, per-secret convention and reduces auditability granularity.

### Alternative 2: SSM Parameter Store (SecureString) instead of Secrets Manager

**Description:** Use SSM SecureString parameters, as Keycloak partially does.
**Pros:** Lower cost (no per-secret monthly charge).
**Cons:** The clarifying answers explicitly require AWS Secrets Manager (rotation support and cross-account access). Secrets Manager is already the module's standard for app secrets.
**Why Rejected:** Contradicts the stated requirement and fragments the pattern.

### Alternative 3: No fallback flag (hard cutover)

**Description:** Move everything to `secrets` unconditionally.
**Pros:** Simpler code, no toggle locals.
**Cons:** No safe rollback during migration; a Secrets Manager or IAM misconfiguration blocks all task starts with no quick escape.
**Why Rejected:** The requirements explicitly ask to keep the plaintext path as a fallback during migration.

### Alternative 4: `for_each` over a single secret-spec map

**Description:** Define one `local` map of `{ name_prefix, description, value, condition }` and drive `aws_secretsmanager_secret`/`_version`, the ECS `secrets` list, the plaintext fallback list, and the IAM `Resource` list all via comprehensions over that map.
**Pros:** One source of truth; adding a secret is a one-line map entry; no triplication of the non-empty predicate; IAM can never drift from what the containers reference.
**Cons:** Diverges from the current one-resource-per-secret style in `secrets.tf`; `for_each` keys must be known at plan time (fine here, keys are static var names); a slightly higher bar for an entry-level maintainer to read.
**Why not chosen as the primary path:** To stay consistent with the existing hand-written pattern and keep the diff reviewable against the established convention. Offered as the recommended refactor once the team is comfortable with 13 homogeneous secrets (this is the point where `for_each` earns its keep). The IAM-from-specs comprehension in Step 6 is a lighter-weight subset of this that is recommended regardless.

### Comparison Matrix

| Criteria | Chosen (per-secret + flag) | Alt 1 (combined JSON) | Alt 2 (SSM) | Alt 3 (no flag) |
|----------|----------------------------|-----------------------|-------------|-----------------|
| Complexity | Medium | Low | Medium | Low |
| Least privilege | High | Low | High | High |
| Matches existing pattern | Yes | No | Partial | Yes |
| Safe rollback | Yes | Yes | Yes | No |
| Meets stated requirement | Yes | Yes | No | Partial |

## Rollout Plan

- Phase 1: Implementation (out of scope for this skill) - add secrets, locals, IAM, toggle, task-def edits.
- Phase 2: Validation - `terraform validate`, `plan` with flag on/off, review the diff to confirm fallback fidelity.
- Phase 3: Staged deploy - apply with `use_secrets_manager_for_env = true` in a non-prod environment; verify tasks start and the app reads each value; check CloudTrail `GetSecretValue`.
- Phase 4: Production apply; keep `false` fallback documented for emergency rollback (flip flag, re-apply).
- Phase 5: After a bake period, consider removing the plaintext fallback in a later issue.

## Open Questions

- **Encrypted remote state backend.** The root stack has no `backend` block, so state (which holds `secret_string` in cleartext) defaults to a local file. Should configuring an S3 + SSE-KMS backend be a hard prerequisite PR before this migration, or handled separately? Recommendation: block the migration apply on encrypted state, since otherwise secrets merely move from the task-def JSON to the state file.
- **Task-role least privilege.** The existing `ecs_secrets_access` policy is attached to both the execution and task roles of auth/registry (`ecs-services.tf:51-64`). The running app never calls the Secrets Manager API directly, so the task-role attachment is unnecessary and widens blast radius. Should this migration also remove the task-role attachment (a change to an existing pattern), or is that a separate hardening issue? For the NEW grafana attachment, this design uses the execution role only.
- **KMS key-policy wildcard.** `aws_kms_key.secrets` grants `kms:Decrypt` to `Principal AWS "*"` gated on `aws:PrincipalArn` `StringLike` `role/*task-exec*` and `aws:PrincipalAccount` (`secrets.tf:26-41`). Any future in-account role whose name contains `task-exec` inherits decrypt. Should the key policy be tightened to explicit execution-role ARNs? (Pre-existing; flagged for follow-up.)
- **Insecure `SECRET_KEY` default.** `auth_server/providers/{okta,auth0,keycloak}.py` fall back to `os.environ.get("SECRET_KEY", "development-secret-key")`, a source-committed HS256 signing key (auth-bypass primitive if `SECRET_KEY` is ever unset). `SECRET_KEY` is already a first-tier Secrets Manager secret and always non-empty, so this migration does not activate the fallback, but it should be removed (fail-closed) as adjacent hardening. In scope to fix here or separate?
- `GITHUB_APP_PRIVATE_KEY` PEM newline fidelity: the app does `.replace("\\n", "\n")` (`github_auth.py:129`), so both multi-line and escaped forms work; still must be confirmed by an explicit smoke test that signs a GitHub App JWT (RS256) with the injected value.
- Should the `*_extra_env` passthrough variables (which can carry arbitrary user secrets) be linted/warned about, or left entirely to operator discretion? (Current design leaves them out of scope with a documented caveat.)
- Should the fallback flag be a single global boolean or per-service/per-group? A global flag means a rollback re-exposes all 13 values in state; per-group flags allow rolling back one misconfigured secret without re-exposing the rest.
- Should the fallback plaintext path be retained long-term or removed once migration is confirmed? Recommendation: file a tracked follow-up issue with a removal date, and add a Terraform precondition/warning when `use_secrets_manager_for_env = false`.

## References

- Existing migrated pattern: `terraform/aws-ecs/modules/mcp-gateway/secrets.tf`, `iam.tf`, `ecs-services.tf:413-480`.
- AWS docs: "Passing sensitive data to a container" (ECS `secrets`/`valueFrom`), "Retrieve secrets through environment variables" (`GetSecretValue` on the task execution role).
- Prior related PRs: #947 (MongoDB connection string secret), #1026 (Keycloak DB creds to Secrets Manager).
