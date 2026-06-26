# Low-Level Design: Migrate Sensitive ECS Environment Variables to AWS Secrets Manager

*Created: 2026-06-25*
*Author: Qwen Qwen3 Coder Next*
*Status: Draft*

## Table of Contents
1. [Overview](#overview)
2. [Codebase Analysis](#codebase-analysis)
3. [Architecture](#architecture)
4. [Data Models](#data-models)
5. [Implementation Details](#implementation-details)
6. [Observability](#observability)
7. [Scaling Considerations](#scaling-considerations)
8. [File Changes](#file-changes)
9. [Testing Strategy](#testing-strategy)
10. [Alternatives Considered](#alternatives-considered)
11. [Rollout Plan](#rollout-plan)

## Overview
### Problem Statement
The current implementation stores sensitive configuration values (API keys, passwords, client secrets, tokens) as plaintext environment variables in ECS task definitions. These secrets are:
- Visible in ECS task definition JSON in AWS Console
- Stored in Terraform state files
- Accessible to users with DescribeTaskDefinition permissions
- Potentially exposed in logs

### Goals
- Migrate all sensitive environment variables to AWS Secrets Manager
- Implement the `secrets` block in ECS task definitions for secret injection
- Update IAM policies to allow secret retrieval
- Maintain backward compatibility during migration

### Non-Goals
- Not implementing automatic secret rotation
- Not changing application code to use different secret retrieval
- Not modifying Keycloak database password (handled by separate RDS IAM auth migration)

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `terraform/aws-ecs/keycloak-ecs.tf` | Keycloak ECS task definition | Contains Keycloak admin credentials in plaintext environment variables |
| `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf` | Main ECS services (auth, registry, mcpgw) | Contains numerous plaintext secrets in environment blocks |
| `terraform/aws-ecs/modules/mcp-gateway/variables.tf` | Module variables definition | Contains sensitive variables that should use Secrets Manager |
| `terraform/aws-ecs/modules/mcp-gateway/secrets.tf` | Existing Secrets Manager resources | Shows current pattern for secrets management |

### Existing Patterns Identified

1. **Secrets Manager Pattern for Keycloak** (keycloak-ecs.tf):
   - Keycloak already uses Secrets Manager for sensitive values:
     - `KEYCLOAK_ADMIN` -> SSM parameter
     - `KEYCLOAK_ADMIN_PASSWORD` -> SSM parameter
     - `KC_DB_URL` -> SSM parameter
     - `KC_DB_USERNAME` and `KC_DB_PASSWORD` -> Secrets Manager secret
   - The `keycloak_container_secrets` block shows the correct pattern:
     ```tf
     keycloak_container_secrets = [
       {
         name      = "KEYCLOAK_ADMIN"
         valueFrom = aws_ssm_parameter.keycloak_admin.arn
       }
     ]
     ```

2. **IAM Policy Pattern**:
   - Task execution roles use `aws_iam_role_policy` for secret access
   - Policy includes `secretsmanager:GetSecretValue` action

3. **Secrets Manager ARN Format**:
   - For secrets with JSON properties: `${aws_secretsmanager_secret.name.arn}:property::`
   - For simple secrets: `aws_secretsmanager_secret.name.arn`

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| ECS Task Definitions | Uses | `secrets` block to inject secrets from Secrets Manager |
| IAM Role Policy | Extends | Add `secretsmanager:GetSecretValue` permissions |
| Secrets Manager | Creates | New secrets for sensitive configuration values |
| Terraform Variables | Updates | Mark sensitive inputs with `sensitive = true` |

### Constraints and Limitations Discovered
- **SSM vs Secrets Manager**: Keycloak currently uses SSM Parameter Store for some secrets (admin credentials) and Secrets Manager for others (database credentials). We should standardize on Secrets Manager for new secrets.
- **Existing Configuration**: Some variables already have `sensitive = true` in Terraform (e.g., `embeddings_api_key`, `entra_client_secret`) but are still passed as environment variables.
- **Task Definition Updates**: ECS task definition changes require new revision deployments, causing brief service disruption during rolling updates.

## Architecture

### System Context Diagram
```
┌─────────────────────────────────────────────────────────────────────┐
│                     AWS Secrets Manager                              │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐      │
│  │  Keycloak Admin │  │  Auth0 Secrets  │  │  Registry       │      │
│  │  Credentials    │  │  (Client        │  │  Embeddings API │      │
│  │  (SSM → SM)     │  │   Secrets)      │  │  Key            │      │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘      │
│           │                    │                    │                │
└───────────┼────────────────────┼────────────────────┼────────────────┘
            │                    │                    │
            │ secretsmanager:    │                    │
            │ GetSecretValue     │                    │
            │                    │                    │
┌───────────┴────────────────────┴────────────────────┴─────────────────┐
│                        ECS Task Executing                             │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │  Container (auth-server, registry, mcpgw, keycloak)              │  │
│  │  ┌───────────────────────────────────────────────────────────┐   │  │
│  │  │  Environment Variables (non-sensitive)                      │   │  │
│  │  │  * REGISTRY_URL                                             │   │  │
│  │  │  * AUTH_PROVIDER                                            │   │  │
│  │  │  * ...                                                      │   │  │
│  │  └───────────────────────────────────────────────────────────┘   │  │
│  │  ┌───────────────────────────────────────────────────────────┐   │  │
│  │  │  Secrets (injected by ECS from Secrets Manager)             │   │  │
│  │  │  * KEYCLOAK_CLIENT_SECRET                                   │   │  │
│  │  │  * EMBEDDINGS_API_KEY                                       │   │  │
│  │  │  * ...                                                      │   │  │
│  │  └───────────────────────────────────────────────────────────┘   │  │
│  └─────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────────┘
```

### Sequence Diagram
```
User/Developer    Terraform    Secrets Manager    ECS Task    Application
     |               |                |               |            |
     |               |                |               |            |
     | 1. terraform  |                |               |            |
     |  apply()       |                |               |            |
     |-------------->|                |               |            |
     |               |                |               |            |
     |               | 2. Create      |               |            |
     |               |  secrets via   |               |            |
     |               |  aws_           |               |            |
     |               |  secretsmanager│               |            |
     |               |  _secret()     |               |            |
     |               |--------------->|               |            |
     |               |                |               |            |
     |               | 3. IAM policy  |               |            |
     |               |  updated with  |               |            |
     |               |  GetSecretVal  |               |            |
     |               |  ue permis-    |               |            |
     |               |  sion          |               |            |
     |               |--------------->|               |            |
     |               |                |               |            |
     |               | 4. Task def    |               |            |
     |               |  updated with  |               |            |
     |               |  secrets block |               |            |
     |               |--------------->|               |            |
     |               |                |               |            |
     | 5. Service     |               |               |            |
     |  starts/re     |               |               |            |
     |  deploys       |               |               |            |
     |-------------->|               |               |            |
     |               |               |               |            |
     |               | 6. ECS re-    |               |            |
     |               | trieves se-   |               |            |
     |               |  cret from     |               |            |
     |               |  Secrets Man-  |               |            |
     |               |  ager          |               |            |
     |               |-------------->|               |            |
     |               |               |               |            |
     |               | 7. Secret     |               |            |
     |               |  injected as  |               |            |
     |               |  env var in    |               |            |
     |               |  container     |               |            |
     |               |<--------------|               |            |
     |               |               |               |            |
     |               | 8. App reads  |               |            |
     |               |  secret from   |               |            |
     |               |  environment   |               |----------->|
     |               |               |               |            |
```

### Component Diagram
```
┌─────────────────────────────────────────────────────────────────────────┐
│                    Terraform Module (mcp-gateway)                        │
│                                                                           │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  Variables (sensitive = true)                                     │   │
│  │  * keycloak_admin_password                                        │   │
│  │  * entra_client_secret                                            │   │
│  │  * okta_api_token                                                 │   │
│  │  * embeddings_api_key                                             │   │
│  │  * ...                                                            │   │
│  └───────────────────────┬──────────────────────────────────────────┘   │
│                          │                                                │
│                          ▼                                                │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  Secrets Manager Resources (aws_secretsmanager_secret)            │   │
│  │  * secret_key                                                     │   │
│  │  * keycloak_client_secret                                         │   │
│  │  * entra_client_secret [conditional]                              │   │
│  │  * okta_api_token [conditional]                                   │   │
│  │  * embeddings_api_key                                             │   │
│  │  * ...                                                            │   │
│  └───────────────────────┬──────────────────────────────────────────┘   │
│                          │                                                │
│                          ▼                                                │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  IAM Policy (aws_iam_role_policy)                                 │   │
│  │  Action: secretsmanager:GetSecretValue                            │   │
│  │  Resource: [all secrets ARNs]                                     │   │
│  └───────────────────────┬──────────────────────────────────────────┘   │
│                          │                                                │
│                          ▼                                                │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  ECS Task Definition                                              │   │
│  │  container_definitions:                                           │   │
│  │    secrets: [                                                      │   │
│  │      {                                                             │   │
│  │        name: "SECRET_KEY",                                         │   │
│  │        valueFrom: aws_secretsmanager_secret.secret_key.arn        │   │
│  │      }                                                             │   │
│  │      ...                                                           │   │
│  │    ]                                                               │   │
│  └──────────────────────────────────────────────────────────────────┘   │
└───────────────────────────────────────────────────────────────────────────┘
```

## Data Models

### New Secrets Manager Secret Structure
Each sensitive configuration value will be stored as a Secrets Manager secret:

```python
# Terraform resource structure
resource "aws_secretsmanager_secret" "name" {
  name        = "mcp-gateway-${var.name_prefix}-${resource_name}"
  description = "Secret for {description}"
  
  tags = local.common_tags
}

resource "aws_secretsmanager_secret_version" "name" {
  secret_id = aws_secretsmanager_secret.name.id
  secret_string = jsonencode({
    value = var.secret_value
  })
}
```

For simple string secrets (most cases), we use:
```tf
resource "aws_secretsmanager_secret" "name" {
  name        = "mcp-gateway-${var.name}-${resource_name}"
  description = "Secret for {description}"
  tags        = local.common_tags
}

# Store directly from variable
resource "aws_secretsmanager_secret_version" "name" {
  secret_id = aws_secretsmanager_secret.name.id
  secret_string = var.secret_value
}
```

### Existing Patterns to Follow

From `keycloak-ecs.tf` (already implemented):
```tf
resource "aws_secretsmanager_secret" "keycloak_db_secret" {
  name                    = "keycloak-db-secret-${var.aws_region}"
  description             = "Keycloak database credentials (username/password)"
  recover_in_days         = 30
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "keycloak_db_secret" {
  secret_id = aws_secretsmanager_secret.keycloak_db_secret.id
  secret_string = jsonencode({
    username = var.keycloak_database_username
    password = var.keycloak_database_password
  })
}
```

## Implementation Details

### Step-by-Step Plan (for a future implementer)

#### Step 1: Create Secrets Manager Resources

**File:** `terraform/aws-ecs/modules/mcp-gateway/secrets.tf` (new or modify existing)

For each sensitive variable identified in `variables.tf`, create a Secrets Manager secret:

```tf
# Secret for auth0_management_api_token
resource "aws_secretsmanager_secret" "auth0_management_api_token" {
  name        = "mcp-gateway-${var.name}-auth0-management-api-token"
  description = "Auth0 Management API token for user/group management"
  tags        = local.common_tags
}

# Secret for okta_api_token
resource "aws_secretsmanager_secret" "okta_api_token" {
  name        = "mcp-gateway-${var.name}-okta-api-token"
  description = "Okta API token for management operations"
  tags        = local.common_tags
}

# Secret for embeddings_api_key
resource "aws_secretsmanager_secret" "embeddings_api_key" {
  name        = "mcp-gateway-${var.name}-embeddings-api-key"
  description = "API key for embeddings provider (OpenAI, Anthropic, etc.)"
  tags        = local.common_tags
}
```

**Note:** Existing secrets should be preserved and existing secret references updated rather than recreated.

#### Step 2: Update ECS Task Definition Secrets Block

**File:** `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf`

Update each service's container definition to add secrets for previously plaintext values:

**For auth-server:**
```tf
# In module "ecs_service_auth" container_definitions.auth-server secrets block
secrets = concat(
  [
    {
      name      = "SECRET_KEY"
      valueFrom = aws_secretsmanager_secret.secret_key.arn
    },
    # ... existing secrets ...
  ],
  # New secrets from variables
  var.auth0_enabled ? [
    {
      name      = "AUTH0_ADMINISTRATION_API_TOKEN"
      valueFrom = aws_secretsmanager_secret.auth0_administration_api_token.arn
    }
  ] : [],
  var.okta_enabled ? [
    {
      name      = "OKTA_API_TOKEN"
      valueFrom = aws_secretsmanager_secret.okta_api_token.arn
    }
  ] : [],
  # ... other conditionals ...
)
```

**For registry:**
```tf
# In module "ecs_service_registry" container_definitions.registry secrets block
secrets = concat(
  [
    {
      name      = "SECRET_KEY"
      valueFrom = aws_secretsmanager_secret.secret_key.arn
    },
    # ... existing secrets ...
  ],
  # New secrets
  var.auth0_enabled ? [
    {
      name      = "AUTH0_ADMINISTRATION_API_TOKEN"
      valueFrom = aws_secretsmanager_secret.auth0_administration_api_token.arn
    }
  ] : [],
  # ... other conditionals ...
)
```

#### Step 3: Update IAM Task Execution Role Policies

**File:** `terraform/aws-ecs/modules/mcp-gateway/iam.tf` (modify)

Update the Secrets Manager access policy:

```tf
resource "aws_iam_role_policy" "ecs_secrets_access" {
  name = "${local.name_prefix}-ecs-secrets-access"
  role = aws_iam_role.ecs_task_exec_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        Resource = [
          # Existing secrets
          aws_secretsmanager_secret.secret_key.arn,
          aws_secretsmanager_secret.keycloak_client_secret.arn,
          # ... existing ARNs ...
          
          # New secrets to be added
          aws_secretsmanager_secret.auth0_administration_api_token.arn,
          aws_secretsmanager_secret.okta_api_token.arn,
          aws_secretsmanager_secret.embeddings_api_key.arn,
          # ... new ARNs ...
        ]
      }
    ]
  })
}
```

#### Step 4: Update Terraform Variables

**File:** `terraform/aws-ecs/modules/mcp-gateway/variables.tf`

Update sensitive variables to note they are now stored in Secrets Manager:

```tf
# Keycloak Admin Credentials
variable "keycloak_admin_password" {
  description = "Keycloak admin password for Management API user/group operations. Stored in AWS Secrets Manager."
  type        = string
  sensitive   = true
  default     = ""  # Leave empty after migration; secret stored in Secrets Manager
}
```

#### Step 5: Keycloak Migration (Special Case)

**File:** `terraform/aws-ecs/keycloak-ecs.tf`

The Keycloak service already uses a mix of SSM Parameter Store and Secrets Manager. For full migration:

1. Create Secrets Manager secrets for any remaining SSM parameters
2. Update `keycloak_container_secrets` to use Secrets Manager ARNs
3. Remove unnecessary SSM parameter resources if they're no longer needed by any service

## Observability
### Tracing / Metrics / Logging Points
- CloudWatch Logs: Monitor ECS task start for secret injection errors
- AWS Secrets Manager: Track secret access via CloudTrail
- IAM Access Analyzer: Monitor secret access patterns

## Scaling Considerations
- Secrets Manager read operations are highly scalable (10,000+ RPS per secret)
- ECS task definitions with secrets don't impact runtime secret retrieval performance
- Consider using Secrets Manager caching client for applications that read secrets frequently

## File Changes

### New Files

| File Path | Description |
|-----------|-------------|
| `terraform/aws-ecs/modules/mcp-gateway/secrets.tf` | Secrets Manager secret resources for all sensitive configuration values |

### Modified Files

| File Path | Lines | Change Description |
|-----------|-------|--------------------|
| `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf` | ~100 | Add Secrets Manager secret references to container definitions |
| `terraform/aws-ecs/modules/mcp-gateway/iam.tf` | ~50 | Update IAM policy to include new secret ARNs |
| `terraform/aws-ecs/modules/mcp-gateway/variables.tf` | ~10 | Add comments noting Secrets Manager storage for sensitive variables |
| `terraform/aws-ecs/keycloak-ecs.tf` | ~30 | Update Keycloak to use Secrets Manager for remaining SSM parameters |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| New secrets resources | ~100 |
| Updated task definitions | ~80 |
| Updated IAM policies | ~50 |
| Documentation updates | ~20 |
| **Total** | **~250** |

## Testing Strategy
**See `testing.md`** for comprehensive testing plan covering:
- Functional tests for secret injection
- Backwards compatibility tests
- Deployment surface tests
- IAM permission verification

## Alternatives Considered

### Alternative 1: Keep Using SSM Parameter Store
**Description:** Continue storing secrets in SSM Parameter Store with `SecureString` type.

**Pros:**
- Already implemented for Keycloak
- No additional cost for small number of parameters

**Cons:**
- Less granular access control
- No native secret rotation
- Parameters visible in SSM Console

**Why Rejected:** Secrets Manager provides better security features and is the recommended AWS secret storage solution.

### Alternative 2: Separate Secrets Per-Secret Property
**Description:** Store each secret property as its own secret (e.g., separate secrets for username and password).

**Pros:**
- Fine-grained access control per property

**Cons:**
- More complex management
- Higher cost (secrets are billed per secret)
- More difficult to rotate

**Why Rejected:** Single secret per configuration value is simpler and more maintainable.

### Alternative 3: Application-Level Secret Retrieval
**Description:** Modify application code to retrieve secrets from Secrets Manager at runtime.

**Pros:**
- Runtime secret rotation support

**Cons:**
- Requires code changes across all languages (Python, Node.js, etc.)
- Higher risk of implementation errors
- Not necessary since ECS handles secret injection

**Why Rejected:** ECS automatically injects secrets as environment variables, eliminating the need for application changes.

### Comparison Matrix

| Criteria | Current (SSM/Env) | Secrets Manager (Chosen) | Secrets Manager + App Code |
|----------|-------------------|--------------------------|----------------------------|
| Security | Medium | High | High |
| Complexity | Low | Low | High |
| Cost | Low | Low-Medium | Low-Medium |
| Rotation Support | None | Built-in | Built-in |
| Code Changes | None | None | Required |
| AWS Recommendation | - | Recommended | Overkill |

## Rollout Plan
- **Phase 1: Implementation** (out of scope for this skill)
  - Create secrets in Secrets Manager
  - Update Terraform configurations
  - Test with non-production environment
  
- **Phase 2: Testing**
  - Deploy to staging environment
  - Verify services start correctly
  - Test secret rotation (optional)

- **Phase 3: Deployment**
  - Deploy to production during maintenance window
  - Monitor for errors
  - Verify secrets are not visible in task definition

- **Phase 4: Cleanup** (optional, after verification)
  - Remove old plaintext environment variables
  - Update documentation

## Open Questions
1. Should we use `tfvars` files with encrypted secret values or prompt for secrets at runtime?
2. Do we need to support cross-region secret access?
3. Should we enable automatic secret rotation for any secrets?

## References
- [AWS Secrets Manager Documentation](https://docs.aws.amazon.com/secretsmanager/)
- [ECS Task Definitions - Secrets](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task_definition_secrets.html)
- Issue #1134: Original issue for this migration
- Issue #1303: RDS IAM authentication migration (separate task)
