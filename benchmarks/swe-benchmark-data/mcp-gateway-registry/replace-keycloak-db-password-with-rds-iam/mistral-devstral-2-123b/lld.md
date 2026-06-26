# Low-Level Design: Replace Keycloak DB Password with RDS IAM Authentication

*Created: 2026-06-25*
*Author: Claude*
*Status: Draft*

## Table of Contents

1. [Overview](#overview)
2. [Codebase Analysis](#codebase-analysis)
3. [Architecture](#architecture)
4. [Configuration Parameters](#configuration-parameters)
5. [New Dependencies](#new-dependencies)
6. [Implementation Details](#implementation-details)
7. [Observability](#observability)
8. [Scaling Considerations](#scaling-considerations)
9. [File Changes](#file-changes)
10. [Testing Strategy](#testing-strategy)
11. [Alternatives Considered](#alternatives-considered)
12. [Rollout Plan](#rollout-plan)
13. [Security Considerations](#security-considerations)

## Overview

### Problem Statement
The current MCP Gateway Registry deployment uses static database credentials (`keycloak_database_username` and `keycloak_database_password`) stored in Terraform variables. These credentials pose security risks and require manual rotation. This design proposes replacing static credentials with RDS IAM Authentication for Aurora MySQL Serverless v2.

### Goals
- Eliminate static database credentials from Terraform configuration
- Implement RDS IAM authentication for automatic credential rotation
- Maintain backward compatibility during transition
- Enhance security posture with short-lived credentials
- Support zero-downtime deployment

### Non-Goals
- Modify Keycloak's internal authentication mechanisms
- Change database engine (remains Aurora MySQL)
- Alter deployment orchestration (ECS, Fargate, etc.)
- Modify non-database security aspects (ALB, networking)

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `terraform/aws-ecs/keycloak-database.tf` | Database infrastructure | Primary target for IAM auth changes |
| `terraform/aws-ecs/keycloak-ecs.tf` | Keycloak ECS task | Needs IAM role updates for token generation |
|| `terraform/aws-ecs/variables.tf` | Terraform variables | Remove static credential variables |
| `terraform/aws-ecs/main.tf` | Main configuration | Potential IAM role dependency updates |
| `auth_server/providers/keycloak.py` | Keycloak Python client | No changes needed - uses API, not direct DB |

### Existing Patterns Identified

1. **Secrets Manager Integration**: Current pattern uses Secrets Manager with RDS Proxy
   - Files: `keycloak-database.tf`, `keycloak-ecs.tf`
   - How a future implementer should follow this: Continue using Secrets Manager for hybrid phase, then remove after full cutover

2. **ECS Task Secrets Injection**: ECS task uses `valueFrom` syntax with Secrets Manager
   - Files: `keycloak-ecs.tf:97-104`
   - How a future implementer should follow this: New approach will use IAM token generation, not secrets injection

3. **RDS Proxy Configuration**: Proxy layer for connection pooling
   - Files: `keycloak-database.tf:6-28`
   - How a future implementer should follow this: Configure IAM auth alongside current auth for hybrid mode

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| RDS Aurora MySQL Cluster | Extends | Enable IAM authentication parameter group |
| RDS Proxy | Extends | Enable IAM authentication while keeping Secrets auth |
| ECS Task Role | Extends | Add `rds-db:connect` permission and token generation |
| Keycloak Configuration | Modifies | Update JDBC URL with SSL and IAM auth parameters |
| Database Users | Modifies | Create IAM-authenticated database user |

### Constraints and Limitations Discovered

- **Aurora MySQL IAM Auth Limit**: Maximum 10 IAM-authenticated users per cluster (not an issue for single Keycloak user)
- **Keycloak JDBC Driver**: Standard MySQL Connector/J 8.0+ supports IAM authentication
- **RDS Proxy IAM Auth**: Proxy must be updated to support IAM authentication for existing connections
- **Hybrid Mode Requirement**: Both IAM and password auth must coexist during transition
- **Keycloak 24+ Requirement**: Keycloak must be on version 24+ for proper MySQL 8.0 driver compatibility

## Architecture

Refer to github-issue.md and testing.md for detailed diagrams.

## Configuration Parameters

No new configuration parameters are introduced. However, the following parameters need to be modified:

- `keycloak_database_url`: Update JDBC URL for IAM authentication
- `keycloak_database_username`: Change to IAM format
- `keycloak_database_password`: Remove this parameter entirely

## New Dependencies

This change requires AWS RDS IAM Authentication to be enabled on Aurora MySQL, which is a standard AWS service feature. No new external dependencies are added.

## Implementation Details

### Hybrid Authentication Phase

```hcl
# During transition, keep both authentication methods enabled
resource "aws_db_proxy" "keycloak" {
  # ... existing config ...
  
  auth {
    auth_scheme               = "SECRETS" 
    secret_arn                = aws_secretsmanager_secret.keycloak_db_secret.arn
    client_password_auth_type = "MYSQL_CACHING_SHA2_PASSWORD"
    iam_auth                  = "REQUIRED"  # Enable IAM auth
  }
}
```

### IAM Policy Updates for ECS Task Role

```hcl
# Add RDS authentication permission to ECS task execution role
resource "aws_iam_role_policy" "keycloak_task_exec_ssm_policy" {
  name = "keycloak-task-exec-ssm-policy"
  role = aws_iam_role.keycloak_task_exec_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "rds-db:connect"  # Required for RDS IAM authentication
        ]
        Resource = [
          aws_rds_cluster.keycloak.arn
        ]
      }
    ]
  })
}
```

## Observability

### Key Missions

- Database connection success/failure metrics
- RDS IAM authentication attempt logs
- RDS Proxy connection pooling metrics
- Keycloak startup time with IAM auth vs static credentials

### Monitoring

- CloudWatch logs: `/aws/rds/cluster/keycloak/` for IAM auth events
- ECS task logs: Check for database connection issues
- RDS Proxy metrics: Connection count, authentication errors

## Scaling Considerations

- RDS IAM authentication supports the same connection limits as password auth
- RDS Proxy connection pooling works seamlessly with IAM auth
- ECS task scaling continues to work without modification
- IAM token generation latency is minimal (<10ms)

## File Changes

### Modified Files

| File Path | Lines | Change Description |
|-----------|-------|--------------------|
| `terraform/aws-ecs/keycloak-database.tf` | ~16, ~128 | Enable IAM auth parameter, update RDS proxy auth |
| `terraform/aws-ecs/keycloak-ecs.tf` | ~144 | Add `rds-db:connect` permission to task role |
| `terraform/aws-ecs/variables.tf` | ~12, ~18 | Remove password variable |

### New Files: None

This change repurposes existing infrastructure rather than adding new files.

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| New code | ~20 |
| Modified code | ~50 |
| Documentation | ~50 |
| **Total** | **~120** |

## Testing Strategy

See separate `testing.md` document for comprehensive test plan.

## Alternatives Considered

### Alternative 1: Use AWS Secrets Provider for MySQL
**Description:** Use AWS Secrets Manager with automatic rotation instead of IAM auth
**Pros / Cons:**
- ✓ AWS-managed rotation without IAM auth complexity
- ✗ Still relies on static-looking credentials
- ✗ No short-lived credential benefits
- ✗ Requires ongoing rotation maintenance
**Why Rejected:** IAM auth provides superior security with automatic short-lived credentials

### Alternative 2: Use PostgreSQL with IAM Auth
**Description:** Migrate to Aurora PostgreSQL to use PostgreSQL IAM authentication
**Pros / Cons:**
- ✓ PostgreSQL has better IAM auth supported inverters
- ✗ Major database engine change
- ✗ Requires Keycloak migration and testing
- ✗ Out of scope for this security initiative
**Why Rejected:** Database engine change is too large in scope

### Alternative 3: Keep Static Credentials with Better Rotation
**Description:** Enhance existing Secrets Manager rotation Lambda function
**Pros / Cons:**
- ✓ Minimal infrastructure change
- ✗ Still uses long-lived credentials
- ✗ Doesn't achieve zero standing privilege goal
- ✗ Maintains credential management overhead
**Why Rejected:** Fails to meet primary security objective

## Rollout Plan

### Phase 1: Infrastructure Updates
- Enable IAM authentication on Aurora MySQL cluster
- Update RDS Proxy for dual authentication support
- Add IAM permissions to ECS task role
- Test hybrid authentication mode

### Phase 2: Application Updates
- Update Keycloak ECS task to use IAM auth
- Deprecate static credential usage  
- Monitor database connections and errors

### Phase 3: Cleanup
- Remove static credentials from Terraform
- Remove old SSM entries and Secrets Manager secrets
- Update documentation
- Mark as "Secure by Default" in README

### Fallback Plan
- If IAM auth fails: RDS Proxy falls back to Secrets-based authentication
- Database credentials remain accessible during transition
- Hybrid mode allows safe rollback without downtime

## Security Considerations

- **Zero Standing Privileges**: No long-lived credentials with database access
- **Short-lived Tokens**: IAM tokens expire after 15 minutes
- **Fine-grained Access**: Controlled via IAM roles, not shared credentials
- **Audit Trail**: All access logged via CloudTrail through IAM
- **No Password Storage**: Eliminates secret spillage risk in logs or configs