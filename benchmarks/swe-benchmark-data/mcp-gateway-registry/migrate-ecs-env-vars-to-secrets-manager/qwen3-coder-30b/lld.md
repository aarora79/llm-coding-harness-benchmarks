# Low-Level Design: Migrate ECS Environment Variables to AWS Secrets Manager

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
Plaintext secrets are currently stored as ECS environment variables in Terraform, which poses a significant security risk. This approach lacks encryption, rotation capabilities, and audit trails for sensitive data like database passwords, API keys, OAuth client secrets, and admin passwords.

### Goals
- Migrate all sensitive ECS environment variables to AWS Secrets Manager
- Maintain backward compatibility during migration through fallback to plaintext
- Ensure all ECS services can access the secrets via the `secrets` block
- Update IAM task execution roles to allow reading the new Secrets Manager secrets
- Support AWS Secrets Manager rotation and cross-account access

### Non-Goals
- Modify Helm/EKS deployment configurations (only ECS/Terraform)
- Database schema changes or migrations
- Application-level secret management (outside of ECS)

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf` | ECS service definitions | Contains environment variables and secrets block usage |
| `terraform/aws-ecs/modules/mcp-gateway/secrets.tf` | Secret resource definitions | Contains existing secret resources that need to be expanded |
| `terraform/aws-ecs/modules/mcp-gateway/iam.tf` | IAM policies | Defines access policies for secrets |
| `terraform/aws-ecs/modules/mcp-gateway/variables.tf` | Variable definitions | Contains variable declarations for secrets |
| `charts/registry/reserved-env-names.txt` | Reserved environment variables | Identifies sensitive variables that should be migrated |
| `charts/auth-server/reserved-env-names.txt` | Reserved environment variables | Identifies sensitive variables that should be migrated |
| `charts/mcpgw/reserved-env-names.txt` | Reserved environment variables | Identifies sensitive variables that should be migrated |

### Existing Patterns Identified
1. **Pattern Name**: Environment variable handling with fallbacks
   - Files: `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf`
   - How a future implementer should follow this: Use conditional logic to check if secret ARN is provided, falling back to plaintext when not available

2. **Pattern Name**: Secrets Manager secret resources  
   - Files: `terraform/aws-ecs/modules/mcp-gateway/secrets.tf`
   - How a future implementer should follow this: Define AWS Secrets Manager resources using `aws_secretsmanager_secret` and `aws_secretsmanager_secret_version` resources

3. **Pattern Name**: IAM policy with secrets access
   - Files: `terraform/aws-ecs/modules/mcp-gateway/iam.tf`
   - How a future implementer should follow this: Update IAM policies to include new secret ARNs in the resource list

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| ECS Task Definitions | Extends | Need to update environment variables to use secrets block |
| IAM Policies | Depends on | Need to update policies to allow access to new secrets |
| Terraform Variables | Uses | Need to add new variables for secret ARNs |
| Secret Rotation | Depends on | Need to ensure secrets are compatible with rotation |

### Constraints and Limitations Discovered
- Current implementation already supports a fallback mechanism where plaintext environment variables are used when secret ARNs are not provided
- Secrets Manager ARNs are already used in some services but not comprehensively
- Some variables are already handled via the secrets block in some services but not all

## Architecture

### System Context Diagram
```
┌─────────────────┐    ┌──────────────┐    ┌─────────────────┐
│   ECS Services  │◄───┤  Secrets     │◄───┤  Terraform      │
│                 │    │  Manager     │    │  Resources      │
│  Registry       │    │              │    │                 │
│  Auth Server    │    │  (Database)  │    │  (Variables)    │
│  MCPGW          │    │              │    │                 │
└─────────────────┘    └──────────────┘    └─────────────────┘
```

### Sequence Diagram
```
1. Terraform applies changes
   │
   ▼
2. Secrets Manager resources created
   │
   ▼
3. ECS services configured with secrets block
   │
   ▼
4. IAM policies updated to allow secret access
   │
   ▼
