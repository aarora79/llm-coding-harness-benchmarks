# Low-Level Design: Migrate Remaining ECS Plaintext Secrets to AWS Secrets Manager

*Created: 2026-07-23*
*Author: Claude*
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

## Overview

### Problem Statement
`terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf` already reads several credentials from AWS Secrets Manager via the ECS `secrets` block (`SECRET_KEY`, Keycloak client secrets, DocumentDB username/password, IdP client secrets for Entra/Okta/Auth0, the embeddings API key, the metrics API key). A second set of sensitive values, however, is still emitted as plaintext `environment` entries even though the corresponding Terraform variables are marked `sensitive = true`:

| Env var | Container(s) | Source variable |
|---|---|---|
| `AUTH0_MANAGEMENT_API_TOKEN` | auth-server, registry | `var.auth0_management_api_token` |
| `REGISTRY_API_TOKEN` | auth-server, registry | `var.registry_api_token` |
| `REGISTRY_API_KEYS` | auth-server, registry | `var.registry_api_keys` |
| `FEDERATION_STATIC_TOKEN` | auth-server, registry | `var.federation_static_token` |
| `FEDERATION_ENCRYPTION_KEY` | auth-server, registry | `var.federation_encryption_key` |
| `ANS_API_KEY` | auth-server, registry | `var.ans_api_key` |
| `ANS_API_SECRET` | auth-server, registry | `var.ans_api_secret` |
| `REGISTRATION_WEBHOOK_AUTH_TOKEN` | registry | `var.registration_webhook_auth_token` |
| `REGISTRATION_GATE_AUTH_CREDENTIAL` | registry | `var.registration_gate_auth_credential` |
| `REGISTRATION_GATE_OAUTH2_CLIENT_SECRET` | registry | `var.registration_gate_oauth2_client_secret` |
| `GITHUB_PAT` | registry | `var.github_pat` |
| `GITHUB_APP_PRIVATE_KEY` | registry | `var.github_app_private_key` |
| `GF_SECURITY_ADMIN_PASSWORD` | grafana, grafana-config | `var.grafana_admin_password` |

`sensitive = true` only redacts these values from `terraform plan`/`apply` CLI output and marks the state attribute as sensitive - it does not stop Terraform from writing the plaintext value into the ECS task definition's `environment` array, which is stored unencrypted and readable by anyone with `ecs:DescribeTaskDefinition` (or, at the container level, via ECS Exec / `/proc/<pid>/environ`). Moving these into the `secrets` block gives them the same encryption-at-rest (`aws_kms_key.secrets`), IAM-scoped read access, and CloudTrail audit trail that the already-migrated secrets have.

### Goals
- Every secret in the table above becomes readable from AWS Secrets Manager via the ECS `secrets` block.
- The existing plaintext variable and its `environment` entry keep working unchanged when no Secrets Manager ARN is supplied (fallback during migration, per the stated constraint).
- No changes to Python application code - the app continues to read the same environment variable name regardless of whether ECS resolved it from `secrets` or `environment`.
- IAM task execution role read access is scoped to exactly the secrets a given service needs, following the existing `ecs_secrets_access` policy pattern.

### Non-Goals
- **Rotation** for the 12 non-Grafana secrets, split into two distinct reasons rather than one blanket statement:
  - *No rotation protocol available*: `auth0_management_api_token`, `github_pat`, `github_app_private_key`, `ans_api_key`, `ans_api_secret` are issued by third-party systems (Auth0, GitHub, ANS) with no AWS-native rotation Lambda protocol - rotation for these is out of scope regardless of storage mechanism.
  - *Rotation deferred, not precluded*: `registry_api_token`, `registry_api_keys`, `federation_static_token`, `federation_encryption_key`, `registration_webhook_auth_token`, `registration_gate_auth_credential`, `registration_gate_oauth2_client_secret` are app-generated or app-defined values that could follow the existing `random_password` + Secrets Manager pattern already used for `secret_key`/`metrics_api_key` (see Alternative 1) if Terraform owned their generation. This design's `_secret_arn`-only mechanism does not preclude that - a follow-up could add Terraform-owned generation with rotation for this subset without changing the `secrets`/IAM wiring this LLD adds. Only DocumentDB and the Keycloak database have Lambda-based rotation today; that is unaffected by this change either way.
