# Low-Level Design: Migrate ECS Environment Variables to AWS Secrets Manager

*Created: 2026-06-25*
*Author: Devstral*
*Status: Draft*

## Overview

### Problem Statement
The MCP Gateway Registry currently has sensitive information stored in ECS task definition environment variables as plaintext, which creates security and compliance risks. This design migrates all sensitive environment variables to AWS Secrets Manager to implement least-privilege access control and improve security posture.

### Goals
1. Identify all sensitive environment variables across ECS services
2. Create Secrets Manager resources for each secret
3. Update ECS task definitions to reference secrets via `secrets` block
4. Extend IAM policies to grant least-privilege access
5. Implement zero-downtime migration process

### Non-Goals
1. Automatic secret rotation (future enhancement)
2. AWS Parameter Store changes
3. Amazon RDS IAM authentication changes

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `terraform/aws-ecs/keycloak-ecs.tf` | Keycloak ECS service definition | Contains sensitive Keycloak credentials that need migration |
| `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf` | MCP Gateway ECS services | Contains auth-server and registry services with sensitive env vars |
| `terraform/aws-ecs/modules/mcp-gateway/secrets.tf` | Existing secrets manager resources | Reference for existing secrets manager patterns |
| `terraform/aws-ecs/modules/mcp-gateway/iam.tf` | IAM policies for ECS | Needs updates for least-privilege secrets access |

### Existing Patterns Identified

1. **Secrets Manager Usage**: Services already use `aws_secretsmanager_secret` resources with KMS encryption via `aws_kms_key.secrets`
2. **ECS Secrets Block**: Some services already reference secrets via `secrets { valueFrom = resource.arn }`
3. **IAM Policy Structure**: Existing `ecs_secrets_access` policy grants `secretsmanager:GetSecretValue` access
4. **Terraform Modules**: The codebase uses `terraform-aws-modules/ecs/aws` module for service definitions

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| ECS Task Definitions | Extends | Add `secrets` blocks, remove sensitive vars from `environment` |
| IAM Policies | Extends | Update `ecs_secrets_access` policy with specific ARNs per service |
| Secrets Manager | Uses | Create new secrets resources for existing sensitive environment variables |
| KMS | Uses | Existing `aws_kms_key.secrets` for encryption |

### Constraints and Limitations Discovered

- **Zero-downtime requirement**: Must support seamless service during migration
- **Backwards compatibility**: Existing Terraform variables must continue to work
- **Terraform version**: Must support Terraform 1.5+ features
- **AWS region**: Fixed to us-east-1

## Architecture

### System Context Diagram

```
[External IdP] --> [CloudFront] --> [ALB] --> [ECS Services]
                                         ^              |
                                         |              v
[Secrets Manager] <-- [Task Execution Role] <-- [AWS KMS]
```

### Sequence Diagram

```
User --> CloudFront --> ALB --> ECS Task Container
                                    |                 |
                                    | (1) Assume role  |
                                    v                 |
                           [ECS Task Exec Role]       |
                                    | (2) GetSecretValue |
                                    v                 |
                          [AWS Secrets Manager]       |
                                    | (3) Decrypt      |
                                    v                 |
                                  [KMS]              |
```

## Data Models

### New Models
```hcl
# New secrets manager resources for existing sensitive environment variables
resource "aws_secretsmanager_secret" "registry_secret_key" {
  name_prefix             = "mcp-gateway-registry-secret-key-"
  description             = "Registry service secret key"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "registry_secret_key" {
  secret_id     = aws_secretsmanager_secret.registry_secret_key.id
  secret_string = var.registry_secret_key
}
```

### Model Changes
- Update ECS container definitions to move sensitive vars from `environment` to `secrets`
- Update IAM policies to reference specific Secrets Manager ARNs instead of wildcards

## API / CLI Design

### New Terraform Resources
**Description**: Terraform resources to create Secrets Manager entries for existing sensitive variables

**Example Override (Secrets Manager):**
```hcl
module.secrets["registry_secret_key"] = {
  name        = "SECRET_KEY"
  description = "Registry secret key"
  value       = var.registry_secret_key
}
```

**Error Cases:**
- Invalid Secrets Manager ARN format
- Missing KMS key permissions
- IAM policy too permissive

## Configuration Parameters

### New Terraform Variables

| Variable Name | Type | Default | Required | Description |
|---------------|------|---------|----------|-------------|
| `registry_secret_key` | string | n/a | Yes | Registry service secret key value |
| `enable_secrets_manager_migration` | bool | true | No | Enable/disable secrets migration |

### Updated IAM Policies

