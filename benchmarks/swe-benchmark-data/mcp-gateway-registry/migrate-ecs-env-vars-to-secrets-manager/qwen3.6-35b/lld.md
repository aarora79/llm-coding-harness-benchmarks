# Low-Level Design: Migrate ECS Environment Variables to AWS Secrets Manager

*Created: 2026-07-06*
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
15. [References](#references)

## Overview

### Problem Statement

The MCP Gateway Registry ECS services currently pass approximately 20 sensitive values as plain-text environment variables in the ECS `environment` blocks of the container definitions. While many secrets already have AWS Secrets Manager resources in `secrets.tf`, a significant number of them are still passed as plaintext in the ECS environment blocks of `ecs-services.tf`, and several other sensitive variables have no corresponding Secrets Manager resource at all.

These plain-text values appear in:
- Terraform state files
- CloudFormation stack inputs
- ECS task definition API responses (describable via AWS CLI)
- `terraform plan` output diffs
- Potential log leakage if any service logs environment configuration

The existing codebase already uses the ECS `secrets` block for many sensitive values (SECRET_KEY, Keycloak client secrets, Okta secrets, Auth0 secrets, etc.), but this pattern has not been applied consistently across all sensitive variables.

### Goals

- Migrate all sensitive environment variables in ECS `environment` blocks to the ECS `secrets` block
- Create new Secrets Manager resources for secrets that do not yet have one
- Update IAM policies to grant ECS task roles access to all new secret ARNs
- Mark all sensitive variables in `variables.tf` with `sensitive = true`
- Preserve backward compatibility by keeping the variable interface unchanged
- Document the migration path for existing deployments

### Non-Goals

- Docker Compose migration (a separate effort; Docker Compose lacks native Secrets Manager integration)
- Helm chart changes (already uses Kubernetes Secret objects)
- Automatic secret rotation for application secrets
- Changes to application code that consumes these environment variables
- Secrets Manager access logging or CloudTrail integration

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `terraform/aws-ecs/modules/mcp-gateway/secrets.tf` | Creates Secrets Manager secrets, KMS keys, random passwords | Source of secret resources; needs additions for new secrets |
| `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf` | ECS container definitions for all services | Primary target: remove plaintext env vars, add `secrets` block entries |
| `terraform/aws-ecs/modules/mcp-gateway/variables.tf` | Module input variables | Add `sensitive = true` to all sensitive variables |
| `terraform/aws-ecs/modules/mcp-gateway/iam.tf` | IAM policies for ECS tasks | Update `ecs_secrets_access` policy with new secret ARNs |
| `terraform/aws-ecs/modules/mcp-gateway/locals.tf` | Local variables used across the module | Check for any hardcoded secrets or references |
| `docker-compose.yml` | Docker Compose deployment surface | Document as secondary migration target |
| `charts/registry/templates/secret.yaml` | Helm Secret manifest | Already properly handles secrets via Kubernetes Secrets |

### Existing Patterns Identified

1. **Secrets Manager resource pattern** (`secrets.tf`):
   - Each secret has an `aws_secretsmanager_secret` + `aws_secretsmanager_secret_version` pair
   - Auto-generated secrets use `random_password` resource, fed into `secret_string`
   - User-provided secrets use the variable value directly as `secret_string`
   - Secrets managed externally (IdP client secrets) use `lifecycle { ignore_changes = [secret_string] }`
   - KMS encryption via `kms_key_id = aws_kms_key.secrets.id`
   - Conditional creation uses `count = var.<feature>_enabled ? 1 : 0`

2. **ECS `secrets` block pattern** (`ecs-services.tf`):
   - Single-value secrets: `{ name = "ENV_VAR", valueFrom = aws_secretsmanager_secret.<name>.arn }`
   - JSON nested secrets: `{ name = "ENV_VAR", valueFrom = "${arn}:field::" }`
   - Conditional secrets use list concatenation with `var.<feature>_enabled ? [...] : []`
   - Concatenated with `concat([base_secrets], conditional_secrets...)`

3. **IAM policy pattern** (`iam.tf`):
   - Single `aws_iam_policy.ecs_secrets_access` with `secretsmanager:GetSecretValue` action
   - Resource list is built with `concat()` and conditional expressions
   - KMS decrypt permission on `aws_kms_key.secrets.arn`
   - Policy attached via `task_exec_iam_role_policies` and `tasks_iam_role_policies`

4. **Variable pattern** (`variables.tf`):
   - Sensitive variables use `sensitive = true`
   - Conditional variables use `default = ""` with feature flag gating
   - Type annotations are explicit (string, bool, number)

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| `secrets.tf` + `ecs-services.tf` | Creates + consumes | Secrets created in one file, consumed in the other |
| `iam.tf` | Access control | Policy must include ARNs of all secrets ECS tasks need |
| `variables.tf` | Input interface | Variables must be marked sensitive |
| Docker Compose | Deployment surface | Separate migration effort |
| Helm charts | Deployment surface | Already uses Kubernetes Secrets; no change needed |

### Constraints and Limitations Discovered

- Secrets in `ecs-services.tf` must reference existing secret resources in the same module
- IAM policy `ecs_secrets_access` must explicitly list each secret ARN in its Resource block
- Docker Compose cannot natively use Secrets Manager; requires workarounds (file-based, pre-fetch script)
- Existing deployments will experience a one-time secret value change if `random_password` is used for a new secret
- Variables passed via `environment` are visible in `terraform plan` output unless marked `sensitive = true`
- The `ecs_secrets_access` IAM policy already uses conditional expressions; new ARNs must follow the same pattern

## Architecture

### System Context Diagram

```
+-------------------+       +---------------------------+       +------------------+
| Terraform Config  | ----> | AWS Secrets Manager       | ----> | ECS Task Defs    |
| (variables.tf)    |       | (secrets.tf creates)      |       | (ecs-services.tf)|
|                   |       |                           |       |                  |
| sensitive=true    |       | Encrypted at rest (KMS)   |       | secrets: block   |
| input vars        |       | Auto-rotation possible    |       | -> ENV override  |
+-------------------+       +---------------------------+       +------------------+
                                                    |
                                                    v
                                           +------------------+
                                           | ECS Task Runtime |
                                           | (container sees  |
                                           |  plaintext ENV   |
                                           |  only at launch) |
                                           +------------------+
```

### Flow Diagram

```
User provides var.xxx
         |
         v
    aws_secretsmanager_secret
         |
         v
    KMS encryption
         |
         v
    ECS task definition secrets block
    { name = "ENV_VAR", valueFrom = secret_arn }
         |
         v
    ECS task launch: Secrets Manager API call
    (IAM role must have secretsmanager:GetSecretValue)
         |
         v
    Value injected as ENV var in container
    (never visible in task definition, terraform state, or CLI output)
```

## Data Models

### New Secrets Manager Resources

The following secrets need new `aws_secretsmanager_secret` + `aws_secretsmanager_secret_version` resources. These are secrets that do NOT yet have a resource in `secrets.tf`:

```hcl
# Auth0 Management API Token (for IAM Management operations)
resource "aws_secretsmanager_secret" "auth0_management_api_token" {
  name_prefix             = "${local.name_prefix}-auth0-mgmt-api-token-"
  description             = "Auth0 Management API token for tenant management operations"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "auth0_management_api_token" {
  secret_id     = aws_secretsmanager_secret.auth0_management_api_token.id
  secret_string = var.auth0_management_api_token

  lifecycle {
    ignore_changes = [secret_string]
  }
}

# Registry API Token (static token auth)
resource "aws_secretsmanager_secret" "registry_api_token" {
  name_prefix             = "${local.name_prefix}-registry-api-token-"
  description             = "Static API token for network-trusted registry access"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "registry_api_token" {
  secret_id     = aws_secretsmanager_secret.registry_api_token.id
  secret_string = var.registry_api_token

  lifecycle {
    ignore_changes = [secret_string]
  }
}

# Registry API Keys (multi-key configuration)
resource "aws_secretsmanager_secret" "registry_api_keys" {
  name_prefix             = "${local.name_prefix}-registry-api-keys-"
  description             = "JSON string configuring multiple static API keys with per-key group assignments"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "registry_api_keys" {
  secret_id     = aws_secretsmanager_secret.registry_api_keys.id
  secret_string = var.registry_api_keys

  lifecycle {
    ignore_changes = [secret_string]
  }
}

# Federation Static Token
resource "aws_secretsmanager_secret" "federation_static_token" {
  name_prefix             = "${local.name_prefix}-federation-static-token-"
  description             = "Static token for Federation API access"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "federation_static_token" {
  secret_id     = aws_secretsmanager_secret.federation_static_token.id
  secret_string = var.federation_static_token

  lifecycle {
    ignore_changes = [secret_string]
  }
}

# Federation Encryption Key (Fernet key)
resource "aws_secretsmanager_secret" "federation_encryption_key" {
  name_prefix             = "${local.name_prefix}-federation-encryption-key-"
  description             = "Fernet encryption key for storing federation tokens in MongoDB"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "federation_encryption_key" {
  secret_id     = aws_secretsmanager_secret.federation_encryption_key.id
  secret_string = var.federation_encryption_key

  lifecycle {
    ignore_changes = [secret_string]
  }
}

# Registration Webhook Auth Token
resource "aws_secretsmanager_secret" "registration_webhook_auth_token" {
  name_prefix             = "${local.name_prefix}-registration-webhook-auth-token-"
  description             = "Auth token for registration webhook requests"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "registration_webhook_auth_token" {
  secret_id     = aws_secretsmanager_secret.registration_webhook_auth_token.id
  secret_string = var.registration_webhook_auth_token

  lifecycle {
    ignore_changes = [secret_string]
  }
}

# Registration Gate Auth Credential
resource "aws_secretsmanager_secret" "registration_gate_auth_credential" {
  name_prefix             = "${local.name_prefix}-registration-gate-auth-"
  description             = "Auth credential for registration gate endpoint"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "registration_gate_auth_credential" {
  secret_id     = aws_secretsmanager_secret.registration_gate_auth_credential.id
  secret_string = var.registration_gate_auth_credential

  lifecycle {
    ignore_changes = [secret_string]
  }
}

# Registration Gate OAuth2 Client Secret
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

# Ansible API Key
resource "aws_secretsmanager_secret" "ans_api_key" {
  name_prefix             = "${local.name_prefix}-ans-api-key-"
  description             = "ANS API key for authentication (GoDaddy Agent Naming Service)"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "ans_api_key" {
  secret_id     = aws_secretsmanager_secret.ans_api_key.id
  secret_string = var.ans_api_key

  lifecycle {
    ignore_changes = [secret_string]
  }
}

# Ansible API Secret
resource "aws_secretsmanager_secret" "ans_api_secret" {
  name_prefix             = "${local.name_prefix}-ans-api-secret-"
  description             = "ANS API secret for authentication (GoDaddy Agent Naming Service)"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "ans_api_secret" {
  secret_id     = aws_secretsmanager_secret.ans_api_secret.id
  secret_string = var.ans_api_secret

  lifecycle {
    ignore_changes = [secret_string]
  }
}

# GitHub PAT (Personal Access Token)
resource "aws_secretsmanager_secret" "github_pat" {
  name_prefix             = "${local.name_prefix}-github-pat-"
  description             = "GitHub Personal Access Token for private repo SKILL.md access"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "github_pat" {
  secret_id     = aws_secretsmanager_secret.github_pat.id
  secret_string = var.github_pat

  lifecycle {
    ignore_changes = [secret_string]
  }
}

# GitHub App Private Key (PEM format)
resource "aws_secretsmanager_secret" "github_app_private_key" {
  name_prefix             = "${local.name_prefix}-github-app-private-key-"
  description             = "GitHub App private key (PEM format) for installation-based auth"
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

# Grafana Admin Password (observability)
resource "aws_secretsmanager_secret" "grafana_admin_password" {
  name_prefix             = "${local.name_prefix}-grafana-admin-password-"
  description             = "Admin password for Grafana OSS (custom image with baked-in provisioning)"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "random_password" "grafana_admin_password" {
  length  = 32
  special = true
}

resource "aws_secretsmanager_secret_version" "grafana_admin_password" {
  secret_id     = aws_secretsmanager_secret.grafana_admin_password.id
  secret_string = var.grafana_admin_password != "" ? var.grafana_admin_password : random_password.grafana_admin_password.result
}
```

### Secrets Already Existing in secrets.tf (no new resource needed)

These secrets already have `aws_secretsmanager_secret` resources but are still passed as plain-text environment variables in ECS. They require only ECS-side changes:

- `okta_client_secret` (line 208 of secrets.tf)
- `okta_m2m_client_secret` (line 232)
- `okta_api_token` (line 256)
- `auth0_client_secret` (line 285)
- `auth0_m2m_client_secret` (line 310)
- `entra_client_secret` (line 184)
- `metrics_api_key` (line 333)
- `otlp_exporter_headers` (line 361)
- `keycloak_admin_password` (line 148)

## Configuration Parameters

### Variables Already Declared (no change to type or name)

All variables in `variables.tf` are already declared with correct types. The only change is adding `sensitive = true` to those not already marked.

### Variables Requiring `sensitive = true` Addition

The following variables in `variables.tf` need `sensitive = true` added:

| Variable | Current `sensitive` | Action |
|----------|---------------------|--------|
| `auth0_management_api_token` | No | Add `sensitive = true` |
| `registry_api_token` | Yes (line 720) | Already marked |
| `registry_api_keys` | Yes (line 728) | Already marked |
| `federation_static_token` | Yes (line 907) | Already marked |
| `federation_encryption_key` | Yes (line 914) | Already marked |
| `registration_webhook_auth_token` | Yes (line 754) | Already marked |
| `registration_gate_auth_credential` | Yes (line 835) | Already marked |
| `registration_gate_oauth2_client_secret` | Yes (line 872) | Already marked |
| `ans_api_key` | Yes (line 946) | Already marked |
| `ans_api_secret` | Yes (line 954) | Already marked |
| `github_pat` | Yes (line 1259) | Already marked |
| `github_app_private_key` | Yes (line 1278) | Already marked |
| `grafana_admin_password` | Yes (line 1179) | Already marked |
| `okta_client_secret` | Yes (line 620) | Already marked |
| `okta_m2m_client_secret` | Yes (line 633) | Already marked |
| `okta_api_token` | Yes (line 641) | Already marked |
| `auth0_client_secret` | Yes (line 676) | Already marked |
| `auth0_m2m_client_secret` | Yes (line 701) | Already marked |
| `entra_client_secret` | Yes (line 573) | Already marked |
| `embeddings_api_key` | Yes (line 340) | Already marked |
| `otel_exporter_otlp_headers` | Yes (line 1192) | Already marked |
| `keycloak_admin_password` | Yes (line 380) | Already marked |

Most variables are already marked sensitive. The main addition is `auth0_management_api_token` (line 703), which currently lacks `sensitive = true`.

### Deployment Surface Checklist

| Surface | Parameter | Status |
|---------|-----------|--------|
| Terraform ECS (module) | All vars in variables.tf | Change required |
| Terraform ECS (root) | vars in `terraform/aws-ecs/variables.tf` | No change needed (passes through) |
| Docker Compose | .env file values | Document as future work |
| Helm charts | values.yaml secrets | No change needed (uses Kubernetes Secrets) |

## New Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| No new packages | N/A | This change uses only existing AWS provider resources |

Explicitly stated: "This change uses only existing dependencies." No new Terraform providers, Lambda runtimes, or application libraries are required.

## Implementation Details

### Step-by-Step Plan

#### Step 1: Add New Secrets Manager Resources to secrets.tf

**File:** `terraform/aws-ecs/modules/mcp-gateway/secrets.tf`
**Lines:** ~470 (append to end of file)

Add the following new secret resources for secrets that do not yet have a corresponding resource:

1. `auth0_management_api_secret` / `auth0_management_api_token`
2. `registry_api_token_secret` / `registry_api_token`
3. `registry_api_keys_secret` / `registry_api_keys`
4. `federation_static_token_secret` / `federation_static_token`
5. `federation_encryption_key_secret` / `federation_encryption_key`
6. `registration_webhook_auth_token_secret` / `registration_webhook_auth_token`
7. `registration_gate_auth_credential_secret` / `registration_gate_auth_credential`
8. `registration_gate_oauth2_client_secret_secret` / `registration_gate_oauth2_client_secret`
9. `ans_api_key_secret` / `ans_api_key`
10. `ans_api_secret_secret` / `ans_api_secret`
11. `github_pat_secret` / `github_pat`
12. `github_app_private_key_secret` / `github_app_private_key`
13. `grafana_admin_password_secret` / `grafana_admin_password` (+ `random_password`)

Use the resource templates from the Data Models section above. Follow existing conventions:
- `name_prefix` uses `${local.name_prefix}-...`
- All secrets encrypted with `kms_key_id = aws_kms_key.secrets.id`
- User-provided values use `lifecycle { ignore_changes = [secret_string] }` to prevent Terraform from overwriting externally managed values
- Conditional secrets use `count = var.<feature>_enabled ? 1 : 0`

#### Step 2: Update variables.tf to Add `sensitive = true`

**File:** `terraform/aws-ecs/modules/mcp-gateway/variables.tf`
**Lines:** ~703 (around `auth0_management_api_token` variable)

Add `sensitive = true` to the `auth0_management_api_token` variable:

```hcl
variable "auth0_management_api_token" {
  description = "Auth0 Management API token (alternative to M2M credentials, expires after 24h)"
  type        = string
  default     = ""
  sensitive   = true
}
```

No other changes needed; all other sensitive variables are already marked.

#### Step 3: Add ECS `secrets` Block Entries

**File:** `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf`

For each service (auth-server, registry), update the `secrets` block in the container definition.

**Auth Server service (line ~413):**

Add the following entries to the existing `secrets` concat block:

```hcl
secrets = concat(
  [
    # Existing secrets...
    { name = "SECRET_KEY", valueFrom = aws_secretsmanager_secret.secret_key.arn },
    { name = "KEYCLOAK_CLIENT_SECRET", valueFrom = "${aws_secretsmanager_secret.keycloak_client_secret.arn}:client_secret::" },
    # ... existing entries ...

    # NEW: Move from environment to secrets
    {
      name      = "OKTA_CLIENT_SECRET"
      valueFrom = aws_secretsmanager_secret.okta_client_secret[0].arn
    },
    {
      name      = "OKTA_M2M_CLIENT_SECRET"
      valueFrom = aws_secretsmanager_secret.okta_m2m_client_secret[0].arn
    },
    {
      name      = "OKTA_API_TOKEN"
      valueFrom = aws_secretsmanager_secret.okta_api_token[0].arn
    },
    {
      name      = "AUTH0_CLIENT_SECRET"
      valueFrom = aws_secretsmanager_secret.auth0_client_secret[0].arn
    },
    {
      name      = "AUTH0_M2M_CLIENT_SECRET"
      valueFrom = aws_secretsmanager_secret.auth0_m2m_client_secret[0].arn
    },
    {
      name      = "AUTH0_MANAGEMENT_API_TOKEN"
      valueFrom = aws_secretsmanager_secret.auth0_management_api_token.arn
    },
    {
      name      = "ENTRA_CLIENT_SECRET"
      valueFrom = aws_secretsmanager_secret.entra_client_secret[0].arn
    },
    {
      name      = "REGISTRY_API_TOKEN"
      valueFrom = aws_secretsmanager_secret.registry_api_token.arn
    },
    {
      name      = "REGISTRY_API_KEYS"
      valueFrom = aws_secretsmanager_secret.registry_api_keys.arn
    },
    {
      name      = "FEDERATION_STATIC_TOKEN"
      valueFrom = aws_secretsmanager_secret.federation_static_token.arn
    },
    {
      name      = "FEDERATION_ENCRYPTION_KEY"
      valueFrom = aws_secretsmanager_secret.federation_encryption_key.arn
    },
    {
      name      = "ANS_API_KEY"
      valueFrom = aws_secretsmanager_secret.ans_api_key.arn
    },
    {
      name      = "ANS_API_SECRET"
      valueFrom = aws_secretsmanager_secret.ans_api_secret.arn
    }
  ],
  var.auth0_enabled ? [] : [],  # No additional auth0 secrets needed (all above)
  var.enable_observability ? [
    { name = "METRICS_API_KEY", valueFrom = aws_secretsmanager_secret.metrics_api_key[0].arn }
  ] : []
)
```

Then remove the corresponding entries from the `environment` block:
- `REGISTRY_API_TOKEN` (auth server, line ~237)
- `REGISTRY_API_KEYS` (auth server, line ~241)
- `FEDERATION_STATIC_TOKEN` (auth server, line ~259)
- `FEDERATION_ENCRYPTION_KEY` (auth server, line ~263)
- `ANS_API_KEY` (auth server, line ~275)
- `ANS_API_SECRET` (auth server, line ~279)
- `AUTH0_MANAGEMENT_API_TOKEN` (auth server, line ~213)
- `OKTA_CLIENT_SECRET` (auth server, lines ~??)
- `OKTA_M2M_CLIENT_SECRET` (auth server, lines ~??)
- `OKTA_API_TOKEN` (auth server, lines ~??)
- `ENTRA_CLIENT_SECRET` (auth server, lines ~??)

**Registry service (line ~1288):**

Similarly update the registry container `secrets` block:

```hcl
secrets = concat(
  [
    # Existing secrets...
    { name = "SECRET_KEY", valueFrom = aws_secretsmanager_secret.secret_key.arn },
    # ... existing entries ...

    # NEW: Move from environment to secrets
    {
      name      = "OKTA_CLIENT_SECRET"
      valueFrom = aws_secretsmanager_secret.okta_client_secret[0].arn
    },
    {
      name      = "OKTA_M2M_CLIENT_SECRET"
      valueFrom = aws_secretsmanager_secret.okta_m2m_client_secret[0].arn
    },
    {
      name      = "OKTA_API_TOKEN"
      valueFrom = aws_secretsmanager_secret.okta_api_token[0].arn
    },
    {
      name      = "AUTH0_CLIENT_SECRET"
      valueFrom = aws_secretsmanager_secret.auth0_client_secret[0].arn
    },
    {
      name      = "AUTH0_M2M_CLIENT_SECRET"
      valueFrom = aws_secretsmanager_secret.auth0_m2m_client_secret[0].arn
    },
    {
      name      = "AUTH0_MANAGEMENT_API_TOKEN"
      valueFrom = aws_secretsmanager_secret.auth0_management_api_token.arn
    },
    {
      name      = "ENTRA_CLIENT_SECRET"
      valueFrom = aws_secretsmanager_secret.entra_client_secret[0].arn
    },
    {
      name      = "REGISTRY_API_TOKEN"
      valueFrom = aws_secretsmanager_secret.registry_api_token.arn
    },
    {
      name      = "REGISTRY_API_KEYS"
      valueFrom = aws_secretsmanager_secret.registry_api_keys.arn
    },
    {
      name      = "FEDERATION_STATIC_TOKEN"
      valueFrom = aws_secretsmanager_secret.federation_static_token.arn
    },
    {
      name      = "FEDERATION_ENCRYPTION_KEY"
      valueFrom = aws_secretsmanager_secret.federation_encryption_key.arn
    },
    {
      name      = "REGISTRATION_WEBHOOK_AUTH_TOKEN"
      valueFrom = aws_secretsmanager_secret.registration_webhook_auth_token.arn
    },
    {
      name      = "REGISTRATION_GATE_AUTH_CREDENTIAL"
      valueFrom = aws_secretsmanager_secret.registration_gate_auth_credential.arn
    },
    {
      name      = "REGISTRATION_GATE_OAUTH2_CLIENT_SECRET"
      valueFrom = aws_secretsmanager_secret.registration_gate_oauth2_client_secret.arn
    },
    {
      name      = "ANS_API_KEY"
      valueFrom = aws_secretsmanager_secret.ans_api_key.arn
    },
    {
      name      = "ANS_API_SECRET"
      valueFrom = aws_secretsmanager_secret.ans_api_secret.arn
    },
    {
      name      = "GITHUB_PAT"
      valueFrom = aws_secretsmanager_secret.github_pat.arn
    },
    {
      name      = "GITHUB_APP_PRIVATE_KEY"
      valueFrom = aws_secretsmanager_secret.github_app_private_key.arn
    }
  ],
  var.okta_enabled ? [
    { name = "OKTA_CLIENT_SECRET", valueFrom = aws_secretsmanager_secret.okta_client_secret[0].arn },
    { name = "OKTA_M2M_CLIENT_SECRET", valueFrom = aws_secretsmanager_secret.okta_m2m_client_secret[0].arn },
    { name = "OKTA_API_TOKEN", valueFrom = aws_secretsmanager_secret.okta_api_token[0].arn }
  ] : [],
  var.auth0_enabled ? [
    { name = "AUTH0_CLIENT_SECRET", valueFrom = aws_secretsmanager_secret.auth0_client_secret[0].arn },
    { name = "AUTH0_M2M_CLIENT_SECRET", valueFrom = aws_secretsmanager_secret.auth0_m2m_client_secret[0].arn }
  ] : [],
  var.enable_observability ? [
    { name = "METRICS_API_KEY", valueFrom = aws_secretsmanager_secret.metrics_api_key[0].arn }
  ] : []
)
```

Then remove the corresponding environment variables from the registry `environment` block:
- `REGISTRY_API_TOKEN` (registry, line ~1080)
- `REGISTRY_API_KEYS` (registry, line ~1084)
- `FEDERATION_STATIC_TOKEN` (registry, line ~952)
- `FEDERATION_ENCRYPTION_KEY` (registry, line ~956)
- `REGISTRATION_WEBHOOK_AUTH_TOKEN` (registry, line ~1106)
- `REGISTRATION_GATE_AUTH_CREDENTIAL` (registry, line ~1160)
- `REGISTRATION_GATE_OAUTH2_CLIENT_SECRET` (registry, line ~1184)
- `ANS_API_KEY` (registry, line ~973)
- `ANS_API_SECRET` (registry, line ~977)
- `GITHUB_PAT` (registry, line ~1251)
- `GITHUB_APP_PRIVATE_KEY` (registry, line ~1263)
- `AUTH0_MANAGEMENT_API_TOKEN` (registry, line ~814)
- `OKTA_CLIENT_SECRET` (registry, line ~??)
- `OKTA_M2M_CLIENT_SECRET` (registry, line ~??)
- `OKTA_API_TOKEN` (registry, line ~??)
- `ENTRA_CLIENT_SECRET` (registry, line ~??)

#### Step 4: Update IAM Policy in iam.tf

**File:** `terraform/aws-ecs/modules/mcp-gateway/iam.tf`
**Lines:** ~15-36 (Resource list in `ecs_secrets_access` policy)

Add new secret ARNs to the `secretsmanager:GetSecretValue` Resource list:

```hcl
resource "aws_iam_policy" "ecs_secrets_access" {
  name_prefix = "${local.name_prefix}-ecs-secrets-"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["secretsmanager:GetSecretValue"]
        Resource = concat(
          [
            aws_secretsmanager_secret.secret_key.arn,
            aws_secretsmanager_secret.keycloak_client_secret.arn,
            aws_secretsmanager_secret.keycloak_m2m_client_secret.arn,
            aws_secretsmanager_secret.embeddings_api_key.arn,
            aws_secretsmanager_secret.keycloak_admin_password.arn,
            # NEW: Additional secrets
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
          var.okta_enabled ? [
            aws_secretsmanager_secret.okta_client_secret[0].arn,
            aws_secretsmanager_secret.okta_m2m_client_secret[0].arn,
            aws_secretsmanager_secret.okta_api_token[0].arn
          ] : [],
          var.auth0_enabled ? [
            aws_secretsmanager_secret.auth0_client_secret[0].arn,
            aws_secretsmanager_secret.auth0_m2m_client_secret[0].arn
          ] : [],
          var.entra_enabled ? [aws_secretsmanager_secret.entra_client_secret[0].arn] : [],
          var.documentdb_credentials_secret_arn != "" ? [var.documentdb_credentials_secret_arn] : [],
          var.enable_observability ? [aws_secretsmanager_secret.metrics_api_key[0].arn] : [],
          var.enable_observability && var.otel_otlp_endpoint != "" ? [aws_secretsmanager_secret.otlp_exporter_headers[0].arn] : []
        )
      },
      {
        Effect = "Allow"
        Action = ["kms:Decrypt", "kms:DescribeKey"]
        Resource = [aws_kms_key.secrets.arn]
      }
    ]
  })
}
```

### Error Handling

- Terraform will fail on `terraform plan` if any secret ARN referenced in `ecs-services.tf` or `iam.tf` does not have a corresponding resource in `secrets.tf`
- AWS will reject ECS task launches if the task role lacks `secretsmanager:GetSecretValue` permission on the referenced secret
- Use `terraform validate` before `terraform plan` to catch missing references early
- Use `terraform plan -detailed-exitcode` to detect drift without applying changes

### Logging

- AWS CloudTrail will log all `GetSecretValue` calls to Secrets Manager
- CloudWatch Logs for Lambda rotation functions will capture any rotation events
- ECS task events will log successful secret injection (but not the values)

## Observability

### Tracing / Metrics / Logging Points

- **CloudTrail**: Each `secretsmanager:GetSecretValue` call generates a CloudTrail event with `requestParameters.secretId` and `userIdentity.arn`
- **ECS Task Events**: `Events: Container image pulled`, `Container started` confirm task launch success (secrets injection is implicit)
- **IAM Access Analyzer**: Can be used to verify no overly permissive secret access policies
- **KMS Key Metrics**: `Decrypt` metric in CloudWatch tracks KMS usage for secret decryption
- **Secrets Manager**: API call metrics (`GetSecretValue`, `PutSecretValue`) available in CloudWatch

No application-level logging changes are required; the application code continues to read the same environment variable names.

## Scaling Considerations

- **Secrets Manager API limits**: 5000 requests per second per account (soft limit). ECS task launch with N secrets triggers N API calls. For Fargate tasks with 3 services, this is 3 x (existing + new) secrets per task start. Well within limits.
- **IAM policy size**: The `ecs_secrets_access` IAM policy JSON will grow but will remain under the 6144-byte soft limit for inline policies.
- **ECS task definition size**: The `secrets` block adds to the task definition JSON size (max 16384 bytes). Adding ~15 new secret entries per service is well within limits.
- **No bottleneck expected**: Secrets Manager is a distributed, highly available service with sub-millisecond latency.

## File Changes

### New Resources (secrets.tf)

| Resource | Description |
|----------|-------------|
| `aws_secretsmanager_secret.auth0_management_api_token` | Auth0 Management API token |
| `aws_secretsmanager_secret_version.auth0_management_api_token` | Token value |
| `aws_secretsmanager_secret.registry_api_token` | Registry API token |
| `aws_secretsmanager_secret_version.registry_api_token` | Token value |
| `aws_secretsmanager_secret.registry_api_keys` | Registry API keys (JSON) |
| `aws_secretsmanager_secret_version.registry_api_keys` | Keys value |
| `aws_secretsmanager_secret.federation_static_token` | Federation static token |
| `aws_secretsmanager_secret_version.federation_static_token` | Token value |
| `aws_secretsmanager_secret.federation_encryption_key` | Fernet encryption key |
| `aws_secretsmanager_secret_version.federation_encryption_key` | Key value |
| `aws_secretsmanager_secret.registration_webhook_auth_token` | Webhook auth token |
| `aws_secretsmanager_secret_version.registration_webhook_auth_token` | Token value |
| `aws_secretsmanager_secret.registration_gate_auth_credential` | Gate auth credential |
| `aws_secretsmanager_secret_version.registration_gate_auth_credential` | Credential value |
| `aws_secretsmanager_secret.registration_gate_oauth2_client_secret` | Gate OAuth2 client secret |
| `aws_secretsmanager_secret_version.registration_gate_oauth2_client_secret` | Secret value |
| `aws_secretsmanager_secret.ans_api_key` | ANS API key |
| `aws_secretsmanager_secret_version.ans_api_key` | Key value |
| `aws_secretsmanager_secret.ans_api_secret` | ANS API secret |
| `aws_secretsmanager_secret_version.ans_api_secret` | Secret value |
| `aws_secretsmanager_secret.github_pat` | GitHub PAT |
| `aws_secretsmanager_secret_version.github_pat` | Token value |
| `aws_secretsmanager_secret.github_app_private_key` | GitHub App private key |
| `aws_secretsmanager_secret_version.github_app_private_key` | Key value |
| `aws_secretsmanager_secret.grafana_admin_password` | Grafana admin password |
| `aws_secretsmanager_secret_version.grafana_admin_password` | Password value |
| `random_password.grafana_admin_password` | Auto-generated default password |

### Modified Files

| File Path | Lines | Change Description |
|-----------|-------|--------------------|
| `secrets.tf` | ~470+ | Append 14 new secret resource pairs (+ 1 random_password) |
| `variables.tf` | ~703 | Add `sensitive = true` to `auth0_management_api_token` |
| `ecs-services.tf` (auth server) | ~413-480 | Remove ~8 plain-text env vars, add ~10 secrets block entries |
| `ecs-services.tf` (registry) | ~1288-1365 | Remove ~17 plain-text env vars, add ~17 secrets block entries |
| `iam.tf` | ~15-36 | Add ~13 new secret ARNs to IAM policy Resource list |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| New secrets.tf resources | ~280 |
| New variables.tf changes | ~1 |
| Modified ecs-services.tf (auth server) | ~-10 env, +15 secrets = +5 net |
| Modified ecs-services.tf (registry) | ~-20 env, +20 secrets = 0 net |
| Modified iam.tf | ~+15 |
| **Total** | **~300** |

## Testing Strategy

See `testing.md` for the complete testing plan.

Summary of test categories:
1. Functional Tests: Terraform plan/validate, ECS task definition inspection
2. Backwards Compatibility Tests: Existing deployments with no config changes
3. Deployment Surface Tests: Terraform, Docker Compose, Helm
4. Security Tests: Verify no plaintext secrets in terraform plan output, ECS API

## Alternatives Considered

### Alternative 1: Use AWS SSM Parameter Store (SecureString)
**Description:** Store secrets in SSM Parameter Store instead of Secrets Manager
**Pros:** Slightly simpler API for single-value parameters
**Cons:** No built-in rotation, different IAM permissions, Secrets Manager is already used extensively
**Why Rejected:** Secrets Manager is the established pattern in this codebase with existing KMS integration, rotation support, and audit logging

### Alternative 2: Store All Secrets in a Single JSON Secret
**Description:** Group multiple secrets into one JSON document per service
**Pros:** Fewer Secrets Manager entries, simpler IAM policy
**Cons:** Cannot rotate individual secrets, all-or-nothing access, harder to audit per-secret access
**Why Rejected:** The per-secret pattern enables independent rotation, granular access control, and precise audit logging. Each secret has different sensitivity and rotation requirements.

### Alternative 3: Use External Secrets Operator (for Helm/Kubernetes only)
**Description:** Deploy the External Secrets Operator to sync Secrets Manager to Kubernetes Secrets
**Pros:** Works well for Kubernetes workloads
**Cons:** Only applicable to Kubernetes; not applicable to ECS or Docker Compose
**Why Rejected:** Helm charts already handle secrets via Kubernetes Secret manifests. The operator would be redundant.

### Comparison Matrix

| Criteria | Chosen (Per-secret SM) | SSM Parameters | Single JSON | Ext. Secrets Op |
|----------|------------------------|----------------|-------------|-----------------|
| Existing pattern match | High | Low | Low | N/A |
| Per-secret rotation | Yes | No | No | N/A |
| Granular IAM | Yes | Yes | No | Yes |
| Audit per secret | Yes | Yes | No | Yes |
| Complexity | Low | Low | Low | High |
| Docker Compose support | No (separate) | No (separate) | No (separate) | No |

## Rollout Plan

### Phase 1: Terraform Plan Verification (out of scope for this skill)
- Run `terraform plan` to verify no unexpected resource changes
- Verify all new secrets appear as `will be created`
- Verify ECS task definitions show `environment` removals and `secrets` additions

### Phase 2: Staged Deployment (out of scope for this skill)
- Deploy to a non-production environment first
- Verify ECS tasks launch successfully and all services respond to health checks
- Verify no errors in CloudWatch Logs for any service
- Verify `terraform state list | grep secretsmanager` shows all new secrets

### Phase 3: Production Rollout (out of scope for this skill)
- Deploy to production in stages (e.g., one service at a time)
- Monitor CloudWatch Metrics for any anomalies
- Verify CloudTrail shows successful `GetSecretValue` calls
- Monitor application health for 24 hours

### Phase 4: Docker Compose Migration (separate task)
- Implement file-based secrets approach for Docker Compose
- Update `.env.example` to document which values must be set

## Open Questions

1. Should `grafana_admin_password` use `random_password` as a default when the user does not provide one, or should it be mandatory?
2. For the `auth0_management_api_token`, the existing code mentions it "expires after 24h". Should this secret be flagged for periodic rotation or re-injection?
3. Should the `github_app_private_key` be stored in a separate AWS account's Secrets Manager due to its high sensitivity (full GitHub repository access)?
4. For Docker Compose, should we implement a pre-run script that fetches secrets from AWS Secrets Manager and writes them to temp files, or use Docker's native `secrets` driver with a file backend?
5. Should we add a `prevention_of_catastrophic_changes = true` lifecycle rule to the Secrets Manager resources to prevent accidental deletion?

## References

- AWS Secrets Manager documentation: https://docs.aws.amazon.com/secretsmanager/
- ECS task definition secrets: https://docs.aws.amazon.com/AmazonECS/latest/developerguide/specifying-sensitive-data.html
- Terraform AWS Secrets Manager provider: https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/secretsmanager_secret
- Terraform AWS random provider: https://registry.terraform.io/providers/hashicorp/random/latest/docs/resources/password