- **Cross-account access** is implicitly supported by the `_secret_arn` mechanism (any ARN, including one in another account with an appropriate resource policy and IAM trust relationship, can be supplied), but this design does not add, configure, or validate a specific cross-account resource-policy setup - no such pattern exists elsewhere in this codebase to model one on. Validating a concrete cross-account configuration is left as a follow-up.
- Touching the Keycloak service's task definition (`terraform/aws-ecs/keycloak-ecs.tf`) - it already sources every credential from Secrets Manager/SSM.
- Docker Compose or Helm chart changes - this is a Terraform/ECS-only migration per the stated constraints.
- Removing the plaintext env-var path - it must remain functional as a fallback.
- Any in-application (Python) Secrets Manager client code - ECS resolves `secrets` block `valueFrom` ARNs to plain environment variables before the container process starts, so `registry/core/config.py::Settings` and `auth_server`'s `os.environ.get` calls need no changes.

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `terraform/aws-ecs/modules/mcp-gateway/secrets.tf` | All `aws_secretsmanager_secret`/`_version` resources for app-tier services, plus the shared `aws_kms_key.secrets` | Where new secret resources are added |
| `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf` | `container_definitions` (`environment` and `secrets` lists) for `auth-server`, `registry`, and other services | Where plaintext `environment` entries move to `secrets` |
| `terraform/aws-ecs/modules/mcp-gateway/observability.tf` | `ecs_service_grafana` module, `GF_SECURITY_ADMIN_PASSWORD` plaintext env on both the `grafana` and `grafana-config` containers | Grafana admin password migration |
| `terraform/aws-ecs/modules/mcp-gateway/iam.tf` | `aws_iam_policy.ecs_secrets_access` - the single IAM policy granting `secretsmanager:GetSecretValue` to every service's task-exec and task role | New secret ARNs must be added to its `Resource` concat |
| `terraform/aws-ecs/modules/mcp-gateway/variables.tf` | Module-level variable declarations (mirror of root `variables.tf`) | New `*_secret_arn` variables added here |
| `terraform/aws-ecs/variables.tf` | Root-level variable declarations, including the existing plaintext variables and the `mongodb_connection_string` / `mongodb_connection_string_secret_arn` fallback pair (lines 439-450) | Reference pattern to replicate; new root variables added here |
| `terraform/aws-ecs/main.tf` | Wires every root variable into `module "mcp_gateway"` | New `*_secret_arn` variables must be passed through |
| `terraform/aws-ecs/terraform.tfvars.example` | Example tfvars documenting each plaintext variable, several already noting "RECOMMENDED: set via `TF_VAR_*` env var" | New `*_secret_arn` variables documented alongside |
| `registry/core/config.py` | Pydantic `Settings(BaseSettings)` - reads all app env vars by name (e.g. `registry_api_token: str = ""` binds to `REGISTRY_API_TOKEN`) | Confirms no code change needed - binds by env var name, source-agnostic |
| `auth_server/server.py` | Ad hoc `os.environ.get("REGISTRY_API_TOKEN", "")`, `os.environ.get("FEDERATION_STATIC_TOKEN", "")` | Confirms no code change needed |

### Existing Patterns Identified

1. **Secrets Manager resource pattern** (`secrets.tf`): every optional secret follows the same shape -
   ```hcl
   resource "aws_secretsmanager_secret" "okta_api_token" {
     count = var.okta_enabled ? 1 : 0
     name_prefix             = "${local.name_prefix}-okta-api-token-"
     description             = "..."
     recovery_window_in_days = 0
     kms_key_id              = aws_kms_key.secrets.id
     tags                    = local.common_tags
   }
   resource "aws_secretsmanager_secret_version" "okta_api_token" {
     count         = var.okta_enabled ? 1 : 0
     secret_id     = aws_secretsmanager_secret.okta_api_token[0].id
     secret_string = var.okta_api_token
     lifecycle { ignore_changes = [secret_string] }
   }
   ```
   A future implementer must follow this shape exactly for the new secrets, using `count` (or no `count` when the secret is unconditional) consistent with whether the underlying feature is optional.

2. **`secrets` block plaintext-or-ARN fallback pattern** (`ecs-services.tf` lines 399-408, 438-443, `variables.tf` lines 434-450) - this is the **direct precedent** for the fallback requirement in this issue:
   ```hcl
   # environment: emitted only when no secret ARN is provided
   var.mongodb_connection_string != "" && var.mongodb_connection_string_secret_arn == "" ? [
     { name = "MONGODB_CONNECTION_STRING", value = var.mongodb_connection_string }
   ] : [],
   ```
   ```hcl
   # secrets: emitted only when a secret ARN is provided
   var.mongodb_connection_string_secret_arn != "" ? [
     { name = "MONGODB_CONNECTION_STRING", valueFrom = var.mongodb_connection_string_secret_arn }
   ] : [],
   ```
   Files: `ecs-services.tf`, `variables.tf` (root and module). How a future implementer should follow this: for each secret in scope, add a `<name>_secret_arn` variable (default `""`), gate the plaintext `environment` entry on `<plaintext_var> != "" && <name>_secret_arn == ""`, and gate the `secrets` entry on `<name>_secret_arn != ""`.

