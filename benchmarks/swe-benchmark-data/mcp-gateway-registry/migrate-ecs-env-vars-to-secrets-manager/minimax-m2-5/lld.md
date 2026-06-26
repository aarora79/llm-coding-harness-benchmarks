# Low-Level Design: Migrate ECS Environment Variables to AWS Secrets Manager

*Created: 2026-06-25*
*Author: Claude (minimax-m2-5)*
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
Multiple sensitive environment variables in ECS task definitions are passed as plaintext via the `environment` block instead of using AWS Secrets Manager via the `secrets` block. This exposes sensitive data (API keys, tokens, passwords) in:
1. ECS task definitions stored in Terraform state
2. AWS Console ECS task details
3. CloudWatch logs (potentially)

Reference: Issue #1134

### Goals
- Move all sensitive environment variables to AWS Secrets Manager secrets
- Ensure backward compatibility during migration
- Add IAM permissions for ECS tasks to read new secrets
- Maintain all existing functionality

### Non-Goals
- Implementing secret rotation (separate issue)
- Using AWS Parameter Store (only Secrets Manager)
- Migrating non-sensitive environment variables
- Changing secret values or rotation schedules

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `terraform/aws-ecs/modules/mcp-gateway/secrets.tf` | Existing Secrets Manager resources | Add new secrets here |
| `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf` | ECS task definitions with environment vars | Migrate env to secrets |
| `terraform/aws-ecs/modules/mcp-gateway/iam.tf` | IAM policies for ECS task execution | Add new secret ARNs |
| `terraform/aws-ecs/modules/mcp-gateway/variables.tf` | Variable definitions | Add new secret ARN variables |
| `terraform/aws-ecs/variables.tf` | Top-level variables | Pass new secret ARNs to module |
| `terraform/aws-ecs/terraform.tfvars.example` | Example configuration | Document new variables |

### Existing Patterns Identified
1. **Secrets definition**: `aws_secretsmanager_secret` + `aws_secretsmanager_secret_version` in `secrets.tf`
2. **ECS secrets injection**: `secrets = concat([...])` block in container definitions
3. **IAM permissions**: `aws_iam_policy.ecs_secrets_access` grants `secretsmanager:GetSecretValue`
4. **Secret naming convention**: `${local.name_prefix}-{secret-name}-` for secret names

### Constraints and Limitations Discovered
- Backward compatibility required - cannot break existing deployments
- Secrets Manager secret names must be unique within the account
- Some secrets already exist and are properly injected via `secrets` block

## Architecture

### System Context Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                        AWS Account                                │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │               Terraform State (S3)                          │ │
│  │  - Contains only secret ARNs, not plaintext values          │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                   │
│  ┌─────────────────┐    ┌─────────────────┐                     │
│  │   IAM Roles     │    │ Secrets Manager │                     │
│  │  (ECS Task Exec)│◄───│  (New Secrets)  │                     │
│  └────────┬────────┘    └────────┬────────┘                     │
│           │                      │                               │
│           ▼                      ▼                               │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │              ECS Fargate Task                               │ │
│  │  ┌────────────────┐  ┌─────────────────┐                   │ │
│  │  │  Auth Server   │  │    Registry     │                   │ │
│  │  │ (secrets: env) │  │ (secrets: env)  │                   │ │
│  │  └────────────────┘  └─────────────────┘                   │ │
│  └─────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

### Migration Flow

1. Add new Secrets Manager secrets in `secrets.tf`
2. Add new secret ARN variables in `variables.tf`
3. Update ECS container definitions in `ecs-services.tf`
4. Update IAM policy in `iam.tf` to include new secret ARNs
5. Deployment runs with dual provision (env vars + secrets) for backward compat
6. After validation, remove deprecated env vars

## Data Models

### New Terraform Resources