5. ECS tasks can now access secrets via secrets block
```

### Component Diagram
```
┌─────────────────────────────────────────────────────────────────────┐
│                          Terraform                                  │
├─────────────────────────────────────────────────────────────────────┤
│  Variables                                                          │
│  ├─ Secret ARN variables                                            │
│  └─ Environment variables                                           │
├─────────────────────────────────────────────────────────────────────┤
│  Resources                                                          │
│  ├─ aws_secretsmanager_secret                                       │
│  ├─ aws_secretsmanager_secret_version                               │
│  └─ aws_secretsmanager_secret_rotation                              │
├─────────────────────────────────────────────────────────────────────┤
│  Modules                                                            │
│  ├─ ecs_services (uses secrets block)                               │
│  └─ iam (updates policies)                                          │
└─────────────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        ECS Services                                 │
├─────────────────────────────────────────────────────────────────────┤
│  Task Definitions                                                   │
│  ├─ environment variables (plaintext)                              │
│  └─ secrets block (secret ARNs)                                    │
├─────────────────────────────────────────────────────────────────────┤
│  IAM Roles                                                          │
│  ├─ Task Execution Role                                            │
│  └─ Task Roles                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

## Data Models

### New Models
None - this change is primarily infrastructure focused.

### Model Changes
None - this change is primarily infrastructure focused.

## API / CLI Design

### New Endpoints / Commands
None - this is an infrastructure change, not a new API endpoint.

## Configuration Parameters

### New Environment Variables

| Variable Name | Type | Default | Required | Description |
|---------------|------|---------|----------|-------------|
| `MONGODB_CONNECTION_STRING_SECRET_ARN` | string | `""` | No | ARN of the secret containing MongoDB connection string |
| `DOCUMENTDB_CREDENTIALS_SECRET_ARN` | string | `""` | No | ARN of the secret containing DocumentDB credentials |
| `KEYCLOAK_ADMIN_PASSWORD_SECRET_ARN` | string | `""` | No | ARN of the secret containing Keycloak admin password |
| `ENTRA_CLIENT_SECRET_SECRET_ARN` | string | `""` | No | ARN of the secret containing Entra client secret |
| `OKTA_CLIENT_SECRET_SECRET_ARN` | string | `""` | No | ARN of the secret containing Okta client secret |
| `OKTA_M2M_CLIENT_SECRET_SECRET_ARN` | string | `""` | No | ARN of the secret containing Okta M2M client secret |
| `OKTA_API_TOKEN_SECRET_ARN` | string | `""` | No | ARN of the secret containing Okta API token |
| `AUTH0_CLIENT_SECRET_SECRET_ARN` | string | `""` | No | ARN of the secret containing Auth0 client secret |
| `AUTH0_M2M_CLIENT_SECRET_SECRET_ARN` | string | `""` | No | ARN of the secret containing Auth0 M2M client secret |
| `AUTH0_MANAGEMENT_API_TOKEN_SECRET_ARN` | string | `""` | No | ARN of the secret containing Auth0 management API token |
| `METRICS_API_KEY_SECRET_ARN` | string | `""` | No | ARN of the secret containing metrics API key |
| `OTLP_EXPORTER_HEADERS_SECRET_ARN` | string | `""` | No | ARN of the secret containing OTLP exporter headers |
| `EMBEDDINGS_API_KEY_SECRET_ARN` | string | `""` | No | ARN of the secret containing embeddings API key |

### Settings / Config Class Updates
```python
# No Python model changes needed for this infrastructure-focused change
```

### Deployment Surface Checklist
List every surface where this parameter must appear (`.env.example`, `docker-compose.yml`, Terraform vars, Helm values, etc.) so an implementer can tick them off later.

- [ ] Terraform variables in `variables.tf` 
- [ ] Terraform module variables in `modules/mcp-gateway/variables.tf`
- [ ] Terraform module outputs in `modules/mcp-gateway/outputs.tf`
- [ ] Secret rotation configuration in `secret-rotation-config.tf`
- [ ] IAM policy in `modules/mcp-gateway/iam.tf`
- [ ] ECS service definitions in `modules/mcp-gateway/ecs-services.tf`
- [ ] Secret resource definitions in `modules/mcp-gateway/secrets.tf`
- [ ] Secret rotation resources in `secret-rotation.tf`

## New Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| None | None | This change uses only existing dependencies. |

If no new dependencies are required, explicitly state: "This change uses only existing dependencies."

## Implementation Details

### Step-by-Step Plan (for a future implementer)

#### Step 1: Identify sensitive environment variables
**File:** `charts/registry/reserved-env-names.txt`, `charts/auth-server/reserved-env-names.txt`, `charts/mcpgw/reserved-env-names.txt`
**Lines:** All sensitive variables listed in these files

#### Step 2: Add new secret ARN variables to Terraform
**File:** `terraform/aws-ecs/modules/mcp-gateway/variables.tf`
**Lines:** ~1350-1400

