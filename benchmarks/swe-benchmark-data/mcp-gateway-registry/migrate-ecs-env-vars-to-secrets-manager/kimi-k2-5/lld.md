# Low-Level Design: Migrate Sensitive ECS Environment Variables to AWS Secrets Manager

*Created: 2026-06-25*
*Author: Claude*
*Status: Draft*

## Table of Contents
1. [Overview](#overview)
2. [Codebase Analysis](#codebase-analysis)
3. [Architecture](#architecture)
4. [Data Models](#data-models)
5. [Implementation Details](#implementation-details)
6. [Observability](#observability)
7. [File Changes](#file-changes)
8. [Testing Strategy](#testing-strategy)
9. [Alternatives Considered](#alternatives-considered)
10. [Rollout Plan](#rollout-plan)

## Overview

### Problem Statement
The MCP Gateway Registry's ECS task definitions currently expose sensitive credentials (API tokens, encryption keys, passwords) as plaintext environment variables. This violates security best practices and complicates secret rotation.

### Goals
- Centralize all secrets in AWS Secrets Manager
- Eliminate plaintext credential exposure in ECS console and Terraform state
- Enable secret rotation without application redeployment
- Maintain least-privilege IAM access patterns

### Non-Goals
- Automated secret rotation (future phase)
- Migration of non-sensitive configuration
- Changes to application code (container image remains unchanged)

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `terraform/aws-ecs/modules/mcp-gateway/secrets.tf` | Secrets Manager resources | Add new secrets following existing pattern |
| `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf` | ECS task definitions | Move secrets from `environment` to `secrets` block |
| `terraform/aws-ecs/modules/mcp-gateway/iam.tf` | IAM policies | Update `ecs_secrets_access` policy |
| `terraform/aws-ecs/modules/mcp-gateway/variables.tf` | Module inputs | Mark sensitive vars and add new secret vars |
| `terraform/aws-ecs/variables.tf` | Root variables | Mark sensitive appropriately |

### Existing Patterns Identified

1. **Secrets Manager Resource Pattern**:
   ```tf
   resource "aws_secretsmanager_secret" "example" {
     name_prefix             = "${local.name_prefix}-example-"
     description             = "Description"
     recovery_window_in_days = 0
     kms_key_id              = aws_kms_key.secrets.id
     tags                    = local.common_tags
   }

   resource "aws_secretsmanager_secret_version" "example" {
     secret_id     = aws_secretsmanager_secret.example.id
     secret_string = var.example_value

     lifecycle {
       ignore_changes = [secret_string]  # For manually-rotated secrets
     }
   }
   ```

2. **ECS Secrets Block Pattern**:
   ```tf
   secrets = concat(
     [
       {
         name      = "SECRET_NAME"
         valueFrom = aws_secretsmanager_secret.example.arn
       }
     ],
     var.conditional ? [
       {
         name      = "OPTIONAL_SECRET"
         valueFrom = aws_secretsmanager_secret.optional[0].arn
       }
     ] : []
   )
   ```

3. **IAM Policy Pattern**:
   ```tf
   resource "aws_iam_policy" "ecs_secrets_access" {
     policy = jsonencode({
       Statement = [
         {
           Action = ["secretsmanager:GetSecretValue"]
           Resource = concat(
             [aws_secretsmanager_secret.existing.arn],
             var.enabled ? [aws_secretsmanager_secret.new[0].arn] : []
           )
         }
       ]
     })
   }
   ```

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| ECS Service | Uses | Secrets Manager via `secrets` block |
| IAM Policy | Allows | `secretsmanager:GetSecretValue` for task execution role |
| KMS Key | Encrypts | All secrets use existing `aws_kms_key.secrets` |

### Constraints and Limitations Discovered

1. **Checkov Skip Annotations**: Existing secrets use `#checkov:skip=CKV2_AWS_57` for application-managed secrets
2. **Count Meta-Argument**: Optional secrets use `count = var.enabled ? 1 : 0` pattern
3. **Plaintext Fallback**: Some secrets (like `embeddings_api_key`) already handle both plaintext and Secrets Manager variants

## Architecture

### System Context Diagram

```
                    +-------------------+
                    |  Terraform Module |
                    |  mcp-gateway      |
                    +---------+---------+
                              |
            +-----------------+-----------------+
            |                                   |
   +--------v---------+              +---------v--------+
   | Secrets Manager  |              | ECS Task Def     |
   | Resources        |              | secrets block    |
   | (aws_secretsman- |              | references ARNs  |
   |  ager_secret)    |              +---------+--------+
   +--------+---------+                        |
            |                        +--------v--------+
            |                        | ECS Fargate     |
            |                        | (injects env    |
            |                        |  vars at launch)|
            |                        +--------+--------+
            |                                 |
            +---------------------------------+
                            |
                    +-------v--------+
                    | Application    |
                    | Containers     |
                    +----------------+
```

### Sequence Diagram

```
Terraform Apply:
1. Create Secrets Manager resources (if not exists)
2. Update IAM policy to include new secret ARNs
3. Update ECS task definition to use secrets block
4. ECS Service deploys new task revision

ECS Task Startup:
1. Fargate agent assumes task execution role
2. Fargate calls Secrets Manager:GetSecretValue for each secret
3. Secrets injected as environment variables in container
4. Application reads secrets from env vars (unchanged)
```

## Data Models

### New Secrets Manager Resources

#### 1. Auth0 Management API Token
```tf
#checkov:skip=CKV2_AWS_57:Managed in Auth0 dashboard, not rotatable via Secrets Manager
resource "aws_secretsmanager_secret" "auth0_management_api_token" {
  count = var.auth0_enabled && var.auth0_management_api_token != "" ? 1 : 0
  name_prefix             = "${local.name_prefix}-auth0-mgmt-token-"
  description             = "Auth0 Management API Token for IAM operations"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "auth0_management_api_token" {
  count = var.auth0_enabled && var.auth0_management_api_token != "" ? 1 : 0
  secret_id     = aws_secretsmanager_secret.auth0_management_api_token[0].id
  secret_string = var.auth0_management_api_token
  lifecycle { ignore_changes = [secret_string] }
}
```

#### 2. Registry API Token
```tf
#checkov:skip=CKV2_AWS_57:Application-managed API token - rotation requires coordinated update
resource "aws_secretsmanager_secret" "registry_api_token" {
  count = var.registry_static_token_auth_enabled && var.registry_api_token != "" ? 1 : 0
  name_prefix             = "${local.name_prefix}-registry-api-token-"
  description             = "Static API token for registry network-trusted auth"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "registry_api_token" {
  count = var.registry_static_token_auth_enabled && var.registry_api_token != "" ? 1 : 0
  secret_id     = aws_secretsmanager_secret.registry_api_token[0].id
  secret_string = var.registry_api_token
  lifecycle { ignore_changes = [secret_string] }
}
```

#### 3. Registry API Keys (JSON)
```tf
#checkov:skip=CKV2_AWS_57:Application-managed API keys config
resource "aws_secretsmanager_secret" "registry_api_keys" {
  count = var.registry_static_token_auth_enabled && var.registry_api_keys != "" ? 1 : 0
  name_prefix             = "${local.name_prefix}-registry-api-keys-"
  description             = "JSON configuration for multiple registry API keys"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "registry_api_keys" {
  count = var.registry_static_token_auth_enabled && var.registry_api_keys != "" ? 1 : 0
  secret_id     = aws_secretsmanager_secret.registry_api_keys[0].id
  secret_string = var.registry_api_keys
  lifecycle { ignore_changes = [secret_string] }
}
```

#### 4. Federation Static Token
```tf
#checkov:skip=CKV2_AWS_57:Federation peer auth token - rotation requires peer coordination
resource "aws_secretsmanager_secret" "federation_static_token" {
  count = var.federation_static_token_auth_enabled && var.federation_static_token != "" ? 1 : 0
  name_prefix             = "${local.name_prefix}-federation-static-token-"
  description             = "Static bearer token for federation peer authentication"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "federation_static_token" {
  count = var.federation_static_token_auth_enabled && var.federation_static_token != "" ? 1 : 0
  secret_id     = aws_secretsmanager_secret.federation_static_token[0].id
  secret_string = var.federation_static_token
  lifecycle { ignore_changes = [secret_string] }
}
```

#### 5. Federation Encryption Key
```tf
#checkov:skip=CKV2_AWS_57:Fernet encryption key - rotation requires re-encrypting stored tokens
resource "aws_secretsmanager_secret" "federation_encryption_key" {
  count = var.federation_encryption_key != "" ? 1 : 0
  name_prefix             = "${local.name_prefix}-federation-encryption-key-"
  description             = "Fernet encryption key for federation token storage"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "federation_encryption_key" {
  count = var.federation_encryption_key != "" ? 1 : 0
  secret_id     = aws_secretsmanager_secret.federation_encryption_key[0].id
  secret_string = var.federation_encryption_key
  lifecycle { ignore_changes = [secret_string] }
}
```

#### 6. ANS API Credentials
```tf
#checkov:skip=CKV2_AWS_57:Third-party API credentials
resource "aws_secretsmanager_secret" "ans_api_key" {
  count = var.ans_integration_enabled && var.ans_api_key != "" ? 1 : 0
  name_prefix             = "${local.name_prefix}-ans-api-key-"
  description             = "ANS (Agent Name Service) API key"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "ans_api_key" {
  count = var.ans_integration_enabled && var.ans_api_key != "" ? 1 : 0
  secret_id     = aws_secretsmanager_secret.ans_api_key[0].id
  secret_string = var.ans_api_key
  lifecycle { ignore_changes = [secret_string] }
}

resource "aws_secretsmanager_secret" "ans_api_secret" {
  count = var.ans_integration_enabled && var.ans_api_secret != "" ? 1 : 0
  name_prefix             = "${local.name_prefix}-ans-api-secret-"
  description             = "ANS (Agent Name Service) API secret"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "ans_api_secret" {
  count = var.ans_integration_enabled && var.ans_api_secret != "" ? 1 : 0
  secret_id     = aws_secretsmanager_secret.ans_api_secret[0].id
  secret_string = var.ans_api_secret
  lifecycle { ignore_changes = [secret_string] }
}
```

#### 7. Registration Webhook Auth Token
```tf
#checkov:skip=CKV2_AWS_57:Webhook auth token - managed by external system
resource "aws_secretsmanager_secret" "registration_webhook_auth_token" {
  count = var.registration_webhook_url != "" && var.registration_webhook_auth_token != "" ? 1 : 0
  name_prefix             = "${local.name_prefix}-webhook-auth-token-"
  description             = "Auth token for registration webhook calls"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "registration_webhook_auth_token" {
  count = var.registration_webhook_url != "" && var.registration_webhook_auth_token != "" ? 1 : 0
  secret_id     = aws_secretsmanager_secret.registration_webhook_auth_token[0].id
  secret_string = var.registration_webhook_auth_token
  lifecycle { ignore_changes = [secret_string] }
}
```

#### 8. Keycloak Admin Credentials (JSON)
```tf
#checkov:skip=CKV2_AWS_57:Keycloak-managed credentials
resource "aws_secretsmanager_secret" "keycloak_admin_credentials" {
  count = var.keycloak_admin_password != "" ? 1 : 0
  name_prefix             = "${local.name_prefix}-keycloak-admin-creds-"
  description             = "Keycloak admin username and password"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "keycloak_admin_credentials" {
  count = var.keycloak_admin_password != "" ? 1 : 0
  secret_id = aws_secretsmanager_secret.keycloak_admin_credentials[0].id
  secret_string = jsonencode({
    username = "admin"
    password = var.keycloak_admin_password
  })
  lifecycle { ignore_changes = [secret_string] }
}
```

## Implementation Details

### Step 1: Add Secrets Manager Resources (secrets.tf)

**File:** `terraform/aws-ecs/modules/mcp-gateway/secrets.tf`
**Lines:** After line 377 (after otlp_exporter_headers secret)

Add the 8 new secrets resources defined in Data Models section above.

### Step 2: Update IAM Policy (iam.tf)

**File:** `terraform/aws-ecs/modules/mcp-gateway/iam.tf`
**Lines:** 15-36

**Changes:**
Add new secret ARNs to the `Resource` list in the `ecs_secrets_access` policy:

```tf
Resource = concat(
  [
    aws_secretsmanager_secret.secret_key.arn,
    aws_secretsmanager_secret.keycloak_client_secret.arn,
    aws_secretsmanager_secret.keycloak_m2m_client_secret.arn,
    aws_secretsmanager_secret.embeddings_api_key.arn,
    aws_secretsmanager_secret.keycloak_admin_password.arn,
    # New secrets
    var.auth0_enabled && var.auth0_management_api_token != "" ? aws_secretsmanager_secret.auth0_management_api_token[0].arn : "",
    var.registry_static_token_auth_enabled && var.registry_api_token != "" ? aws_secretsmanager_secret.registry_api_token[0].arn : "",
    var.registry_static_token_auth_enabled && var.registry_api_keys != "" ? aws_secretsmanager_secret.registry_api_keys[0].arn : "",
    var.federation_static_token_auth_enabled && var.federation_static_token != "" ? aws_secretsmanager_secret.federation_static_token[0].arn : "",
    var.federation_encryption_key != "" ? aws_secretsmanager_secret.federation_encryption_key[0].arn : "",
    var.ans_integration_enabled && var.ans_api_key != "" ? aws_secretsmanager_secret.ans_api_key[0].arn : "",
    var.ans_integration_enabled && var.ans_api_secret != "" ? aws_secretsmanager_secret.ans_api_secret[0].arn : "",
    var.registration_webhook_url != "" && var.registration_webhook_auth_token != "" ? aws_secretsmanager_secret.registration_webhook_auth_token[0].arn : "",
    var.keycloak_admin_password != "" ? aws_secretsmanager_secret.keycloak_admin_credentials[0].arn : "",
  ],
  # ... existing conditional secrets
)
```

### Step 3: Update ECS Task Definitions (ecs-services.tf)

#### Auth Server Service (ecs_service_auth)

**File:** `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf`
**Lines:** 97-411 (environment block)

**Changes:**

1. **Remove** from `environment` block:
   - Lines 212-214: `AUTH0_MANAGEMENT_API_TOKEN`
   - Lines 236-238: `REGISTRY_API_TOKEN`
   - Lines 239-241: `REGISTRY_API_KEYS`
   - Lines 257-259: `FEDERATION_STATIC_TOKEN`
   - Lines 262-264: `FEDERATION_ENCRYPTION_KEY`
   - Lines 273-275: `ANS_API_KEY`
   - Lines 278-280: `ANS_API_SECRET`

2. **Update** `secrets` block (lines 413-480):

```tf
secrets = concat(
  [
    # ... existing secrets ...
  ],
  # New secrets for auth-server
  var.auth0_enabled && var.auth0_management_api_token != "" ? [
    {
      name      = "AUTH0_MANAGEMENT_API_TOKEN"
      valueFrom = aws_secretsmanager_secret.auth0_management_api_token[0].arn
    }
  ] : [],
  var.registry_static_token_auth_enabled && var.registry_api_token != "" ? [
    {
      name      = "REGISTRY_API_TOKEN"
      valueFrom = aws_secretsmanager_secret.registry_api_token[0].arn
    }
  ] : [],
  var.registry_static_token_auth_enabled && var.registry_api_keys != "" ? [
    {
      name      = "REGISTRY_API_KEYS"
      valueFrom = aws_secretsmanager_secret.registry_api_keys[0].arn
    }
  ] : [],
  var.federation_static_token_auth_enabled && var.federation_static_token != "" ? [
    {
      name      = "FEDERATION_STATIC_TOKEN"
      valueFrom = aws_secretsmanager_secret.federation_static_token[0].arn
    }
  ] : [],
  var.federation_encryption_key != "" ? [
    {
      name      = "FEDERATION_ENCRYPTION_KEY"
      valueFrom = aws_secretsmanager_secret.federation_encryption_key[0].arn
    }
  ] : [],
  var.ans_integration_enabled && var.ans_api_key != "" ? [
    {
      name      = "ANS_API_KEY"
      valueFrom = aws_secretsmanager_secret.ans_api_key[0].arn
    }
  ] : [],
  var.ans_integration_enabled && var.ans_api_secret != "" ? [
    {
      name      = "ANS_API_SECRET"
      valueFrom = aws_secretsmanager_secret.ans_api_secret[0].arn
    }
  ] : [],
  # ... existing conditional secrets ...
)
```

#### Registry Service (ecs_service_registry)

**File:** `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf`
**Lines:** 698-1286

**Changes:**

1. **Remove** from `environment` block:
   - Lines 813-815: `AUTH0_MANAGEMENT_API_TOKEN`
   - Lines 886-888: `KEYCLOAK_ADMIN` (username) - keep for now, move to secret
   - Lines 950-952: `FEDERATION_STATIC_TOKEN`
   - Lines 954-956: `FEDERATION_ENCRYPTION_KEY`
   - Lines 971-973: `ANS_API_KEY`
   - Lines 976-978: `ANS_API_SECRET`
   - Lines 1079-1081: `REGISTRY_API_TOKEN`
   - Lines 1082-1084: `REGISTRY_API_KEYS`
   - Lines 1104-1106: `REGISTRATION_WEBHOOK_AUTH_TOKEN`

2. **Add to `secrets` block** (after line 1365):

```tf
  var.auth0_enabled && var.auth0_management_api_token != "" ? [
    {
      name      = "AUTH0_MANAGEMENT_API_TOKEN"
      valueFrom = aws_secretsmanager_secret.auth0_management_api_token[0].arn
    }
  ] : [],
  var.registry_static_token_auth_enabled && var.registry_api_token != "" ? [
    {
      name      = "REGISTRY_API_TOKEN"
      valueFrom = aws_secretsmanager_secret.registry_api_token[0].arn
    }
  ] : [],
  var.registry_static_token_auth_enabled && var.registry_api_keys != "" ? [
    {
      name      = "REGISTRY_API_KEYS"
      valueFrom = aws_secretsmanager_secret.registry_api_keys[0].arn
    }
  ] : [],
  var.federation_static_token_auth_enabled && var.federation_static_token != "" ? [
    {
      name      = "FEDERATION_STATIC_TOKEN"
      valueFrom = aws_secretsmanager_secret.federation_static_token[0].arn
    }
  ] : [],
  var.federation_encryption_key != "" ? [
    {
      name      = "FEDERATION_ENCRYPTION_KEY"
      valueFrom = aws_secretsmanager_secret.federation_encryption_key[0].arn
    }
  ] : [],
  var.ans_integration_enabled && var.ans_api_key != "" ? [
    {
      name      = "ANS_API_KEY"
      valueFrom = aws_secretsmanager_secret.ans_api_key[0].arn
    }
  ] : [],
  var.ans_integration_enabled && var.ans_api_secret != "" ? [
    {
      name      = "ANS_API_SECRET"
      valueFrom = aws_secretsmanager_secret.ans_api_secret[0].arn
    }
  ] : [],
  var.registration_webhook_url != "" && var.registration_webhook_auth_token != "" ? [
    {
      name      = "REGISTRATION_WEBHOOK_AUTH_TOKEN"
      valueFrom = aws_secretsmanager_secret.registration_webhook_auth_token[0].arn
    }
  ] : [],
  var.keycloak_admin_password != "" ? [
    {
      name      = "KEYCLOAK_ADMIN_PASSWORD"
      valueFrom = "${aws_secretsmanager_secret.keycloak_admin_credentials[0].arn}:password::"
    }
  ] : [],
```

### Step 4: Update Module Variables (variables.tf)

**File:** `terraform/aws-ecs/modules/mcp-gateway/variables.tf`

Verify these existing variables have `sensitive = true`:
- `auth0_management_api_token`
- `registry_api_token`
- `registry_api_keys`
- `federation_static_token`
- `federation_encryption_key`
- `ans_api_key`
- `ans_api_secret`
- `registration_webhook_auth_token`
- `keycloak_admin_password`

### Error Handling

1. **Empty Secret Handling**: Use conditional `count` to only create secrets when values are non-empty
2. **Missing Secret at Runtime**: ECS will fail to start task if secret cannot be retrieved; CloudWatch logs will show error
3. **IAM Permission Denied**: Task will fail to start; ECS events will show "unable to pull secret" error

### Logging

- Terraform plan will NOT show sensitive values (marked with `sensitive = true`)
- ECS Console shows secrets as "Hidden" in container definition
- CloudWatch Logs show application startup messages using secrets (ensure no secret values logged)

## Observability

### Metrics

Monitor via CloudWatch:
- `SecretsManager:GetSecretValue` API calls
- ECS task start failures related to secret retrieval

### Alarms

Consider adding CloudWatch alarm for:
- High rate of Secrets Manager access denied errors
- ECS task startup failures correlated with secret retrieval

## File Changes

### New Files

None - all changes are modifications to existing files.

### Modified Files

| File Path | Lines | Change Description |
|-----------|-------|--------------------|
| `terraform/aws-ecs/modules/mcp-gateway/secrets.tf` | +160 | Add 8 new secret resources |
| `terraform/aws-ecs/modules/mcp-gateway/iam.tf` | +20 | Update IAM policy with new secret ARNs |
| `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf` | -80, +120 | Remove secrets from environment, add to secrets block |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| New Terraform resources | ~160 |
| Modified task definitions | ~40 (net change) |
| IAM policy updates | ~20 |
| **Total** | **~220** |

## Testing Strategy

### Terraform Plan Verification

1. Run `terraform plan` and verify:
   - No plaintext secrets shown in environment blocks
   - New `aws_secretsmanager_secret` resources created
   - IAM policy updated with new ARNs
   - ECS task definition changes in-place (no force recreation)

### ECS Task Definition Verification

1. After apply, check ECS Console:
   - Container definition shows secrets as "Hidden"
   - Environment variables no longer contain sensitive values

### Application Startup Test

1. Deploy to dev environment
2. Verify services start successfully
3. Check application logs for no "missing env var" errors
4. Test key functionality that uses secrets (federation, webhook, ANS)

### IAM Policy Test

1. Verify task execution role can read all secrets:
   ```bash
   aws secretsmanager get-secret-value \
     --secret-id arn:aws:secretsmanager:...:secret:mcp-gateway-... \
     --profile task-execution-role-profile
   ```

## Alternatives Considered

### Alternative 1: SSM Parameter Store
**Description:** Use AWS Systems Manager Parameter Store instead of Secrets Manager.
**Pros:** Lower cost, simpler IAM.
**Cons:** No native rotation, less granular access control.
**Why Rejected:** Secrets Manager is already the established pattern in this codebase.

### Alternative 2: External Secrets Operator (Kubernetes)
**Description:** Use EKS with External Secrets Operator.
**Pros:** Cloud-agnostic, GitOps-friendly.
**Cons:** Requires EKS migration, adds complexity.
**Why Rejected:** ECS Fargate deployment; no Kubernetes.

### Comparison Matrix

| Criteria | Secrets Manager | Parameter Store | External Secrets |
|----------|-----------------|-----------------|------------------|
| Cost | $$ | $ | $$$ |
| Rotation | Built-in | Manual | Manual |
| IAM Granularity | High | Medium | High |
| ECS Native | Yes | Yes | No |

## Rollout Plan

### Phase 1: Development Environment
- Apply changes to dev environment
- Verify all services start correctly
- Run integration tests

### Phase 2: Staging Environment
- Apply to staging
- Load test with secrets
- Verify secret rotation works (manual test)

### Phase 3: Production
- Apply during maintenance window
- Monitor ECS task startup
- Validate application functionality

## Open Questions

1. Should we add a `moved` block for Terraform state migration if secret names change?
2. Do we need to support migration from existing plaintext values without downtime?
3. Should we add validation that secrets are provided when features are enabled?

## References

- [AWS ECS Secrets Documentation](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/specifying-sensitive-data-secrets.html)
- [Terraform aws_ecs_task_definition secrets](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/ecs_task_definition#secrets)
- Existing pattern: `secrets.tf` line 104-143 (keycloak_client_secret)