```hcl
# New Secrets Manager secrets (10 total)

# Registry API Token
resource "aws_secretsmanager_secret" "registry_api_token" {
  name_prefix             = "${local.name_prefix}-registry-api-token-"
  description             = "Registry API token for static token authentication"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "registry_api_token" {
  secret_id     = aws_secretsmanager_secret.registry_api_token.id
  secret_string = var.registry_api_token
}

# Registry API Keys (JSON string)
resource "aws_secretsmanager_secret" "registry_api_keys" {
  name_prefix             = "${local.name_prefix}-registry-api-keys-"
  description             = "Registry API keys for multi-key authentication"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "registry_api_keys" {
  secret_id     = aws_secretsmanager_secret.registry_api_keys.id
  secret_string = var.registry_api_keys
}

# Federation Static Token
resource "aws_secretsmanager_secret" "federation_static_token" {
  name_prefix             = "${local.name_prefix}-federation-static-token-"
  description             = "Static token for peer-to-peer registry federation"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "federation_static_token" {
  secret_id     = aws_secretsmanager_secret.federation_static_token.id
  secret_string = var.federation_static_token
}

# Federation Encryption Key
resource "aws_secretsmanager_secret" "federation_encryption_key" {
  name_prefix             = "${local.name_prefix}-federation-encryption-key-"
  description             = "Encryption key for registry federation"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "federation_encryption_key" {
  secret_id     = aws_secretsmanager_secret.federation_encryption_key.id
  secret_string = var.federation_encryption_key
}

# ANS API Key
resource "aws_secretsmanager_secret" "ans_api_key" {
  name_prefix             = "${local.name_prefix}-ans-api-key-"
  description             = "ANS API key for Agent Name Service integration"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "ans_api_key" {
  secret_id     = aws_secretsmanager_secret.ans_api_key.id
  secret_string = var.ans_api_key
}

# ANS API Secret
resource "aws_secretsmanager_secret" "ans_api_secret" {
  name_prefix             = "${local.name_prefix}-ans-api-secret-"
  description             = "ANS API secret for Agent Name Service integration"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "ans_api_secret" {
  secret_id     = aws_secretsmanager_secret.ans_api_secret.id
  secret_string = var.ans_api_secret
}

# Registration Webhook Auth Token
resource "aws_secretsmanager_secret" "registration_webhook_auth_token" {
  name_prefix             = "${local.name_prefix}-registration-webhook-auth-token-"
  description             = "Auth token for registration webhook"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "registration_webhook_auth_token" {
  secret_id     = aws_secretsmanager_secret.registration_webhook_auth_token.id
  secret_string = var.registration_webhook_auth_token
}

# Registration Gate Auth Credential
resource "aws_secretsmanager_secret" "registration_gate_auth_credential" {
  name_prefix             = "${local.name_prefix}-registration-gate-auth-credential-"
  description             = "Auth credential for registration gate"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "registration_gate_auth_credential" {
  secret_id     = aws_secretsmanager_secret.registration_gate_auth_credential.id
  secret_string = var.registration_gate_auth_credential
}

# Registration Gate OAuth2 Client Secret
resource "aws_secretsmanager_secret" "registration_gate_oauth2_client_secret" {
  name_prefix             = "${local.name_prefix}-registration-gate-oauth2-client-secret-"
  description             = "OAuth2 client secret for registration gate"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "registration_gate_oauth2_client_secret" {
  secret_id     = aws_secretsmanager_secret.registration_gate_oauth2_client_secret.id
  secret_string = var.registration_gate_oauth2_client_secret
}

# Auth0 Management API Token
resource "aws_secretsmanager_secret" "auth0_management_api_token" {
  count = var.auth0_enabled ? 1 : 0

  name_prefix             = "${local.name_prefix}-auth0-management-api-token-"
  description             = "Auth0 management API token for user/group operations"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "auth0_management_api_token" {
  count = var.auth0_enabled ? 1 : 0

  secret_id     = aws_secretsmanager_secret.auth0_management_api_token[0].id
  secret_string = var.auth0_management_api_token
}
```

## API / CLI Design

This change does not affect the API or CLI; it is purely an infrastructure change.

## Configuration Parameters

