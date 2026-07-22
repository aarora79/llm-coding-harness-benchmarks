# Low-Level Design: Migrate ECS Environment Variables to AWS Secrets Manager

*Created: 2026-07-22*
*Author: Claude*
*Status: Draft*

## Table of Contents

1. [Overview](#overview)
2. [Codebase Analysis](#codebase-analysis)
3. [Architecture](#architecture)
4. [Data Models](#data-models)
5. [Configuration Parameters](#configuration-parameters)
6. [New Dependencies](#new-dependencies)
7. [Implementation Details](#implementation-details)
8. [Observability](#observability)
9. [Scaling Considerations](#scaling-considerations)
10. [File Changes](#file-changes)
11. [Testing Strategy](#testing-strategy)
12. [Alternatives Considered](#alternatives-considered)
13. [Rollout Plan](#rollout-plan)
14. [Open Questions](#open-questions)

## Overview

### Problem Statement

The MCP Gateway Registry deploys five ECS service groups via Terraform in `terraform/aws-ecs/modules/mcp-gateway/`: auth-server, registry, mcpgw, demo servers (currenttime, realserverfaketools, flight-booking-agent, travel-assistant-agent), and observability (grafana, metrics-service). Sensitive values in these services are passed as plaintext in the ECS `environment` blocks.

While Terraform variables marked `sensitive = true` are hidden from `terraform output`, they are still visible in:
- ECS task definition JSON (returned by `aws ecs describe-task-definition`)
- ECS task/service describe API responses
- `terraform plan` diffs (unless callers explicitly filter)
- Terraform state files

### Goals

- Create `aws_secretsmanager_secret` resources for all sensitive variables that do not yet have one (13 new secrets).
- Add ECS `secrets` block entries for ALL sensitive variables (existing and new).
- Keep plaintext env-var as a conditional fallback for zero-downtime migration.
- Update IAM policy to grant access to all new secrets, with cross-account access support via KMS grants.
- Add a central `enable_secrets_manager` toggle to enable/disable the migration.
- Add per-secret `sensitive_rotation` parameter for secrets that support automated rotation.
- Mark missing `sensitive = true` attributes on variables (already complete in v1.24.4).

### Non-Goals

- Docker Compose migration.
- Helm chart changes.
- Automatic rotation for IdP-managed secrets.
- Keycloak ECS deployment migration.
- Application code changes (the app reads env vars regardless of source).

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `terraform/aws-ecs/modules/mcp-gateway/secrets.tf` | Creates KMS key, random passwords, Secrets Manager secrets | Needs new secret resources for variables that lack them |
| `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf` | ECS container definitions (~2258 lines) | Primary target: add `secrets` entries, add conditional fallbacks to `environment`, remove plaintext values |
| `terraform/aws-ecs/modules/mcp-gateway/variables.tf` | Module input variables (~1430 lines) | Add `sensitive = true` where missing; add new variables |
| `terraform/aws-ecs/modules/mcp-gateway/iam.tf` | IAM policies for ECS tasks | Update `ecs_secrets_access` with new secret ARNs and cross-account grants |
| `terraform/aws-ecs/modules/mcp-gateway/observability.tf` | Grafana and metrics-service ECS services | Update grafana to read GF_SECURITY_ADMIN_PASSWORD from Secrets Manager |
| `terraform/aws-ecs/modules/mcp-gateway/locals.tf` | Local variables | Add `shared_secrets` local to reduce duplication between auth-server and registry |
| `terraform/aws-ecs/keycloak-ecs.tf` | Keycloak ECS service | Already uses Secrets Manager for KC DB credentials per issue #1026; out of scope |
| `registry/core/config.py` | Pydantic Settings config loader | Read-only; confirms the app reads env vars by name only |

### Existing Patterns Identified

1. **Secrets Manager resource pattern (secrets.tf)**:
   - Each secret: `aws_secretsmanager_secret` + `aws_secretsmanager_secret_version` pair
   - Auto-generated: `random_password` -> `secret_string`
   - User-provided: `var.<name>` as `secret_string` with `lifecycle { ignore_changes = [secret_string] }`
   - All encrypted with `kms_key_id = aws_kms_key.secrets.id`
   - Conditional creation: `count = var.<feature>_enabled ? 1 : 0`

2. **ECS `secrets` block pattern (ecs-services.tf)**:
   - Single value: `{ name = "ENV_VAR", valueFrom = aws_secretsmanager_secret.<name>.arn }`
   - JSON nested: `{ name = "ENV_VAR", valueFrom = "${arn}:field::" }`
   - Conditional: `var.<feature>_enabled ? [{...}] : []`
   - Built with `secrets = concat([...], conditional1, conditional2, ...)`

3. **Conditional plaintext fallback pattern**:
   - When removing a secret from `environment`, add a conditional entry:
   - `var.<name> != "" ? [{ name = "ENV_VAR", value = var.<name> }] : []`

4. **IAM policy pattern (iam.tf)**:
   - Single `aws_iam_policy.ecs_secrets_access` with `concat()` + conditional expressions
   - KMS decrypt on `aws_kms_key.secrets.arn`

5. **Config loader pattern (registry/core/config.py)**:
   - Pydantic `BaseSettings` reads env vars by name (e.g., `secret_key`, `documentdb_password`).
   - ECS injects secrets into the same env var namespace regardless of source.
   - No application code change needed.

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| secrets.tf + ecs-services.tf | Creates + consumes | Secrets created in one file, consumed via ARN in the other |
| iam.tf | Access control | Policy must include ARNs of all secrets ECS tasks need |
| variables.tf | Input interface | All sensitive variables marked `sensitive = true`; new toggle variables |
| locals.tf | Shared resources | Common secrets list extracted for dedup between services |
| observability.tf | Grafana + metrics-service | Grafana needs GF_SECURITY_ADMIN_PASSWORD moved to secrets |

### Constraints and Limitations

- All secrets must use the existing KMS key (`aws_kms_key.secrets.id`).
- IAM policy `ecs_secrets_access` must explicitly list each secret ARN (no wildcards).
- Docker Compose cannot use Secrets Manager natively.
- The plaintext fallback must use `var.<name>` which is already declared and `sensitive = true`.
- ECS secrets are injected at the task level, shared by all containers in the same task.
- Rotation Lambda functions are a separate concern; this design only adds configuration scaffolding for future rotation.

## Architecture

### System Context Diagram

```
+-------------------+       +---------------------------+       +------------------+
| Terraform Config  | ----> | AWS Secrets Manager       | ----> | ECS Task Defs    |
| (variables.tf)    |       | (secrets.tf creates)      |       | (ecs-services.tf)|
|                   |       |                           |       |                  |
| sensitive=true    |       | Encrypted at rest (KMS)   |       | secrets: block   |
| input vars        |       | Auto-rotation possible    |       | + fallback env   |
| enable_secrets    |       | Cross-account via grants  |       |                  |
| manager toggle    |       +---------------------------+       +------------------+
| per-secret rotation|                                        |
+-------------------+       +---------------------------+       +------------------+
                              AWS KMS Key (secrets)        |
                              + cross-account grants       v
                              + key rotation               ECS Task Runtime
                              + IAM grant on key           (container sees
                                                 plaintext ENV only at launch)
```

### Flow Diagram

```
User provides var.xxx (terraform.tfvars or CLI, marked sensitive=true)
         |
         v
    +--------------------------+
    | enable_secrets_manager ? |
    +--------------------------+
         | YES
    +--------------------------+
    | sensitive_rotation ?     |
    +--------------------------+
         | YES
    +-----------------------------------+
    | aws_secretsmanager_secret +       |
    | aws_secretsmanager_secret_version |
    | (encrypted by KMS key)            |
    +-----------------------------------+
         |
         v
    KMS encryption (aws_kms_key.secrets.id)
    + cross-account grant condition
         |
         v
    ECS task definition:
      secrets block -> ECS injects value from SM
      environment block -> fallback value from var.xxx (conditional)
         |
         v
    ECS task launch: Secrets Manager API call
    (IAM role must have secretsmanager:GetSecretValue)
         |
         v
    Value injected as ENV var in container
    (ECS injects secrets before container start)
         |
         v
    Application reads os.environ["ENV_VAR"]
    (no code change needed)
```

## Data Models

### New Secrets Manager Resources

The following 13 secrets need new `aws_secretsmanager_secret` + `aws_secretsmanager_secret_version` resources. These do NOT yet have a resource in the existing `secrets.tf`:

```hcl
# =============================================================================
# REGISTRY API TOKEN
# =============================================================================
resource "aws_secretsmanager_secret" "registry_api_token" {
  name_prefix             = "${local.name_prefix}-registry-api-token-"
  description             = "Static API key for Registry API (IdP-independent access)"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "registry_api_token" {
  secret_id = aws_secretsmanager_secret.registry_api_token.id
  secret_string = var.registry_api_token

  lifecycle {
    ignore_changes = [secret_string]
  }
}

# =============================================================================
# REGISTRY API KEYS (JSON)
# =============================================================================
resource "aws_secretsmanager_secret" "registry_api_keys" {
  name_prefix             = "${local.name_prefix}-registry-api-keys-"
  description             = "Multi-key static tokens JSON for Registry API"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "registry_api_keys" {
  secret_id = aws_secretsmanager_secret.registry_api_keys.id
  secret_string = var.registry_api_keys

  lifecycle {
    ignore_changes = [secret_string]
  }
}

# =============================================================================
# FEDERATION STATIC TOKEN
# =============================================================================
resource "aws_secretsmanager_secret" "federation_static_token" {
  name_prefix             = "${local.name_prefix}-federation-static-token-"
  description             = "Static token for Federation API peer registry access"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "federation_static_token" {
  secret_id = aws_secretsmanager_secret.federation_static_token.id
  secret_string = var.federation_static_token

  lifecycle {
    ignore_changes = [secret_string]
  }
}

# =============================================================================
# FEDERATION ENCRYPTION KEY (Fernet)
# =============================================================================
resource "aws_secretsmanager_secret" "federation_encryption_key" {
  name_prefix             = "${local.name_prefix}-federation-encryption-key-"
  description             = "Fernet encryption key for federation tokens in MongoDB"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "federation_encryption_key" {
  secret_id = aws_secretsmanager_secret.federation_encryption_key.id
  secret_string = var.federation_encryption_key

  lifecycle {
    ignore_changes = [secret_string]
  }
}

# =============================================================================
# ANS API KEY
# =============================================================================
resource "aws_secretsmanager_secret" "ans_api_key" {
  name_prefix             = "${local.name_prefix}-ans-api-key-"
  description             = "GoDaddy ANS API key for agent identity verification"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "ans_api_key" {
  secret_id = aws_secretsmanager_secret.ans_api_key.id
  secret_string = var.ans_api_key

  lifecycle {
    ignore_changes = [secret_string]
  }
}

# =============================================================================
# ANS API SECRET
# =============================================================================
resource "aws_secretsmanager_secret" "ans_api_secret" {
  name_prefix             = "${local.name_prefix}-ans-api-secret-"
  description             = "GoDaddy ANS API secret for agent identity verification"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "ans_api_secret" {
  secret_id = aws_secretsmanager_secret.ans_api_secret.id
  secret_string = var.ans_api_secret

  lifecycle {
    ignore_changes = [secret_string]
  }
}

# =============================================================================
# GITHUB PAT
# =============================================================================
resource "aws_secretsmanager_secret" "github_pat" {
  name_prefix             = "${local.name_prefix}-github-pat-"
  description             = "GitHub Personal Access Token for private repo SKILL.md access"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "github_pat" {
  secret_id = aws_secretsmanager_secret.github_pat.id
  secret_string = var.github_pat

  lifecycle {
    ignore_changes = [secret_string]
  }
}

# =============================================================================
# GITHUB APP PRIVATE KEY
# =============================================================================
resource "aws_secretsmanager_secret" "github_app_private_key" {
  name_prefix             = "${local.name_prefix}-github-app-private-key-"
  description             = "GitHub App private key (PEM format)"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "github_app_private_key" {
  secret_id = aws_secretsmanager_secret.github_app_private_key.id
  secret_string = var.github_app_private_key

  lifecycle {
    ignore_changes = [secret_string]
  }
}

# =============================================================================
# REGISTRATION WEBHOOK AUTH TOKEN
# =============================================================================
resource "aws_secretsmanager_secret" "registration_webhook_auth_token" {
  name_prefix             = "${local.name_prefix}-registration-webhook-auth-token-"
  description             = "Auth token for registration webhook requests (Issue #742)"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "registration_webhook_auth_token" {
  secret_id = aws_secretsmanager_secret.registration_webhook_auth_token.id
  secret_string = var.registration_webhook_auth_token

  lifecycle {
    ignore_changes = [secret_string]
  }
}

# =============================================================================
# REGISTRATION GATE AUTH CREDENTIAL
# =============================================================================
resource "aws_secretsmanager_secret" "registration_gate_auth_credential" {
  name_prefix             = "${local.name_prefix}-registration-gate-auth-"
  description             = "Auth credential for registration gate endpoint (Issue #809)"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "registration_gate_auth_credential" {
  secret_id = aws_secretsmanager_secret.registration_gate_auth_credential.id
  secret_string = var.registration_gate_auth_credential

  lifecycle {
    ignore_changes = [secret_string]
  }
}

# =============================================================================
# REGISTRATION GATE OAUTH2 CLIENT SECRET
# =============================================================================
resource "aws_secretsmanager_secret" "registration_gate_oauth2_client_secret" {
  name_prefix             = "${local.name_prefix}-registration-gate-oauth2-secret-"
  description             = "OAuth2 client secret for registration gate client credentials flow"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "registration_gate_oauth2_client_secret" {
  secret_id = aws_secretsmanager_secret.registration_gate_oauth2_client_secret.id
  secret_string = var.registration_gate_oauth2_client_secret

  lifecycle {
    ignore_changes = [secret_string]
  }
}

# =============================================================================
# AUTH0 MANAGEMENT API TOKEN
# =============================================================================
resource "aws_secretsmanager_secret" "auth0_management_api_token" {
  name_prefix             = "${local.name_prefix}-auth0-mgmt-api-token-"
  description             = "Auth0 Management API token (alternative to M2M credentials, expires after 24h)"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "auth0_management_api_token" {
  secret_id = aws_secretsmanager_secret.auth0_management_api_token.id
  secret_string = var.auth0_management_api_token

  lifecycle {
    ignore_changes = [secret_string]
  }
}

# =============================================================================
# GRAFANA ADMIN PASSWORD
# =============================================================================
resource "random_password" "grafana_admin_password" {
  length           = 32
  special          = true
  override_special = "!#$%&*()-_=+[]{}|:,.<>?"
}

resource "aws_secretsmanager_secret" "grafana_admin_password" {
  name_prefix             = "${local.name_prefix}-grafana-admin-password-"
  description             = "Admin password for Grafana (GF_SECURITY_ADMIN_PASSWORD)"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "grafana_admin_password" {
  secret_id = aws_secretsmanager_secret.grafana_admin_password.id
  secret_string = var.grafana_admin_password != "" ? var.grafana_admin_password : random_password.grafana_admin_password.result

  lifecycle {
    ignore_changes = [secret_string]
  }
}
```

### Secrets Already Existing in secrets.tf (no new resource needed)

These already have `aws_secretsmanager_secret` resources:

| Secret Resource | ECS Variable(s) |
|-----------------|----------------|
| `secret_key` | SECRET_KEY |
| `keycloak_client_secret` | KEYCLOAK_CLIENT_SECRET |
| `keycloak_m2m_client_secret` | KEYCLOAK_M2M_CLIENT_SECRET |
| `keycloak_admin_password` | KEYCLOAK_ADMIN_PASSWORD |
| `embeddings_api_key` | EMBEDDINGS_API_KEY |
| `okta_client_secret` | OKTA_CLIENT_SECRET |
| `okta_m2m_client_secret` | OKTA_M2M_CLIENT_SECRET |
| `okta_api_token` | OKTA_API_TOKEN |
| `entra_client_secret` | ENTRA_CLIENT_SECRET |
| `auth0_client_secret` | AUTH0_CLIENT_SECRET |
| `auth0_m2m_client_secret` | AUTH0_M2M_CLIENT_SECRET |
| `metrics_api_key` | METRICS_API_KEY |
| `otlp_exporter_headers` | OTEL_EXPORTER_OTLP_HEADERS |

## Configuration Parameters

### New Terraform Variables

The following variables must be added to `variables.tf` and passed through `main.tf` to the `mcp_gateway` module:

**Central toggle (add near the top of variables.tf):**
```hcl
variable "enable_secrets_manager" {
  description = "Enable loading secrets from AWS Secrets Manager. When false, all services fall back to plaintext environment variables. Set to false for initial migration phase, then true once all operators have migrated."
  type        = bool
  default     = true
}
```

**Per-secret rotation toggle (add after the existing secret-related variables):**
```hcl
variable "secret_rotation_enabled" {
  description = "Enable automated secret rotation for supported secrets (documentdb, grafana admin password). IdP-managed secrets (okta, auth0, entra, keycloak) are managed externally and cannot be rotated via Secrets Manager."
  type        = bool
  default     = false
}
```

### Variable Sensitivity Checklist

All sensitive variables are already marked `sensitive = true` in `variables.tf`. No changes needed.

| Variable | Current `sensitive` | Action |
|----------|---------------------|--------|
| `auth0_management_api_token` | Yes (line ~703) | No change |
| `registry_api_token` | Yes (line ~718) | No change |
| `registry_api_keys` | Yes (line ~724) | No change |
| `federation_static_token` | Yes (line ~905) | No change |
| `federation_encryption_key` | Yes (line ~913) | No change |
| `registration_webhook_auth_token` | Yes (line ~752) | No change |
| `registration_gate_auth_credential` | Yes (line ~833) | No change |
| `registration_gate_oauth2_client_secret` | Yes (line ~850) | No change |
| `ans_api_key` | Yes (line ~929) | No change |
| `ans_api_secret` | Yes (line ~937) | No change |
| `github_pat` | Yes (line ~1305) | No change |
| `github_app_private_key` | Yes (line ~1324) | No change |
| `grafana_admin_password` | Yes (line ~1218) | No change |
| All Okta/Auth0/Entra secrets | Yes | No change |

### Deployment Surface Checklist

| Surface | Status |
|---------|--------|
| Terraform ECS (module) | Change required |
| Terraform ECS (root) | Pass-through of `enable_secrets_manager` and `secret_rotation_enabled` |
| Docker Compose | Out of scope |
| Helm charts | No change |

## New Dependencies

No new dependencies. This change uses only existing AWS provider resources (aws_secretsmanager_secret, aws_secretsmanager_secret_version, random_password).

## Implementation Details

### Step-by-Step Plan

#### Step 1: Add new variables to `variables.tf`

**File:** `terraform/aws-ecs/modules/mcp-gateway/variables.tf`
**Lines:** ~1315 (append after `mcpgw_extra_env`)

Add the two new toggle variables:
- `enable_secrets_manager` (bool, default `true`)
- `secret_rotation_enabled` (bool, default `false`)

Also add a `shared_secrets` local to `locals.tf`:

**File:** `terraform/aws-ecs/modules/mcp-gateway/locals.tf`
**Lines:** Append at end of existing locals block

```hcl
locals {
  # ... existing locals ...

  # Shared secrets used by both auth-server and registry services.
  # Each entry is an ECS secrets block tuple.
  shared_secrets = var.enable_secrets_manager ? [
    { name = "REGISTRY_API_TOKEN", valueFrom = aws_secretsmanager_secret.registry_api_token.arn },
    { name = "REGISTRY_API_KEYS", valueFrom = aws_secretsmanager_secret.registry_api_keys.arn },
    { name = "FEDERATION_STATIC_TOKEN", valueFrom = aws_secretsmanager_secret.federation_static_token.arn },
    { name = "FEDERATION_ENCRYPTION_KEY", valueFrom = aws_secretsmanager_secret.federation_encryption_key.arn },
    { name = "ANS_API_KEY", valueFrom = aws_secretsmanager_secret.ans_api_key.arn },
    { name = "ANS_API_SECRET", valueFrom = aws_secretsmanager_secret.ans_api_secret.arn },
  ] : []
}
```

#### Step 2: Add New Secrets Manager Resources to `secrets.tf`

Append the new secret resources (listed in Data Models section above) to the end of `secrets.tf`, after the existing `otlp_exporter_headers` resource. All 13 new secrets plus the `random_password.grafana_admin_password`.

#### Step 3: Update Auth Server ECS Service (`ecs-services.tf`)

**File:** `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf`
**Lines:** ~97-480 (auth-server container definition)

**In the `secrets` block** (currently lines 413-480), add the shared secrets and conditional entries:

```hcl
secrets = concat(
  [
    { name = "SECRET_KEY", valueFrom = aws_secretsmanager_secret.secret_key.arn },
    { name = "KEYCLOAK_CLIENT_SECRET", valueFrom = "${aws_secretsmanager_secret.keycloak_client_secret.arn}:client_secret::" },
    { name = "KEYCLOAK_M2M_CLIENT_SECRET", valueFrom = "${aws_secretsmanager_secret.keycloak_m2m_client_secret.arn}:client_secret::" },
    { name = "DOCUMENTDB_USERNAME", valueFrom = "${var.documentdb_credentials_secret_arn}:username::" },
    { name = "DOCUMENTDB_PASSWORD", valueFrom = "${var.documentdb_credentials_secret_arn}:password::" },
  ],
  # MongoDB connection string override (Secrets Manager variant)
  var.mongodb_connection_string_secret_arn != "" && var.enable_secrets_manager ? [
    { name = "MONGODB_CONNECTION_STRING", valueFrom = var.mongodb_connection_string_secret_arn }
  ] : [],
  # Okta secrets (conditional)
  var.okta_enabled && var.enable_secrets_manager ? [
    { name = "OKTA_CLIENT_SECRET", valueFrom = aws_secretsmanager_secret.okta_client_secret[0].arn },
    { name = "OKTA_M2M_CLIENT_SECRET", valueFrom = aws_secretsmanager_secret.okta_m2m_client_secret[0].arn },
    { name = "OKTA_API_TOKEN", valueFrom = aws_secretsmanager_secret.okta_api_token[0].arn },
  ] : [],
  # Auth0 secrets (conditional)
  var.auth0_enabled && var.enable_secrets_manager ? [
    { name = "AUTH0_CLIENT_SECRET", valueFrom = aws_secretsmanager_secret.auth0_client_secret[0].arn },
    { name = "AUTH0_M2M_CLIENT_SECRET", valueFrom = aws_secretsmanager_secret.auth0_m2m_client_secret[0].arn },
    { name = "AUTH0_MANAGEMENT_API_TOKEN", valueFrom = aws_secretsmanager_secret.auth0_management_api_token.arn },
  ] : [],
  # Entra secret (conditional)
  var.entra_enabled && var.enable_secrets_manager ? [
    { name = "ENTRA_CLIENT_SECRET", valueFrom = aws_secretsmanager_secret.entra_client_secret[0].arn },
  ] : [],
  # Shared secrets (registry API, federation, ANS)
  local.shared_secrets,
  # Observability (conditional)
  var.enable_observability && var.enable_secrets_manager ? [
    { name = "METRICS_API_KEY", valueFrom = aws_secretsmanager_secret.metrics_api_key[0].arn },
  ] : []
)
```

**In the `environment` block**, add conditional fallbacks and remove plaintext values for:

| Variable | Lines | Action |
|----------|-------|--------|
| `OKTA_CLIENT_SECRET` | ~181 | Remove; add fallback: `var.okta_enabled && var.okta_client_secret != "" && !var.enable_secrets_manager ? [{ name = "OKTA_CLIENT_SECRET", value = var.okta_client_secret }] : []` |
| `OKTA_M2M_CLIENT_SECRET` | ~186 | Same pattern |
| `OKTA_API_TOKEN` | ~239 | Same pattern |
| `AUTH0_MANAGEMENT_API_TOKEN` | ~213 | Same pattern with `var.auth0_enabled` |
| `ENTRA_CLIENT_SECRET` | N/A (already in secrets block) | Same pattern with `var.entra_enabled` |
| `REGISTRY_API_TOKEN` | ~237 | Add fallback |
| `REGISTRY_API_KEYS` | ~241 | Add fallback |
| `FEDERATION_STATIC_TOKEN` | ~259 | Add fallback |
| `FEDERATION_ENCRYPTION_KEY` | ~263 | Add fallback |
| `ANS_API_KEY` | ~275 | Add fallback |
| `ANS_API_SECRET` | ~279 | Add fallback |

The existing Okta, Auth0, and Entra `client_id` fields are NOT secrets and should remain in `environment`. Only the `_secret` fields move to `secrets`.

#### Step 4: Update Registry ECS Service (`ecs-services.tf`)

**File:** `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf`
**Lines:** ~698-1365 (registry container definition)

**In the `secrets` block** (currently lines 1288-1365), add shared secrets and all missing secret entries:

```hcl
secrets = concat(
  [
    { name = "SECRET_KEY", valueFrom = aws_secretsmanager_secret.secret_key.arn },
    { name = "KEYCLOAK_CLIENT_SECRET", valueFrom = "${aws_secretsmanager_secret.keycloak_client_secret.arn}:client_secret::" },
    { name = "KEYCLOAK_M2M_CLIENT_SECRET", valueFrom = "${aws_secretsmanager_secret.keycloak_m2m_client_secret.arn}:client_secret::" },
    { name = "KEYCLOAK_ADMIN_PASSWORD", valueFrom = aws_secretsmanager_secret.keycloak_admin_password.arn },
    { name = "EMBEDDINGS_API_KEY", valueFrom = aws_secretsmanager_secret.embeddings_api_key.arn },
  ],
  var.mongodb_connection_string_secret_arn != "" && var.enable_secrets_manager ? [
    { name = "MONGODB_CONNECTION_STRING", valueFrom = var.mongodb_connection_string_secret_arn }
  ] : [],
  var.storage_backend == "documentdb" && var.enable_secrets_manager ? [
    { name = "DOCUMENTDB_USERNAME", valueFrom = "${var.documentdb_credentials_secret_arn}:username::" },
    { name = "DOCUMENTDB_PASSWORD", valueFrom = "${var.documentdb_credentials_secret_arn}:password::" },
  ] : [],
  var.okta_enabled && var.enable_secrets_manager ? [
    { name = "OKTA_CLIENT_SECRET", valueFrom = aws_secretsmanager_secret.okta_client_secret[0].arn },
    { name = "OKTA_M2M_CLIENT_SECRET", valueFrom = aws_secretsmanager_secret.okta_m2m_client_secret[0].arn },
    { name = "OKTA_API_TOKEN", valueFrom = aws_secretsmanager_secret.okta_api_token[0].arn },
  ] : [],
  var.auth0_enabled && var.enable_secrets_manager ? [
    { name = "AUTH0_CLIENT_SECRET", valueFrom = aws_secretsmanager_secret.auth0_client_secret[0].arn },
    { name = "AUTH0_M2M_CLIENT_SECRET", valueFrom = aws_secretsmanager_secret.auth0_m2m_client_secret[0].arn },
    { name = "AUTH0_MANAGEMENT_API_TOKEN", valueFrom = aws_secretsmanager_secret.auth0_management_api_token.arn },
  ] : [],
  var.entra_enabled && var.enable_secrets_manager ? [
    { name = "ENTRA_CLIENT_SECRET", valueFrom = aws_secretsmanager_secret.entra_client_secret[0].arn },
  ] : [],
  local.shared_secrets,
  # Registration secrets
  var.enable_secrets_manager ? [
    { name = "REGISTRATION_WEBHOOK_AUTH_TOKEN", valueFrom = aws_secretsmanager_secret.registration_webhook_auth_token.arn },
    { name = "REGISTRATION_GATE_AUTH_CREDENTIAL", valueFrom = aws_secretsmanager_secret.registration_gate_auth_credential.arn },
    { name = "REGISTRATION_GATE_OAUTH2_CLIENT_SECRET", valueFrom = aws_secretsmanager_secret.registration_gate_oauth2_client_secret.arn },
  ] : [],
  # GitHub secrets
  var.enable_secrets_manager ? [
    { name = "GITHUB_PAT", valueFrom = aws_secretsmanager_secret.github_pat.arn },
    { name = "GITHUB_APP_PRIVATE_KEY", valueFrom = aws_secretsmanager_secret.github_app_private_key.arn },
  ] : [],
  # Observability (conditional)
  var.enable_observability && var.enable_secrets_manager ? [
    { name = "METRICS_API_KEY", valueFrom = aws_secretsmanager_secret.metrics_api_key[0].arn },
  ] : []
)
```

Remove from `environment` (or replace with conditional fallback):

| Variable | Lines | Action |
|----------|-------|--------|
| `AUTH0_MANAGEMENT_API_TOKEN` | ~814 | Add fallback |
| `FEDERATION_STATIC_TOKEN` | ~952 | Add fallback |
| `FEDERATION_ENCRYPTION_KEY` | ~956 | Add fallback |
| `ANS_API_KEY` | ~973 | Add fallback |
| `ANS_API_SECRET` | ~977 | Add fallback |
| `REGISTRY_API_TOKEN` | ~1080 | Add fallback |
| `REGISTRY_API_KEYS` | ~1084 | Add fallback |
| `REGISTRATION_WEBHOOK_AUTH_TOKEN` | ~1106 | Add fallback |
| `REGISTRATION_GATE_AUTH_CREDENTIAL` | ~1160 | Add fallback |
| `REGISTRATION_GATE_OAUTH2_CLIENT_SECRET` | ~1184 | Add fallback |
| `GITHUB_PAT` | ~1251 | Add fallback |
| `GITHUB_APP_PRIVATE_KEY` | ~1263 | Add fallback |

Also remove secret `_secret` fields from Okta, Auth0, and Entra sections (lines ~768-800). The `_client_id` fields remain.

#### Step 5: Update Grafana in observability.tf

**File:** `terraform/aws-ecs/modules/mcp-gateway/observability.tf`

Replace the plaintext `GF_SECURITY_ADMIN_PASSWORD` environment variable with a `secrets` block entry. Since ECS injects secrets at the task level (shared by all containers in the task), the main `grafana` container's `secrets` block is sufficient; the grafana-config sidecar inherits the env var automatically.

Remove from environment:
```hcl
{ name = "GF_SECURITY_ADMIN_PASSWORD", value = var.grafana_admin_password }
```

Add to secrets:
```hcl
var.enable_secrets_manager && var.grafana_admin_password != "" ? [
  { name = "GF_SECURITY_ADMIN_PASSWORD", valueFrom = aws_secretsmanager_secret.grafana_admin_password.arn }
] : [],
```

Add a conditional fallback to environment:
```hcl
var.grafana_admin_password != "" && !var.enable_secrets_manager ? [
  { name = "GF_SECURITY_ADMIN_PASSWORD", value = var.grafana_admin_password }
] : [],
```

#### Step 6: Update IAM Policy in `iam.tf`

**File:** `terraform/aws-ecs/modules/mcp-gateway/iam.tf`
**Lines:** ~15-36

Add new secret ARNs to the `ecs_secrets_access` policy Resource list. Also add cross-account grant condition to the KMS key policy in `secrets.tf`:

```hcl
resource "aws_iam_policy" "ecs_secrets_access" {
  name_prefix = "${local.name_prefix}-ecs-secrets-"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        Resource = concat(
          [
            aws_secretsmanager_secret.secret_key.arn,
            aws_secretsmanager_secret.keycloak_client_secret.arn,
            aws_secretsmanager_secret.keycloak_m2m_client_secret.arn,
            aws_secretsmanager_secret.embeddings_api_key.arn,
            aws_secretsmanager_secret.keycloak_admin_password.arn,
            # NEW: secrets without existing resources
            aws_secretsmanager_secret.auth0_management_api_token.arn,
            aws_secretsmanager_secret.registry_api_token.arn,
            aws_secretsmanager_secret.registry_api_keys.arn,
            aws_secretsmanager_secret.federation_static_token.arn,
            aws_secretsmanager_secret.federation_encryption_key.arn,
            aws_secretsmanager_secret.registration_webhook_auth_token.arn,
            aws_secretsmanager_secret.registration_gate_auth_credential.arn,
            aws_secretsmanager_secret.registration_gate_oauth2_client_secret.arn,
            aws_secretsmanager_secret.ans_api_key.arn,
            aws_secretsmanager_secret.ans_api_secret.arn,
            aws_secretsmanager_secret.github_pat.arn,
            aws_secretsmanager_secret.github_app_private_key.arn,
            aws_secretsmanager_secret.grafana_admin_password.arn,
          ],
          var.documentdb_credentials_secret_arn != "" ? [var.documentdb_credentials_secret_arn] : [],
          var.okta_enabled ? [
            aws_secretsmanager_secret.okta_client_secret[0].arn,
            aws_secretsmanager_secret.okta_m2m_client_secret[0].arn,
            aws_secretsmanager_secret.okta_api_token[0].arn,
          ] : [],
          var.auth0_enabled ? [
            aws_secretsmanager_secret.auth0_client_secret[0].arn,
            aws_secretsmanager_secret.auth0_m2m_client_secret[0].arn,
            aws_secretsmanager_secret.auth0_management_api_token.arn,
          ] : [],
          var.entra_enabled ? [aws_secretsmanager_secret.entra_client_secret[0].arn] : [],
          var.enable_observability ? [aws_secretsmanager_secret.metrics_api_key[0].arn] : [],
          var.enable_observability && var.otel_otlp_endpoint != "" ? [aws_secretsmanager_secret.otlp_exporter_headers[0].arn] : []
        )
      },
      {
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:DescribeKey"
        ]
        Resource = [aws_kms_key.secrets.arn]
      }
    ]
  })

  tags = local.common_tags
}
```

#### Step 7: Add Cross-Account KMS Grant in `secrets.tf`

To support cross-account secret access, add a KMS grant that allows external accounts to decrypt using the shared key. This goes in `secrets.tf` after the existing KMS key policy:

```hcl
# Cross-account KMS grant (optional, only when target_account_id is set)
resource "aws_kms_grant" "cross_account" {
  count        = var.kms_cross_account_principals != "" ? 1 : 0
  key_id       = aws_kms_key.secrets.id
  constraints {
    encryption_context_equals = var.kms_grant_context
  }
  principals = [
    for account in split(",", var.kms_cross_account_principals) :
    "arn:aws:iam::${account}:root"
  ]
  operations = ["Decrypt", "DescribeKey"]
}
```

#### Step 8: Pass New Variables Through Root `main.tf`

**File:** `terraform/aws-ecs/main.tf`
**Lines:** ~300-304 (after existing module variable pass-throughs)

Add the new variables to the `module "mcp_gateway"` block:
- `enable_secrets_manager`
- `secret_rotation_enabled`

Also add corresponding variables to `terraform/aws-ecs/variables.tf` (pass-through with same defaults) and to `terraform/aws-ecs/modules/mcp-gateway/variables.tf` (actual definition).

### Rotation Configuration (Scaffolding)

The following variables support rotation configuration. These are configuration-only; the actual rotation Lambda functions are a separate effort.

```hcl
# Rotation configuration for supported secrets
variable "secret_rotation_enabled" {
  description = "Enable automated secret rotation for supported secrets. IdP-managed secrets (okta, auth0, entra, keycloak) are managed externally."
  type        = bool
  default     = false
}

variable "secret_rotation_schedule_expression" {
  description = "CloudWatch Events schedule expression for secret rotation (e.g., 'rate(90 days)'). Only used when secret_rotation_enabled is true."
  type        = string
  default     = "rate(90 days)"
}
```

### Error Handling

- Terraform will fail at `plan` time if any `secrets` block entry references a non-existent secret ARN.
- AWS ECS will reject task launches if the task role lacks `secretsmanager:GetSecretValue` on the secret. CloudWatch will show `AccessDeniedException`.
- Use `terraform validate` before `terraform plan`.
- Use `terraform plan -detailed-exitcode` to detect drift.

## Observability

- **CloudTrail**: Each `secretsmanager:GetSecretValue` generates a CloudTrail event with `requestParameters.secretId` and `userIdentity.arn`.
- **ECS Task Events**: `Container started` events confirm successful launch.
- **KMS Key Metrics**: `Decrypt` calls tracked in CloudWatch.
- **Secrets Manager**: API call metrics available in CloudWatch.
- **Recommended**: Enable CloudTrail Data Events for the Secrets Manager KMS key to audit all secret access (SOC2/PCI compliance).

## Scaling Considerations

- **Secrets Manager API limits**: 5000 RPS per account. With ~25 secrets per task and ~4 services, this is ~100 calls per task set. Well within limits.
- **IAM policy size**: Will grow to ~4 KB with all ARNs. Under the 6144-byte soft limit but leaves limited headroom. Consider splitting into multiple policies if more secrets are added.
- **ECS task definition size**: Max 16384 bytes. Adding ~30 secrets per service is well within limits.

## File Changes

### New Resources (secrets.tf)

| Resource | Description |
|----------|-------------|
| `aws_secretsmanager_secret.registry_api_token` + version | Registry API token |
| `aws_secretsmanager_secret.registry_api_keys` + version | Registry API keys JSON |
| `aws_secretsmanager_secret.federation_static_token` + version | Federation token |
| `aws_secretsmanager_secret.federation_encryption_key` + version | Fernet key |
| `aws_secretsmanager_secret.ans_api_key` + version | ANS API key |
| `aws_secretsmanager_secret.ans_api_secret` + version | ANS API secret |
| `aws_secretsmanager_secret.github_pat` + version | GitHub PAT |
| `aws_secretsmanager_secret.github_app_private_key` + version | GitHub App private key |
| `aws_secretsmanager_secret.registration_webhook_auth_token` + version | Webhook auth token |
| `aws_secretsmanager_secret.registration_gate_auth_credential` + version | Gate auth credential |
| `aws_secretsmanager_secret.registration_gate_oauth2_client_secret` + version | Gate OAuth2 secret |
| `aws_secretsmanager_secret.auth0_management_api_token` + version | Auth0 Management API token |
| `aws_secretsmanager_secret.grafana_admin_password` + version | Grafana admin password |
| `random_password.grafana_admin_password` | Auto-generated default password |
| `aws_kms_grant.cross_account` | Cross-account KMS grant (optional) |
| `aws_secretsmanager_rotation_lambda.*` | Rotation Lambda scaffolding (optional) |

### Modified Files

| File Path | Lines | Change Description |
|-----------|-------|--------------------|
| `secrets.tf` | ~250+ | Append new secret resources, rotation scaffolding, cross-account grant |
| `ecs-services.tf` | ~413-480, ~1288-1365 | Add shared_secrets, conditional fallbacks, enable_secrets_manager gate, remove plaintext |
| `locals.tf` | ~10 | Add `shared_secrets` local |
| `observability.tf` | ~580-650 | Move GF_SECURITY_ADMIN_PASSWORD to secrets |
| `iam.tf` | ~15-36 | Add ~14 new ARNs to IAM policy |
| `variables.tf` (module) | ~15 | Add enable_secrets_manager, secret_rotation_enabled |
| `variables.tf` (root) | ~10 | Pass-through new variables |
| `main.tf` (root) | ~5 | Pass new variables to module |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| New secrets.tf resources | ~250 |
| New variables.tf entries | ~15 |
| Modified ecs-services.tf | ~+30 net (add fallbacks, remove plaintext) |
| Modified locals.tf | ~10 |
| Modified observability.tf | ~+5 net |
| Modified iam.tf | ~+14 |
| Modified root main.tf + variables.tf | ~15 |
| **Total** | **~340** |

## Testing Strategy

See `testing.md` for the complete testing plan.

## Alternatives Considered

### Alternative 1: Use AWS SSM Parameter Store (SecureString)

**Pros:** Simpler API for single-value parameters.
**Cons:** No built-in rotation, different IAM permissions, Secrets Manager already used extensively.
**Why Rejected:** Secrets Manager is the established pattern with existing KMS integration and rotation support.

### Alternative 2: Store All Secrets in a Single JSON Secret

**Pros:** Fewer Secrets Manager entries.
**Cons:** Cannot rotate individual secrets, all-or-nothing access, harder to audit per-secret access.
**Why Rejected:** Per-secret pattern enables independent rotation and granular access control.

### Alternative 3: Use External Secrets Operator

**Pros:** Kubernetes-native, supports multiple backends.
**Cons:** Requires Kubernetes/EKS; only ECS/terraform is in scope.
**Why Rejected:** EKS is not in scope for this deployment.

## Rollout Plan

### Phase 1: Terraform Plan Verification
- Run `terraform plan` to verify no unexpected resource changes.
- Verify all new secrets appear as "will be created".
- Verify IAM policy grows as expected.

### Phase 2: Staging Deployment
- Deploy to staging with `enable_secrets_manager = false` (plaintext fallback only).
- Apply the Terraform changes (creates secrets, updates IAM).
- Set `enable_secrets_manager = true` and re-apply.
- Verify ECS tasks launch and health checks pass.
- Verify CloudWatch Logs show no errors.

### Phase 3: Production Rollout
- Deploy in stages to production.
- Monitor CloudWatch Metrics.
- Verify CloudTrail shows successful `GetSecretValue` calls.

### Phase 4: Remove Plaintext Fallback
- After all operators have migrated, remove the conditional plaintext fallback from `environment` blocks.
- Set `enable_secrets_manager` default to `true` permanently.
- This is a separate follow-up change.

## Open Questions

1. Should `grafana_admin_password` use `random_password` as default when not provided? This generates a new password on first apply. After that, Terraform will not change it (due to `ignore_changes`). Existing deployments without the variable will get a random password; they must set it explicitly to stabilize.

2. Should `lifecycle { prevent_destroy = true }` be added to all Secrets Manager resources to prevent accidental deletion?

3. For `auth0_management_api_token` (expires after 24h), should we implement a CloudWatch Events alert for age-based rotation reminders?

4. For cross-account access, should the KMS grant be optional (`var.kms_cross_account_principals`) or always created? Making it optional avoids conflicts with existing cross-account grants.

5. Should `recovery_window_in_days` be non-zero (e.g., 7) for high-value secrets to prevent accidental deletion, while keeping it at 0 for operational secrets?

6. Should the `enable_secrets_manager` variable default to `false` for the first apply and `true` for subsequent applies, to allow operators to verify the state plan before switching?