```hcl
resource "aws_iam_policy" "ecs_secrets_access" {
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["secretsmanager:GetSecretValue"]
      Resource = concat([
        aws_secretsmanager_secret.secret_key.arn,
        aws_secretsmanager_secret.registry_secret_key.arn,
        # Add new secrets here
      ], var.conditional_secrets)
    }]
  })
}
```

## New Dependencies
- No new dependencies required (using existing AWS provider)

## Implementation Details

### Step-by-Step Plan

#### Step 1: Create Secrets Manager Resources
**File:** `terraform/aws-ecs/modules/mcp-gateway/secrets.tf`
**Lines:** ~400-500 (new resources)

```hcl
# Registry service secret key
resource "aws_secretsmanager_secret" "registry_secret_key" {
  name_prefix             = "${local.name_prefix}-registry-secret-key-"
  description             = "MCP Gateway Registry secret key"
  recovery_window_in_days = 0
  kms_key_id              = aws_kms_key.secrets.id
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "registry_secret_key" {
  secret_id     = aws_secretsmanager_secret.registry_secret_key.id
  secret_string = var.registry_secret_key
}
```

#### Step 2: Update ECS Task Definitions
**File:** `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf`
**Lines:** ~1300-1350 (registry service secrets block)

```hcl
# Move SECRET_KEY from environment to secrets
secrets = concat(
  [
    {
      name      = "SECRET_KEY"
      valueFrom = aws_secretsmanager_secret.registry_secret_key.arn
    }
  ],
  # Existing secrets...
  var.conditional_secrets
)
```

#### Step 3: Update IAM Policies
**File:** `terraform/aws-ecs/modules/mcp-gateway/iam.tf`
**Lines:** ~10-40 (resource list in policy)

```hcl
Resource = concat(
  [
    aws_secretsmanager_secret.secret_key.arn,
    aws_secretsmanager_secret.keycloak_client_secret.arn,
    # Add new registry secret
    aws_secretsmanager_secret.registry_secret_key.arn
  ],
  var.conditional_secrets_arns
)
```

### Error Handling
- Validate secrets exist before deployment
- Use Terraform's `depends_on` for resource ordering
- Include health checks in ECS task definitions

### Logging
- Enable CloudWatch container insights
- Add secrets access to application logs (masked)
- Monitor IAM access events via CloudTrail

### Zero-Downtime Process

```bash
# Zero-downtime migration process
terraform apply -target=aws_secretsmanager_secret.registry_secret_key
terraform apply -target=aws_ecs_task_definition.registry
terraform apply -target=aws_iam_policy.ecs_secrets_access
terraform apply -target=aws_ecs_service.registry\n```

## Observability

### Tracing / Metrics / Logging Points
- CloudWatch Logs: `/ecs/mcp-gateway-registry-auth-server`
- IAM Access Events: CloudTrail logs
- Secrets Manager Metrics: AWS CloudWatch namespace AWS/SecretsManager

## Scaling Considerations

- Use existing autoscaling policies (unchanged)
- Secrets Manager has built-in rate limiting (5/sec per region)
- Consider caching sensitive, rarely changed values in container memory

## File Changes

### New Files

| File Path | Description |
|-----------|-------------|
| `terraform/aws-ecs/modules/mcp-gateway/ecs-secrets-migration.md` | Documentation for migration process |

### Modified Files

| File Path | Lines | Change Description |
|-----------|-------|--------------------|
| `terraform/aws-ecs/modules/mcp-gateway/secrets.tf` | ~400-500 | Add new secrets manager resources |
| `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf` | ~1300-1350 | Move sensitive vars from environment to secrets |
| `terraform/aws-ecs/modules/mcp-gateway/iam.tf` | ~10-40 | Update IAM policies for specific secrets access |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| New code | ~150 |
| Modified code | ~50 |
| **Total** | **~200** |

## Testing Strategy

See `testing.md` for comprehensive testing plan.

## Alternatives Considered

### Alternative 1: Use SSM Parameter Store
**Pros:** Easier migration, familiar interface
**Cons:** No built-in rotation, no audit logging, no versioning
**Why Rejected:** Doesn't meet security/compliance requirements

### Alternative 2: Third-party Secrets Manager
**Pros:** Vendor-agnostic, feature-rich
**Cons:** Additional cost, operational complexity
**Why Rejected:** AWS native solution preferred

## Rollout Plan

1. Test migration in dev environment
2. Implement automated rollback testing
3. Apply to production with monitoring
4. Document post-migration verification

## Open Questions

1. Should we implement automatic secret rotation in future phase?
2. What's the long-term strategy for secret lifecycle management?

## References

- AWS Secrets Manager ECS Integration: https://docs.aws.amazon.com/AmazonECS/latest/developerguide/specifying-sensitive-data.html
- Terraform AWS ECS Module: https://registry.terraform.io/modules/terraform-aws-modules/ecs/aws/latest