### New / Modified Environment Variables

| Variable Name | Type | Default | Required | Description |
|---------------|------|---------|----------|-------------|
| (none - Infra only) | - | - | - | All vars moved to Secrets Manager |

### Terraform Variables Updated

| Variable | Type | Default | Required | Description |
|----------|------|---------|----------|-------------|
| `registry_api_token` | string | "" | No | Static token for registry auth (migrated to Secrets Manager) |
| `registry_api_keys` | string | "" | No | API keys for registry auth (migrated to Secrets Manager) |
| `federation_static_token` | string | "" | No | Federation token (migrated to Secrets Manager) |
| `federation_encryption_key` | string | "" | No | Federation encryption key (migrated to Secrets Manager) |
| `ans_api_key` | string | "" | No | ANS API key (migrated to Secrets Manager) |
| `ans_api_secret` | string | "" | No | ANS API secret (migrated to Secrets Manager) |
| `registration_webhook_auth_token` | string | "" | No | Webhook auth token (migrated to Secrets Manager) |
| `registration_gate_auth_credential` | string | "" | No | Gate auth credential (migrated to Secrets Manager) |
| `registration_gate_oauth2_client_secret` | string | "" | No | OAuth2 client secret (migrated to Secrets Manager) |
| `auth0_management_api_token` | string | "" | No (when auth0 disabled) | Auth0 management token (migrated to Secrets Manager) |

### Deployment Surface Checklist

- [ ] `terraform/aws-ecs/modules/mcp-gateway/variables.tf` - Mark variables as sensitive
- [ ] `terraform/aws-ecs/modules/mcp-gateway/secrets.tf` - Add new secret resources
- [ ] `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf` - Update container definitions
- [ ] `terraform/aws-ecs/modules/mcp-gateway/iam.tf` - Update IAM policy for new secrets
- [ ] `terraform/aws-ecs/terraform.tfvars.example` - Document variables
- [ ] `docs/` - Update deployment documentation

## New Dependencies

This change uses only existing dependencies:
- `hashicorp/aws` provider (already in use)
- No new external packages required

## Implementation Details

### Step-by-Step Plan

#### Step 1: Add Secrets Manager Secrets
**File:** `terraform/aws-ecs/modules/mcp-gateway/secrets.tf`
**Lines:** Append after existing secrets (~line 377)

Add 10 new secret resources following existing patterns:
- Use `aws_secretsmanager_secret` resource
- Use `aws_secretsmanager_secret_version` for initial value
- Use same KMS key and tag conventions as existing secrets

#### Step 2: Update Container Definitions
**File:** `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf`
**Lines:**
- Auth Server container: ~lines 236-284 (environment), ~lines 413-480 (secrets block)
- Registry container: ~lines 947-980 (environment), ~lines 1288-1362 (secrets block)

Move the following from `environment` array to `secrets` array:
- `REGISTRY_API_TOKEN` -> `registry_api_token` secret
- `REGISTRY_API_KEYS` -> `registry_api_keys` secret
- `FEDERATION_STATIC_TOKEN` -> `federation_static_token` secret
- `FEDERATION_ENCRYPTION_KEY` -> `federation_encryption_key` secret
- `ANS_API_KEY` -> `ans_api_key` secret
- `ANS_API_SECRET` -> `ans_api_secret` secret
- `REGISTRATION_WEBHOOK_AUTH_TOKEN` (registry only) -> secret
- `REGISTRATION_GATE_AUTH_CREDENTIAL` (registry only) -> secret
- `REGISTRATION_GATE_OAUTH2_CLIENT_SECRET` (registry only) -> secret

**Note:** Keep old environment variables UNCHANGED for backward compatibility initially. Remove after validating deployment.

#### Step 3: Update IAM Policy
**File:** `terraform/aws-ecs/modules/mcp-gateway/iam.tf`
**Lines:** ~lines 15-36