3. **IAM policy `Resource` concat pattern** (`iam.tf` lines 15-36) - every conditionally-created secret ARN is appended to the same `concat()` list, guarded by the same condition used to create the secret:
   ```hcl
   Resource = concat(
     [ aws_secretsmanager_secret.secret_key.arn, ... ],
     var.entra_enabled ? [aws_secretsmanager_secret.entra_client_secret[0].arn] : [],
     ...
   )
   ```
   New secret ARNs (including ARNs supplied externally via `*_secret_arn` variables) must be added here, gated on the ARN variable being non-empty (an externally supplied ARN might reference a secret this Terraform config does not own, so IAM access must still be granted whenever the ARN is provided, exactly like `documentdb_credentials_secret_arn` already does at line 23).

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| `aws_kms_key.secrets` | Reused (no change) | All new `aws_secretsmanager_secret` resources encrypt with this existing key |
| `aws_iam_policy.ecs_secrets_access` | Extended | New secret ARNs (both self-created and externally supplied) appended to its `Resource` concat |
| `module.ecs_service_auth` / `module.ecs_service_registry` | Extended | `environment` entries removed/conditionalized, `secrets` entries added |
| `module.ecs_service_grafana` (observability.tf) | Extended | Same treatment for `GF_SECURITY_ADMIN_PASSWORD` on both `grafana` and `grafana-config` containers |
| `terraform/aws-ecs/main.tf` | Extended | New root `*_secret_arn` variables passed through to `module.mcp_gateway` |
| `registry/core/config.py`, `auth_server/*` | None | No change - env var names are unchanged, ECS resolves the value regardless of source |

### Constraints and Limitations Discovered