```hcl
# New variables for secret ARNs
variable "mongodb_connection_string_secret_arn" {
  description = "ARN of the secret containing MongoDB connection string. When set, overrides the mongodb_connection_string variable."
  type        = string
  default     = ""
}

variable "documentdb_credentials_secret_arn" {
  description = "ARN of the secret containing DocumentDB credentials. When set, overrides the documentdb_* variables."
  type        = string
  default     = ""
}

# ... Add similar variables for other sensitive secrets
```

#### Step 3: Create new secret resources for sensitive variables
**File:** `terraform/aws-ecs/modules/mcp-gateway/secrets.tf`
**Lines:** ~90-370

```hcl
# New secret resources for sensitive variables
resource "aws_secretsmanager_secret" "keycloak_admin_password" {
  name_prefix = "${local.name_prefix}-keycloak-admin-password-"
}

resource "aws_secretsmanager_secret_version" "keycloak_admin_password" {
  secret_id     = aws_secretsmanager_secret.keycloak_admin_password.id
  secret_string = var.keycloak_admin_password
}

# ... Add similar resources for other sensitive variables
```

#### Step 4: Update IAM policies to include new secrets
**File:** `terraform/aws-ecs/modules/mcp-gateway/iam.tf`
**Lines:** ~10-50

```hcl
# Update the ecs_secrets_access policy to include new secrets
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
            aws_secretsmanager_secret.keycloak_admin_password.arn
          ],
          var.documentdb_credentials_secret_arn != "" ? [var.documentdb_credentials_secret_arn] : [],
          var.entra_enabled ? [aws_secretsmanager_secret.entra_client_secret[0].arn] : [],
          var.okta_enabled ? [
            aws_secretsmanager_secret.okta_client_secret[0].arn,
            aws_secretsmanager_secret.okta_m2m_client_secret[0].arn,
            aws_secretsmanager_secret.okta_api_token[0].arn
          ] : [],
          var.auth0_enabled ? [
            aws_secretsmanager_secret.auth0_client_secret[0].arn,
            aws_secretsmanager_secret.auth0_m2m_client_secret[0].arn
          ] : [],
          var.enable_observability ? [aws_secretsmanager_secret.metrics_api_key[0].arn] : [],
          var.enable_observability && var.otel_otlp_endpoint != "" ? [aws_secretsmanager_secret.otlp_exporter_headers[0].arn] : [],
          # NEW: Add new secret ARNs here
          var.mongodb_connection_string_secret_arn != "" ? [var.mongodb_connection_string_secret_arn] : [],
          var.keycloak_admin_password_secret_arn != "" ? [var.keycloak_admin_password_secret_arn] : []
        )
      },
      {
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:DescribeKey"
        ]
        Resource = [
          aws_kms_key.secrets.arn
        ]
      }
    ]
  })
}
```

#### Step 5: Update ECS service definitions to use secrets block
**File:** `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf`
**Lines:** ~410-480 and ~1285-1365

Update the environment variable sections to use conditional logic for secrets vs plaintext fallback:

```hcl
# Registry service environment variables (update to use secrets block)
environment = concat([
    # ... existing environment variables
    {
      name  = "MONGODB_CONNECTION_STRING"
      value = var.mongodb_connection_string
    },
    # ... more existing environment variables
  ],
  # PR #947: MongoDB connection string override (plain-text variant).
  # Only emitted when var.mongodb_connection_string is non-empty and a
  # Secrets Manager ARN was not provided. When empty, the registry
  # falls back to the DOCUMENTDB_* env vars above.
  var.mongodb_connection_string != "" && var.mongodb_connection_string_secret_arn == "" ? [
    {
      name  = "MONGODB_CONNECTION_STRING"
      value = var.mongodb_connection_string
    }
  ] : [],
  # Extra environment variables from user (Issue #1000)
  var.registry_extra_env
)

# Registry service secrets block (update to use secrets block)
secrets = concat(
  [
    {
      name      = "SECRET_KEY"
      valueFrom = aws_secretsmanager_secret.secret_key.arn
    },
    # ... existing secrets
  ],
  # PR #947: MongoDB connection string override (Secrets Manager variant).
  # Preferred when the URI contains credentials (avoids plain text in state).
  var.mongodb_connection_string_secret_arn != "" ? [
    {
      name      = "MONGODB_CONNECTION_STRING"
      valueFrom = var.mongodb_connection_string_secret_arn
    }
  ] : [],
  # ... more secret blocks
)
```