Add new secret ARNs to the `Resource` list in `ecs_secrets_access` policy:
```hcl
Resource = concat(
  [
    # ... existing secrets ...
    aws_secretsmanager_secret.registry_api_token.arn,
    aws_secretsmanager_secret.registry_api_keys.arn,
    aws_secretsmanager_secret.federation_static_token.arn,
    aws_secretsmanager_secret.federation_encryption_key.arn,
    aws_secretsmanager_secret.ans_api_key.arn,
    aws_secretsmanager_secret.ans_api_secret.arn,
    aws_secretsmanager_secret.registration_webhook_auth_token.arn,
    aws_secretsmanager_secret.registration_gate_auth_credential.arn,
    aws_secretsmanager_secret.registration_gate_oauth2_client_secret.arn,
  ],
  var.auth0_enabled ? [aws_secretsmanager_secret.auth0_management_api_token[0].arn] : []
)
```

#### Step 4: Update Variables (Mark as Sensitive)
**File:** `terraform/aws-ecs/modules/mcp-gateway/variables.tf`
**Lines:** Find each variable block

Add `sensitive = true` to each variable definition to prevent plaintext in Terraform plan/output.

#### Step 5: Documentation Updates
**Files:** Update relevant docs in `docs/` directory if any

### Error Handling
- Terraform will validate that secret values are provided
- If a secret value is empty, create empty secret (same as existing pattern)
- IAM policy updates are atomic with secret creation

### Logging
- No application-level logging changes required
- Terraform plan will show secret ARNs instead of plaintext values

## Observability

### Secrets Manager Monitoring
- Use existing CloudWatch metrics for Secrets Manager
- Enable AWS Config rules for secret access auditing (existing)
- No new custom metrics required

## Scaling Considerations
- Secrets Manager supports unlimited secrets (per account limits apply)
- ECS task execution role already has appropriate permissions
- No performance impact on container startup (secrets retrieved in parallel)

## File Changes

### New Files
None

### Modified Files

| File Path | Lines | Change Description |
|-----------|-------|--------------------|
| `terraform/aws-ecs/modules/mcp-gateway/secrets.tf` | ~+400 | Add 10 new secret resources |
| `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf` | ~+50 | Add secrets to container definitions |
| `terraform/aws-ecs/modules/mcp-gateway/iam.tf` | ~+15 | Add new secret ARNs to IAM policy |
| `terraform/aws-ecs/modules/mcp-gateway/variables.tf` | ~+20 | Mark variables as sensitive |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| New code | ~450 |
| New tests | ~0 (infrastructure) |
| Modified code | ~70 |
| **Total** | **~520** |

## Testing Strategy
Pointer to testing.md - the full plan lives there.

## Alternatives Considered

### Alternative 1: Use AWS Parameter Store
**Description:** Use AWS Systems Manager Parameter Store instead of Secrets Manager
**Pros:** Lower cost, simpler rotation
**Cons:** Not recommended for secrets (designed for configuration)
**Why Rejected:** Issue #1134 specifically requires Secrets Manager

### Alternative 2: Inline Secrets in Task Definition
**Description:** Pass secrets directly in task definition without Secrets Manager
**Pros:** Simpler migration
**Cons:** Still exposes secrets in CloudWatch logs
**Why Rejected:** Does not solve the core security problem

## Rollout Plan

**Phase 1: Implementation**
- Add Secrets Manager resources
- Update container definitions (add secrets, keep env vars)
- Update IAM policy

**Phase 2: Validation**
- Run `terraform plan` to verify changes
- Deploy to staging environment
- Verify all services start correctly with new secrets

**Phase 3: Cleanup**
- Remove deprecated environment variables
- Run final validation
- Update documentation

## Open Questions
- Should empty secrets be allowed or require validation?
- How to handle migration for existing deployments (need special handling)?

## References
- [ECS Task Definition Secrets](https://docs.aws.amazon.com/AmazonECS/latest/userguide/specifying-sensitive-data-secrets.html)
- [Secrets Manager](https://docs.aws.amazon.com/secretsmanager/latest/userguide/)
- Issue #1134: Original issue for this work