- **Backwards compatibility is mandatory.** Existing deployments have populated `terraform.tfvars` with plaintext values for these variables (e.g. `registry_api_token = "..."` per `terraform.tfvars.example` line 354). Any change that made the plaintext variable required-empty or removed it outright would break `terraform plan` for every existing user. The fallback design keeps every plaintext variable's default and behavior untouched.
- **`GF_SECURITY_ADMIN_PASSWORD` is consumed by two containers** in the same task (`grafana` and the non-essential `grafana-config` sidecar, which builds a Grafana API URL with embedded basic-auth credentials at container-entrypoint runtime: `GURL="http://admin:$${GF_SECURITY_ADMIN_PASSWORD}@localhost:3000"`). Both containers need the `secrets` entry (or both need the `environment` entry) - they cannot be split, since the sidecar's shell script directly interpolates the same env var name.
- **`aws_secretsmanager_secret` resources are always created** (not `count`-gated) for secrets tied to a currently-optional plaintext variable with default `""` (e.g. `registry_api_token`) only when a `_secret_arn` variable is not the mechanism - but since this design uses `_secret_arn` variables (externally-created secrets) rather than Terraform-created-and-populated secrets for parity with the plaintext fallback, **no new `aws_secretsmanager_secret` resources are required by this design** for the 12 non-Grafana secrets; see Alternatives Considered for why.
- **IdP-conditional secrets** (`AUTH0_MANAGEMENT_API_TOKEN`) already live inside `var.auth0_enabled` gating for other Auth0 secrets, but `AUTH0_MANAGEMENT_API_TOKEN` itself has no `auth0_enabled` gate in the current code (it's unconditionally emitted, just possibly empty) - the new `_secret_arn` variable and its `environment`/`secrets` split must preserve this unconditional-but-possibly-empty behavior, not add a new gate.

## Architecture

### System Context Diagram
```
                       terraform apply
                             |
                             v
        +----------------------------------------+
        |     terraform/aws-ecs (root module)     |
        |  variables.tf: <secret>_secret_arn (new)|
        |  variables.tf: <secret> (existing,       |
        |                 plaintext, unchanged)    |
        +--------------------+---------------------+
                             | passthrough (main.tf)
                             v
        +----------------------------------------+
        |  modules/mcp-gateway (child module)      |
        |  variables.tf: same new/existing vars    |
        |  ecs-services.tf: environment/secrets     |
        |    conditional emission                   |
        |  iam.tf: ecs_secrets_access Resource list |
        +--------------------+---------------------+
                             |
             +---------------+----------------+
             v                                v
   ARN empty -> environment            ARN set -> secrets block
   { name, value = plaintext var }     { name, valueFrom = ARN }
             |                                |
             v                                v
        ECS task definition (JSON)   ECS task definition (JSON)
        plaintext value in-line      valueFrom = ARN, resolved by
                                      ECS agent at container start
                                              |
                                              v
                                   AWS Secrets Manager GetSecretValue
                                   (IAM: ecs_secrets_access policy)
                                              |
                                              v
                             Container env var (same name either way)
                                              |
                                              v
                        registry/core/config.py::Settings /
                        auth_server os.environ.get(...) - unchanged
```

### Sequence Diagram (per-secret decision at plan/apply time)
```
Operator                Terraform                          ECS / Secrets Manager
   |                        |                                       |
   |--(sets tfvars)-------->|                                       |
   |                        |-- evaluate <name>_secret_arn ---------|
   |                        |                                       |
   |                        |-- if "" : emit environment{name,value}|
   |                        |-- if set: emit secrets{name,valueFrom}|
   |                        |                                       |
   |                        |-- register task definition ---------->|
   |                        |                                       |
   |                        |               ECS agent starts task   |
   |                        |               resolves valueFrom ARNs |
   |                        |               via GetSecretValue ----->|
   |                        |<-- secret value (decrypted via KMS) --|
   |                        |               injects as env var       |
   |                        |               container process starts |
```

### Component Diagram
```
secrets.tf ---------------------------+
  (no new resources required for      |
   the 12 externally-referenced       |
   secrets; unchanged)                |
                                       |
observability.tf (Grafana) -----------+---> iam.tf: ecs_secrets_access.Resource
  (GF_SECURITY_ADMIN_PASSWORD:        |        (append every populated
   emits secrets entry when           |         *_secret_arn, same pattern
   grafana_admin_password_secret_arn  |         as documentdb_credentials_secret_arn)
   is set)                            |
                                       |
ecs-services.tf (auth-server,         |
  registry) --------------------------+
  (12 secrets: environment/secrets
   split per <name>_secret_arn)
```

## Data Models

This change is pure Terraform HCL - no Pydantic models, no new Python data structures. The "data model" is the set of new Terraform variables, one per migrated secret:

```hcl
variable "<name>_secret_arn" {
  description = "Optional Secrets Manager ARN for <description of secret>. When set, takes precedence over the plaintext var.<name> and the value is injected via the ECS task definition's secrets block instead of environment."
  type        = string
  default     = ""
}
```

No `sensitive = true` on the `_secret_arn` variables (an ARN is not itself a secret - this mirrors `mongodb_connection_string_secret_arn`, `documentdb_credentials_secret_arn`, which are also not marked sensitive).

## API / CLI Design

Not applicable - this is a Terraform-only infrastructure change with no new CLI, HTTP endpoint, or user-facing interface. The "interface" is the set of new Terraform input variables described in Configuration Parameters below.

## Configuration Parameters

### New Terraform Variables (root: `terraform/aws-ecs/variables.tf`, and module: `terraform/aws-ecs/modules/mcp-gateway/variables.tf`)

| Variable Name | Type | Default | Required | Description |
|---|---|---|---|---|
| `auth0_management_api_token_secret_arn` | string | `""` | No | Secrets Manager ARN for the Auth0 Management API token |
| `registry_api_token_secret_arn` | string | `""` | No | Secrets Manager ARN for the static registry API token |
| `registry_api_keys_secret_arn` | string | `""` | No | Secrets Manager ARN for the registry API keys JSON blob |
| `federation_static_token_secret_arn` | string | `""` | No | Secrets Manager ARN for the federation static token |
| `federation_encryption_key_secret_arn` | string | `""` | No | Secrets Manager ARN for the Fernet federation encryption key |
| `ans_api_key_secret_arn` | string | `""` | No | Secrets Manager ARN for the ANS API key |
| `ans_api_secret_secret_arn` | string | `""` | No | Secrets Manager ARN for the ANS API secret |
| `registration_webhook_auth_token_secret_arn` | string | `""` | No | Secrets Manager ARN for the registration webhook auth token |
| `registration_gate_auth_credential_secret_arn` | string | `""` | No | Secrets Manager ARN for the registration gate auth credential |
| `registration_gate_oauth2_client_secret_secret_arn` | string | `""` | No | Secrets Manager ARN for the registration gate OAuth2 client secret |
| `github_pat_secret_arn` | string | `""` | No | Secrets Manager ARN for the GitHub PAT |
| `github_app_private_key_secret_arn` | string | `""` | No | Secrets Manager ARN for the GitHub App private key (PEM) |
| `grafana_admin_password_secret_arn` | string | `""` | No | Secrets Manager ARN for the Grafana admin password |

All 12 existing plaintext variables (`auth0_management_api_token`, `registry_api_token`, `registry_api_keys`, `federation_static_token`, `federation_encryption_key`, `ans_api_key`, `ans_api_secret`, `registration_webhook_auth_token`, `registration_gate_auth_credential`, `registration_gate_oauth2_client_secret`, `github_pat`, `github_app_private_key`) plus `grafana_admin_password` are **unchanged** - same name, type, default, `sensitive = true`.

### Settings / Config Class Updates
None. `registry/core/config.py::Settings` and `auth_server`'s env reads are unaffected - they consume the resolved environment variable regardless of whether ECS populated it from `secrets` or `environment`.

### Deployment Surface Checklist
- [ ] `terraform/aws-ecs/variables.tf` - add the 13 new `*_secret_arn` variables (root).
- [ ] `terraform/aws-ecs/modules/mcp-gateway/variables.tf` - add the 13 new `*_secret_arn` variables (module).
- [ ] `terraform/aws-ecs/main.tf` - pass all 13 new variables from root into `module.mcp_gateway`.
- [ ] `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf` - conditional `environment`/`secrets` emission for the 12 non-Grafana secrets on `auth-server` and `registry`.
- [ ] `terraform/aws-ecs/modules/mcp-gateway/observability.tf` - conditional `environment`/`secrets` emission for `GF_SECURITY_ADMIN_PASSWORD` on `grafana` and `grafana-config`.
- [ ] `terraform/aws-ecs/modules/mcp-gateway/iam.tf` - extend `aws_iam_policy.ecs_secrets_access`'s `Resource` concat with all 13 `*_secret_arn` variables (gated on non-empty).
- [ ] `terraform/aws-ecs/terraform.tfvars.example` - document each new `*_secret_arn` variable next to its plaintext counterpart.
- No Docker Compose, Helm chart, or Python application changes (out of scope, and unnecessary per the resolved-before-process-start behavior of the ECS `secrets` block).

## New Dependencies
This change uses only existing dependencies. No new Terraform providers, modules, or Python packages are required - `aws_kms_key.secrets` and the `terraform-aws-modules/ecs/aws//modules/service` module (which already supports the `secrets` argument on `container_definitions`) are already in use.

## Implementation Details

### Step-by-Step Plan (for a future implementer)

#### Step 1: Add the new `*_secret_arn` variables (root)
**File:** `terraform/aws-ecs/variables.tf`
**Lines:** immediately after each existing plaintext variable declaration (e.g. after `registry_api_token` at line 692, add `registry_api_token_secret_arn` at 693)

```hcl
variable "registry_api_token_secret_arn" {
  description = "Optional Secrets Manager ARN for the registry API token. When set, takes precedence over registry_api_token and the value is injected via the ECS task definition's secrets block instead of a plaintext environment entry."
  type        = string
  default     = ""
}
```
Repeat for all 13 secrets, placed directly below their plaintext counterpart so the pairing is easy for a reader to spot (matches the existing `mongodb_connection_string` / `mongodb_connection_string_secret_arn` adjacency at lines 439-450).

#### Step 2: Add the same variables to the module
**File:** `terraform/aws-ecs/modules/mcp-gateway/variables.tf`
Identical declarations, same placement convention, immediately below each plaintext counterpart.

#### Step 3: Wire root variables into the module call
**File:** `terraform/aws-ecs/main.tf`
Add 13 lines alongside the existing passthrough lines (e.g. near line 227-228 where `ans_api_key`/`ans_api_secret` are passed):
```hcl
  ans_api_key                        = var.ans_api_key
  ans_api_key_secret_arn             = var.ans_api_key_secret_arn
  ans_api_secret                     = var.ans_api_secret
  ans_api_secret_secret_arn          = var.ans_api_secret_secret_arn
```
Repeat for each secret pair.

#### Step 4: Split `environment` into conditional plaintext + `secrets` entries
**File:** `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf`
For `module.ecs_service_auth`, replace the unconditional environment entry, e.g. (existing, lines 273-280):
```hcl
{
  name  = "ANS_API_KEY"
  value = var.ans_api_key
},
{
  name  = "ANS_API_SECRET"
  value = var.ans_api_secret
},
```
with a conditional block moved out of the main `environment` list into the same `concat(...)` pattern already used for the MongoDB fallback (lines 399-408), e.g.:
```hcl
environment = concat([
  # ... unchanged non-secret entries ...
  ],
  var.ans_api_key != "" && var.ans_api_key_secret_arn == "" ? [
    { name = "ANS_API_KEY", value = var.ans_api_key }
  ] : [],
  var.ans_api_secret != "" && var.ans_api_secret_secret_arn == "" ? [
    { name = "ANS_API_SECRET", value = var.ans_api_secret }
  ] : [],
  # ... one such block per migrated secret ...
  var.mongodb_connection_string != "" && var.mongodb_connection_string_secret_arn == "" ? [
    { name = "MONGODB_CONNECTION_STRING", value = var.mongodb_connection_string }
  ] : [],
  var.auth_server_extra_env
)
```
and add matching entries to the `secrets` list (existing pattern at lines 413-480):
```hcl
secrets = concat(
  [ /* unchanged unconditional secrets */ ],
  var.ans_api_key_secret_arn != "" ? [
    { name = "ANS_API_KEY", valueFrom = var.ans_api_key_secret_arn }
  ] : [],
  var.ans_api_secret_secret_arn != "" ? [
    { name = "ANS_API_SECRET", valueFrom = var.ans_api_secret_secret_arn }
  ] : [],
  # ... one such block per migrated secret ...
)
```
Apply the identical transformation to `module.ecs_service_registry`'s `environment`/`secrets` lists for the secrets it also emits (`AUTH0_MANAGEMENT_API_TOKEN`, `REGISTRY_API_TOKEN`, `REGISTRY_API_KEYS`, `FEDERATION_STATIC_TOKEN`, `FEDERATION_ENCRYPTION_KEY`, `ANS_API_KEY`, `ANS_API_SECRET`, `REGISTRATION_WEBHOOK_AUTH_TOKEN`, `REGISTRATION_GATE_AUTH_CREDENTIAL`, `REGISTRATION_GATE_OAUTH2_CLIENT_SECRET`, `GITHUB_PAT`, `GITHUB_APP_PRIVATE_KEY`).

`GITHUB_APP_PRIVATE_KEY` deserves a callout: it is a multi-line PEM value. The `secrets` block `valueFrom` mechanism handles multi-line strings natively (Secrets Manager stores the raw string, ECS injects it verbatim as the env var value), so no additional escaping logic is needed beyond what `github_app_private_key` (plaintext) already requires today.

#### Step 5: Apply the same split to Grafana
**File:** `terraform/aws-ecs/modules/mcp-gateway/observability.tf`
Replace the unconditional entry on the `grafana` container (line 582):
```hcl
environment = concat([
  # ... unchanged non-secret entries ...
  ],
  var.grafana_admin_password != "" && var.grafana_admin_password_secret_arn == "" ? [
    { name = "GF_SECURITY_ADMIN_PASSWORD", value = var.grafana_admin_password }
  ] : []
)
secrets = var.grafana_admin_password_secret_arn != "" ? [
  { name = "GF_SECURITY_ADMIN_PASSWORD", valueFrom = var.grafana_admin_password_secret_arn }
] : []
```
and identically on the `grafana-config` sidecar (line 645), since its embedded shell script reads the same env var name (`$${GF_SECURITY_ADMIN_PASSWORD}`) - both containers must receive it via the same mechanism so the value is always present and identical.

#### Step 6: Extend IAM access
**File:** `terraform/aws-ecs/modules/mcp-gateway/iam.tf`
Extend `aws_iam_policy.ecs_secrets_access`'s `Resource` concat (lines 15-36) with one new conditional entry per `_secret_arn` variable, following the existing `documentdb_credentials_secret_arn` precedent at line 23:
```hcl
Resource = concat(
  [ /* unchanged unconditional ARNs */ ],
  var.documentdb_credentials_secret_arn != "" ? [var.documentdb_credentials_secret_arn] : [],
  var.ans_api_key_secret_arn != "" ? [var.ans_api_key_secret_arn] : [],
  var.ans_api_secret_secret_arn != "" ? [var.ans_api_secret_secret_arn] : [],
  var.auth0_management_api_token_secret_arn != "" ? [var.auth0_management_api_token_secret_arn] : [],
  var.registry_api_token_secret_arn != "" ? [var.registry_api_token_secret_arn] : [],
  var.registry_api_keys_secret_arn != "" ? [var.registry_api_keys_secret_arn] : [],
  var.federation_static_token_secret_arn != "" ? [var.federation_static_token_secret_arn] : [],
  var.federation_encryption_key_secret_arn != "" ? [var.federation_encryption_key_secret_arn] : [],
  var.registration_webhook_auth_token_secret_arn != "" ? [var.registration_webhook_auth_token_secret_arn] : [],
  var.registration_gate_auth_credential_secret_arn != "" ? [var.registration_gate_auth_credential_secret_arn] : [],
  var.registration_gate_oauth2_client_secret_secret_arn != "" ? [var.registration_gate_oauth2_client_secret_secret_arn] : [],
  var.github_pat_secret_arn != "" ? [var.github_pat_secret_arn] : [],
  var.github_app_private_key_secret_arn != "" ? [var.github_app_private_key_secret_arn] : [],
  var.grafana_admin_password_secret_arn != "" ? [var.grafana_admin_password_secret_arn] : [],
  # ... existing entra/okta/auth0/metrics/otlp conditionals unchanged ...
)
```
Since this same `aws_iam_policy.ecs_secrets_access` is attached to both `auth-server` and `registry` (and, for Grafana, a policy would need to be attached too - see note below), granting access here covers every service that needs it without per-service IAM policy duplication.

**Grafana IAM note:** `module.ecs_service_grafana` currently attaches only `EcsExecTaskExecution`/`EcsExecTask`/`GrafanaAMPAccess` to its task-exec/task roles - it does **not** attach `ecs_secrets_access` today (it has no secrets). Once `GF_SECURITY_ADMIN_PASSWORD` can come from Secrets Manager, add `SecretsManagerAccess = aws_iam_policy.ecs_secrets_access.arn` to `module.ecs_service_grafana`'s `task_exec_iam_role_policies` map (`observability.tf` line 506).

#### Step 7: Document in the example tfvars
**File:** `terraform/aws-ecs/terraform.tfvars.example`
Directly below each existing commented-out plaintext line, add a matching commented `*_secret_arn` line, e.g. (near line 353-354):
```hcl
# Generate with: python3 -c "import secrets; print(secrets.token_urlsafe(32))"
# Can also be set via environment variable:
#   export TF_VAR_registry_api_token="your-generated-token"
registry_api_token = "m3zT65wREARMVDToKosg_DgNkKqS_434hNxy3sslGPY"
# RECOMMENDED for production: store the token in Secrets Manager and reference
# it here instead of the plaintext value above. When set, this takes
# precedence and registry_api_token is ignored.
# registry_api_token_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:registry-api-token-AbCdEf"
```

### Error Handling
No new runtime error handling is introduced - all logic is Terraform conditional expressions evaluated at plan time. The only failure mode to guard against is a `_secret_arn` variable pointing at a secret without the expected structure (e.g. a JSON secret when `valueFrom` alone is used, vs. a `valueFrom` needing a `:jsonkey::` suffix). Since every one of the 13 secrets in scope is a single scalar string (or a PEM blob, or a JSON string used as a single opaque value by the app, e.g. `REGISTRY_API_KEYS`), no `:jsonkey::` suffix is needed on any `valueFrom` in this design - each `_secret_arn` variable is expected to point directly at a Secrets Manager secret whose value is the exact string the corresponding env var should receive. This should be stated explicitly in each variable's `description` to avoid an operator storing a JSON wrapper object unnecessarily (contrast with `documentdb_credentials_secret_arn`, which does need `:username::`/`:password::` because it is a single JSON secret shared by two env vars).

### Logging
No application logging changes. At the infrastructure level, `terraform plan`/`apply` output already redacts these values (via `sensitive = true` on the plaintext variables); the new `_secret_arn` variables are not sensitive and will show in plan output as ARNs, which is fine (ARNs are not secrets).

## Observability
No new metrics or traces. Existing AWS-side observability suffices: CloudTrail records every `secretsmanager:GetSecretValue` call made by the task execution role when ECS resolves a `secrets` block entry, giving an audit trail for each of the 13 secrets that did not exist while they were plaintext `environment` entries.

## Scaling Considerations
Not applicable - this change does not affect running services' request/response path, throughput, or resource usage. ECS resolves `secrets` block entries once per task launch (not per request), so there is no added latency to steady-state traffic; task launch time may increase negligibly (each `secretsmanager:GetSecretValue` call adds low-double-digit milliseconds during container bootstrap, already incurred today for the secrets already migrated).

## File Changes

### New Files
None.

### Modified Files

| File Path | Lines | Change Description |
|-----------|-------|--------------------|
| `terraform/aws-ecs/variables.tf` | +65 (13 new variable blocks, ~5 lines each) | Add 13 `*_secret_arn` root variables |
| `terraform/aws-ecs/modules/mcp-gateway/variables.tf` | +65 | Add 13 `*_secret_arn` module variables |
| `terraform/aws-ecs/main.tf` | +13 | Pass new variables into `module.mcp_gateway` |
| `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf` | ~90 (removing 12 unconditional environment entries from 2 services, adding 24 conditional environment blocks and 24 conditional secrets blocks) | Conditional environment/secrets split for auth-server and registry |
| `terraform/aws-ecs/modules/mcp-gateway/observability.tf` | ~20 | Conditional environment/secrets split for grafana + grafana-config; add `SecretsManagerAccess` to Grafana's task-exec role policies |
| `terraform/aws-ecs/modules/mcp-gateway/iam.tf` | +13 | Extend `ecs_secrets_access` Resource concat |
| `terraform/aws-ecs/terraform.tfvars.example` | +40 (13 new documentation blocks) | Document new variables |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| New code (HCL) | ~260 |
| New tests | ~0 (no automated Terraform test suite exists in this repo today; see testing.md for manual verification plan) |
| Modified code | ~40 (removed/replaced environment entries) |
| **Total** | **~300** |

## Testing Strategy
See `./testing.md` for the full plan: `terraform validate`/`plan` backwards-compatibility checks with unmodified tfvars, `terraform plan` with `*_secret_arn` variables populated, IAM policy diff verification, and a deployment-surface checklist confirming the task definition JSON contains `secrets` (not `environment`) entries once an ARN is supplied.

## Alternatives Considered

### Alternative 1: Terraform-managed secrets (populate the secret value directly, like `okta_client_secret`)
**Description:** Instead of a `_secret_arn` variable pointing at an externally-managed secret, create `aws_secretsmanager_secret` + `aws_secretsmanager_secret_version` resources in `secrets.tf` whose `secret_string` is set directly from the existing plaintext variable (e.g. `secret_string = var.registry_api_token`), unconditionally, and always reference that Terraform-owned secret from the `secrets` block.
**Pros:** No new variable needed; matches the `entra_client_secret`/`okta_client_secret` pattern exactly; one fewer moving part.
**Cons:** Cannot satisfy the "keep the plaintext env-var path as a fallback during migration" requirement cleanly - the plaintext variable would still need to exist for the `secret_string` source, meaning the value is still typed into `terraform.tfvars` in plaintext either way (Secrets Manager just becomes a pass-through store, not a genuine reduction in where the plaintext lives - it still transits Terraform state and CLI history). It also does not support operators who already have these values in an externally-managed Secrets Manager secret (e.g. created by a separate secrets-rotation pipeline) without importing that secret into Terraform state.
**Why Rejected:** The `_secret_arn` pattern (Alternative chosen) is the one the repo's own PR #947 precedent already established for exactly this kind of "let the operator choose where the secret lives" tradeoff, and it is the only approach that lets an operator move a value into Secrets Manager without ever putting it in `terraform.tfvars` in plaintext, which is the actual security goal.

### Alternative 2: Runtime (in-Python) Secrets Manager fallback
**Description:** Add a `boto3.client("secretsmanager")` call inside `registry/core/config.py::Settings` (or a shared helper) that, when an env var is unset, fetches the value from a named or ARN'd secret at process startup.
**Pros:** Would work identically across ECS, Docker Compose, and any future non-ECS deployment target without relying on the ECS `secrets` block.
**Cons:** Adds a new runtime dependency on IAM permissions and network access to Secrets Manager from inside the application process (currently the app has zero direct AWS SDK usage outside of the DocumentDB IAM-auth code path); duplicates functionality ECS already provides for free; requires either a synchronous blocking call during a fail-fast module-level `Settings()` singleton instantiation (line ~1209 in `config.py`) or nontrivial refactoring to defer that instantiation; and does not match the stated scope, which explicitly calls this a Terraform/ECS task.
**Why Rejected:** Out of scope per the task description ("Terraform/ECS setup in this repo... plus app config-loader changes" refers to the config loader needing *no* changes, confirmed by the codebase analysis showing ECS already resolves secrets before the process starts - not that this design should add a Python-side fetch path). Terraform-side `secrets` block resolution is strictly simpler and already proven in this codebase.

### Comparison Matrix

| Criteria | Chosen (`_secret_arn` fallback) | Alt 1 (Terraform-managed) | Alt 2 (runtime fetch) |
|----------|--------|-------|-------|
| Complexity | Low | Low | Medium-High |
| Backwards compatible with existing tfvars | Yes | Yes (but doesn't remove plaintext exposure) | Yes |
| Removes plaintext value from tfvars/state for new deployments | Yes | No | Yes |
| Requires app code changes | No | No | Yes |
| Matches existing repo precedent | Yes (PR #947) | Partially (entra/okta pattern) | No |

## Rollout Plan
- Phase 1: Implementation (out of scope for this skill) - add the 13 variables, update `ecs-services.tf`/`observability.tf`/`iam.tf`, update `terraform.tfvars.example`.
- Phase 2: Testing - run the backwards-compatibility and new-ARN test plans in `testing.md` against a non-production AWS account/workspace.
- Phase 3: Deployment - existing deployments require no tfvars changes to keep working (plaintext path). Operators who want the Secrets Manager path create the secret manually (or via a separate Terraform config/module) and set the corresponding `*_secret_arn` variable, then `terraform apply`; ECS performs a rolling task replacement to pick up the change.

## Open Questions
- Should a follow-up issue add Terraform-managed (Alternative 1 style) secret creation as an opt-in convenience for operators who don't want to manage the Secrets Manager resource themselves, in addition to the `_secret_arn` fallback added here? Not needed for this issue's acceptance criteria, but worth tracking.
- Should `GITHUB_APP_PRIVATE_KEY` get a dedicated warning in `terraform.tfvars.example` about multi-line PEM handling in Secrets Manager (e.g. storing with literal `\n` vs. actual newlines)? Recommended but not blocking.

## References
- PR #947 - introduced the `mongodb_connection_string` / `mongodb_connection_string_secret_arn` fallback pattern this design replicates.
- Issue #955 - `is_aws_documentdb` conditional-resource gating pattern referenced for consistency.
- Issue #1026 - Keycloak DB credentials sourced from Secrets Manager via the rotation Lambda, the closest existing "full loop" reference implementation in this repo.
- Reference issue #1134 (this task).