#### Step 6: Add secret rotation configuration
**File:** `terraform/aws-ecs/secret-rotation-config.tf`
**Lines:** ~10-50

```hcl
# Add new secret rotation resources for new secrets
resource "aws_secretsmanager_secret_rotation" "keycloak_admin_password" {
  secret_id           = aws_secretsmanager_secret.keycloak_admin_password.id
  rotation_lambda_arn = aws_lambda_function.secret_rotation.id
  rotation_rules {
    automatically_after_days = 90
  }
}

# ... Add similar rotation resources for other secrets
```

### Error Handling
- When secret ARNs are provided but the secrets don't exist, Terraform will fail with clear error messages
- When plaintext variables are used as fallback, existing behavior is preserved
- IAM policy errors will prevent access to secrets, causing ECS task failures

### Logging
- Terraform plan/apply operations will show which secrets are created/updated
- ECS task logs will show successful access to secrets
- IAM policy changes will be audited through CloudTrail

## Observability
### Tracing / Metrics / Logging Points
- Terraform state changes for secret resources
- ECS task execution logs showing secret access
- CloudTrail events for IAM policy changes
- CloudWatch logs for secret access errors

## Scaling Considerations
- Current load assumptions: Single deployment per environment
- Horizontal scaling: Secrets Manager supports high-throughput access
- Bottlenecks: Secret retrieval is lightweight, no significant scaling concerns
- Caching strategy: Secrets Manager handles caching internally

## File Changes

### New Files

| File Path | Description |
|-----------|-------------|
| `terraform/aws-ecs/modules/mcp-gateway/secrets.tf` | Add new secret resources for sensitive variables |
| `terraform/aws-ecs/secret-rotation-config.tf` | Add secret rotation configuration for new secrets |

### Modified Files

| File Path | Lines | Change Description |
|-----------|-------|--------------------|
| `terraform/aws-ecs/modules/mcp-gateway/variables.tf` | ~1350-1400 | Add new secret ARN variables |
| `terraform/aws-ecs/modules/mcp-gateway/iam.tf` | ~10-50 | Update IAM policy to include new secrets |
| `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf` | ~410-480, ~1285-1365 | Update environment variables and secrets blocks |
| `terraform/aws-ecs/secret-rotation-config.tf` | ~10-50 | Add new secret rotation resources |
| `terraform/aws-ecs/main.tf` | ~130-140 | Update main module to pass new variables |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| New code | ~300 |
| New tests | ~0 |
| Modified code | ~200 |
| **Total** | **~500** |

## Testing Strategy
{Pointer to testing.md - the full plan lives there}

## Alternatives Considered

### Alternative 1: Complete migration without fallback
**Description:** Remove all plaintext environment variables and only use secrets manager
**Pros / Cons:** 
- Pro: More secure approach
- Con: Breaks backward compatibility, requires all deployments to be updated simultaneously
**Why Rejected:** This would break existing deployments and isn't feasible for a gradual migration

### Alternative 2: Use Vault instead of Secrets Manager
**Description:** Replace AWS Secrets Manager with HashiCorp Vault
**Pros / Cons:**
- Pro: More advanced features, better integration with existing Vault deployments
- Con: Adds complexity, requires additional infrastructure, different learning curve
**Why Rejected:** AWS Secrets Manager is already established and sufficient for this use case

### Comparison Matrix

| Criteria | Chosen | Alt 1 | Alt 2 |
|----------|--------|-------|-------|
| Security | High | High | Very High |
| Complexity | Low | Low | High |
| Backward Compatibility | High | Low | Medium |
| Cost | Low | Low | Medium-High |
| Implementation Time | Short | Short | Long |

## Rollout Plan
- Phase 1: Implementation (out of scope for this skill)
- Phase 2: Testing (unit, integration, and E2E tests)
- Phase 3: Deployment (gradual rollout to environments)

## Open Questions
- Which specific sensitive environment variables should be migrated to Secrets Manager?
- Should all services (registry, auth-server, mcpgw) be updated consistently?
- Are there any additional services that need secret migration beyond the core ones?

## References
- [AWS Secrets Manager Documentation](https://docs.aws.amazon.com/secretsmanager/)
- [ECS Task Definition Secrets](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task_definition_parameters.html#container_definition_secrets)
- [Terraform AWS Secrets Manager Provider](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/secretsmanager_secret)