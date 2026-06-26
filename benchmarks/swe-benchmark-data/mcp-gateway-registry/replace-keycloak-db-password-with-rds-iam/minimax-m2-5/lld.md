# Low-Level Design: Replace Keycloak Database Password with RDS IAM Authentication

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
The current Keycloak deployment uses static database passwords stored in Terraform state, Secrets Manager, and ECS task definitions. This creates security and operational issues:
- Long-lived credentials stored at rest in multiple locations
- Manual password rotation required (via Lambda in issue #1026)
- Terraform state contains decrypted secrets
- Audit/compliance concerns

### Goals
- Replace static password authentication with RDS IAM authentication
- Use short-lived (~15 minute) tokens generated on-demand
- Eliminate stored DB credentials from Terraform state and Secrets Manager
- Enable zero-downtime cutover preserving existing Keycloak sessions
- Maintain backward compatibility with Keycloak 25

### Non-Goals
- Changing database engine from Aurora MySQL to PostgreSQL
- Modifying other services' database configuration
- Optimizing database connection pooling
- Changing RDS instance size or scaling configuration

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `terraform/aws-ecs/keycloak-database.tf` |Aurora MySQL cluster, RDS Proxy, Secrets Manager secret | Enable IAM auth on cluster, remove password from secret |
| `terraform/aws-ecs/keycloak-ecs.tf` | ECS task definition, IAM roles | Update task role for RDS IAM auth, modify container secrets |
| `terraform/aws-ecs/variables.tf` | Terraform variables | Remove `keycloak_database_password` variable |
| `terraform/aws-ecs/secret-rotation.tf` | Secrets Manager rotation Lambda | May need modification/removal |
| `terraform/aws-ecs/secret-rotation-config.tf` | Rotation Lambda configuration | May need modification |
| `docker-compose.yml` | Local development config | Already uses PostgreSQL - not affected |

### Existing Patterns Identified

1. **RDS Proxy Pattern**: The codebase already uses RDS Proxy for connection pooling (lines 6-19 of keycloak-database.tf). The proxy currently uses Secrets Manager auth. After IAM auth is enabled on the cluster, the proxy connection will need to authenticate via IAM as well.

2. **Secrets Manager for DB Credentials**: Issue #1026 established the pattern of reading `KC_DB_USERNAME` and `KC_DB_PASSWORD` from Secrets Manager. This pattern will be replaced.

3. **IAM Role for ECS Tasks**: The keycloak-task-role and keycloak-task-exec-role already exist and will be updated to include `rds-db:connect` permission.

4. **SSM Parameter for DB URL**: The `/keycloak/database/url` SSM parameter currently contains a JDBC URL without credentials. This URL format will need to change to use IAM auth.

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| Aurora MySQL Cluster | Enable IAM auth | Add `iam_authentication_enabled = true` to cluster |
| RDS Proxy | Update auth | Requires IAM auth for both cluster and proxy |
| ECS Task Execution Role | Add permission | `rds-db:connect` for the DB user |
| Secrets Manager | Remove/modify | Remove password from secret or delete secret entirely |
| Keycloak Container | Change env vars | Add IAM token generation at startup |

### Constraints and Limitations Discovered

- **Aurora MySQL IAM Auth**: Requires the DB user to be created with IAM authentication support (`CREATE USER ... IDENTIFIED WITH AWSAUTH`)
- **RDS Proxy**: Currently uses password auth via Secrets Manager. After IAM migration, proxy must also use IAM auth.
- **Keycloak KC_DB**: Currently set to `mysql`. Keycloak doesn't natively support IAM auth for MySQL - requires an entrypoint wrapper script.
- **Zero-downtime**: Must preserve existing sessions during cutover - requires careful ordering of changes.

## Architecture

### System Context Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                            AWS Cloud                                     │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  ECS Fargate - Keycloak Service                                  │   │
│  │  ┌────────────────────────────────────────────────────────────┐  │   │
│  │  │  Keycloak Container                                        │  │   │
│  │  │  ┌──────────────────┐  ┌─────────────────────────────┐   │  │   │
│  │  │  │ Entrypoint       │  │ Keycloak Process            │   │  │   │
│  │  │  │ (token generator)│→ │ (KC_DB=mysql, IAM auth)    │   │  │   │
│  │  │  └────────┬─────────┘  └─────────────────────────────┘   │  │   │
│  │  │           │                                                │  │   │
│  │  │           ↓                                                │  │   │
│  │  │  ┌────────────────────────────────────────────────────┐   │  │   │
│  │  │  │ IAM Role: keycloak-task-role                       │   │  │   │
│  │  │  │   └─ rds-db:connect ( IAM auth token generation)   │   │  │   │
│  │  │  └────────────────────────────────────────────────────┘   │  │   │
│  │  └────────────────────────────────────────────────────────────┘  │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                                    │                                     │
│                                    ↓                                     │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  RDS Proxy → Aurora MySQL Cluster                               │   │
│  │    - iam_authentication_enabled = true                          │   │
│  │    - rds-proxy.auth = IAM                                       │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### Sequence Diagram

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  ECS Task   │     │ AWS STS     │     │  Aurora     │     │  Keycloak   │
│  Starting   │     │  (Generate  │     │  MySQL      │     │  Process    │
│             │     │  Token)     │     │  (Validate) │     │             │
└──────┬──────┘     └──────┬──────┘     └──────┬──────┘     └──────┬──────┘
       │                   │                   │                   │
       │ 1. Start container│                   │                   │
       │──────────────────→│                   │                   │
       │                   │                   │                   │
       │ 2. Run entrypoint │                   │                   │
       │   script          │                   │                   │
       │──────────────────→│                   │                   │
       │                   │                   │                   │
       │ 3. Get IAM token  │                   │                   │
       │   via rds-token   │                   │                   │
       │   generator       │                   │                   │
       │──────────────────→│                   │                   │
       │                   │                   │                   │
       │ 4. Receive token  │                   │                   │
       │←──────────────────│                   │                   │
       │                   │                   │                   │
       │ 5. Set as         │                   │                   │
       │   KC_DB_PASSWORD  │                   │                   │
       │──────────────────→│                   │                   │
       │                   │                   │                   │
       │                   │ 6. Connect with   │                   │
       │                   │    IAM token      │                   │
       │                   │──────────────────→│                   │
       │                   │                   │                   │
       │                   │ 7. Auth success   │                   │
       │                   │←──────────────────│                   │
       │                   │                   │                   │
       │                   │                   │ 8. Start Keycloak│
       │                   │                   │←─────────────────│
       │                   │                   │                   │
```

### Component Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    Modified Components                                  │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  terraform/aws-ecs/keycloak-database.tf                                 │
│  ├── aws_rds_cluster.keycloak                                           │
│  │   └── + iam_authentication_enabled = true                           │
│  ├── aws_db_proxy.keycloak                                              │
│  │   └── + Auth configuration changed to IAM                           │
│  ├── aws_secretsmanager_secret.keycloak_db_secret                      │
│  │   └── - password field (or secret deletion)                         │
│  └── aws_iam_role.rds_proxy_role                                        │
│      └── - Remove secretsmanager access                                 │
│                                                                          │
│  terraform/aws-ecs/keycloak-ecs.tf                                      │
│  ├── aws_iam_role.keycloak_task_exec_role                               │
│  │   └── + rds-db:connect permission                                    │
│  ├── aws_iam_role_policy.keycloak_task_exec_rds_policy (NEW)           │
│  ├── aws_ecs_task_definition.keycloak                                   │
│  │   └── - KC_DB_PASSWORD secret                                        │
│  │   └── + Entrypoint script volume                                     │
│  └── aws_ssm_parameter.keycloak_database_url                           │
│      └── + Update to include iamauthcache parameter                     │
│                                                                          │
│  terraform/aws-ecs/variables.tf                                         │
│  └── - variable "keycloak_database_password"                            │
│                                                                          │
│  docker/ (NEW)                                                          │
│  └── keycloak-entrypoint.sh                                             │
│      └── Entrypoint that generates IAM token before starting Keycloak  │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

## Data Models

### Terraform Variables (to be removed)

```hcl
# REMOVE this variable
variable "keycloak_database_password" {
  description = "Keycloak database password"
  type        = string
  sensitive   = true
}
```

### New Terraform Variables

```hcl
# NEW variable (optional, for feature flagging)
variable "keycloak_use_iam_auth" {
  description = "Use IAM authentication for Keycloak database (recommended over password auth)"
  type        = bool
  default     = true
}
```

### Secrets Manager Secret Structure

**Before:**
```json
{
  "username": "keycloak",
  "password": "static-password-value"
}
```

**After:**
```json
{
  "username": "keycloak"
}
```
*Note: Password field removed. IAM auth doesn't require stored password.*

## API / CLI Design

### No External API Changes

This is an infrastructure change. No REST API or CLI endpoints are added or modified.

### Container Entrypoint

The Keycloak container will use a custom entrypoint script that:
1. Generates an IAM auth token using the AWS CLI
2. Sets the token as `KC_DB_PASSWORD` environment variable
3. Executes the original Keycloak entrypoint

**Dockerfile snippet:**
```dockerfile
# Override entrypoint
COPY keycloak-entrypoint.sh /usr/local/bin/
ENTRYPOINT ["/usr/local/bin/keycloak-entrypoint.sh"]
CMD ["start"]
```

**Entrypoint script:**
```bash
#!/bin/bash
set -e

# Generate RDS IAM auth token
# The token is short-lived (~15 minutes) and changes on every container start
export KC_DB_PASSWORD=$(aws rds generate-db-auth-token \
    --hostname "${KC_DB_URL_HOST}" \
    --port 3306 \
    --username "${KC_DB_USERNAME}" \
    --region "${AWS_REGION}")

# Execute original Keycloak entrypoint
exec /opt/keycloak/bin/kc.sh "$@"
```

*Note: `KC_DB_URL_HOST` needs to be extracted from `KC_DB_URL` environment variable.*

## Configuration Parameters

### New Environment Variables (Container)

| Variable Name | Type | Source | Description |
|---------------|------|--------|-------------|
| `KC_DB_PASSWORD` | string | Generated at runtime | Short-lived IAM token |

### Removed Environment Variables (from Secrets Manager)

| Variable Name | Previous Source | Notes |
|---------------|-----------------|-------|
| `KC_DB_PASSWORD` | Secrets Manager | Now generated via IAM |

### Existing Environment Variables (unchanged)

| Variable Name | Source | Description |
|---------------|--------|-------------|
| `KC_DB` | Task env | `mysql` |
| `KC_DB_URL` | SSM Parameter | JDBC URL |
| `KC_DB_USERNAME` | Secrets Manager | DB username (unchanged) |
| `AWS_REGION` | Task env | AWS region |

### Deployment Surface Checklist

| Configuration Location | Action Required |
|------------------------|-----------------|
| `terraform/aws-ecs/variables.tf` | Remove `keycloak_database_password` variable |
| `terraform/aws-ecs/terraform.tfvars.example` | Remove password variable reference |
| `terraform/aws-ecs/keycloak-database.tf` | Enable IAM auth on cluster; modify proxy auth |
| `terraform/aws-ecs/keycloak-ecs.tf` | Add IAM role permissions; modify secrets |
| `charts/keycloak/values.yaml` | If exists, update Helm values |
| `.env.example` | Remove KEYCLOAK_DB_PASSWORD references |

## New Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| AWS CLI | Pre-installed on ECS container | Generate RDS IAM auth token |

*Note: The AWS CLI is already available in the Keycloak ECS task (via the ECS-optimized AMI). No new system dependencies required.*

## Implementation Details

### Step-by-Step Plan

#### Step 1: Enable IAM Authentication on RDS Cluster
**File:** `terraform/aws-ecs/keycloak-database.tf`
**Lines:** ~48-60 (aws_rds_cluster)

```hcl
resource "aws_rds_cluster" "keycloak" {
  # ... existing config ...

  # Add IAM authentication
  iam_authentication_enabled = var.keycloak_use_iam_auth ? true : false

  # ... rest unchanged ...
}
```

#### Step 2: Create DB User with IAM Auth Support
**File:** `terraform/aws-ecs/keycloak-database.tf`
**Location:** After cluster creation (requires database connection)

```hcl
# This requires a null_resource with local-exec to create the user
# since Terraform doesn't support CREATE USER with IAM natively
resource "null_resource" "keycloak_db_iam_user" {
  depends_on = [aws_rds_cluster_instance.keycloak]

  provisioner "local-exec" {
    command = <<-EOT
      aws rds execute-db-statement \
        --db-cluster-identifier ${aws_rds_cluster.keycloak.cluster_identifier} \
        --region ${var.aws_region} \
        --sql "CREATE USER IF NOT EXISTS '${var.keycloak_database_username}'@'%' IDENTIFIED WITH AWSAUTH;"
    EOT
  }
}
```

*Note: This is a one-time operation. The user creation persists.*

#### Step 3: Update RDS Proxy to Use IAM Auth
**File:** `terraform/aws-ecs/keycloak-database.tf`
**Lines:** ~6-25 (aws_db_proxy)

```hcl
resource "aws_db_proxy" "keycloak" {
  name                   = "keycloak-proxy"
  engine_family          = "MYSQL"
  vpc_security_group_ids = [aws_security_group.keycloak_db.id]
  vpc_subnet_ids         = module.vpc.private_subnets

  # Update auth to use IAM
  auth {
    auth_scheme = "IAM"
    iam_auth    = "DISABLED"  # "REQUIRED" for IAM-only, "PREFERRED" for dual
    secret_arn  = ""  # Remove - IAM auth doesn't use secrets
  }

  # Role ARN needed for IAM auth
  role_arn = aws_iam_role.rds_proxy_role.arn

  # ... rest unchanged ...
}
```

#### Step 4: Update ECS Task Execution Role
**File:** `terraform/aws-ecs/keycloak-ecs.tf`
**Lines:** ~140-210 (after existing policies)

```hcl
# NEW: Policy for RDS IAM authentication
resource "aws_iam_role_policy" "keycloak_task_exec_rds_iam_policy" {
  name = "keycloak-task-rds-iam-policy"
  role = aws_iam_role.keycloak_task_exec_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "rds-db:connect"
        ]
        Resource = [
          "arn:aws:rds-db:${var.aws_region}:${data.aws_caller_identity.current.account_id}:dbuser:${aws_rds_cluster.keycloak.cluster_identifier}/${var.keycloak_database_username}"
        ]
      }
    ]
  })
}
```

#### Step 5: Update ECS Task Secrets
**File:** `terraform/aws-ecs/keycloak-ecs.tf`
**Lines:** ~77-105 (keycloak_container_secrets)

```hcl
locals {
  keycloak_container_secrets = [
    {
      name      = "KEYCLOAK_ADMIN"
      valueFrom = aws_ssm_parameter.keycloak_admin.arn
    },
    {
      name      = "KEYCLOAK_ADMIN_PASSWORD"
      valueFrom = aws_ssm_parameter.keycloak_admin_password.arn
    },
    {
      name      = "KC_DB_URL"
      valueFrom = aws_ssm_parameter.keycloak_database_url.arn
    },
    {
      name      = "KC_DB_USERNAME"
      valueFrom = "${aws_secretsmanager_secret.keycloak_db_secret.arn}:username::"
    }
    # KC_DB_PASSWORD removed - now generated at runtime via entrypoint
  ]
}
```

#### Step 6: Update Database URL SSM Parameter
**File:** `terraform/aws-ecs/keycloak-database.tf`
**Lines:** ~277-283

```hcl
resource "aws_ssm_parameter" "keycloak_database_url" {
  name  = "/keycloak/database/url"
  type  = "SecureString"
  key_id = aws_kms_key.rds.id
  # Add iamauthcache=1 to enable IAM authentication caching
  value = "jdbc:mysql://${aws_rds_cluster.keycloak.endpoint}:3306/keycloak?ssl=enabled&sslmode=require&iamauthcache=1"
  tags  = local.common_tags
}
```

#### Step 7: Create Entrypoint Script
**File:** `docker/keycloak-entrypoint.sh`

```bash
#!/bin/bash
set -e

# Extract hostname from KC_DB_URL
# Format: jdbc:mysql://hostname:port/database
DB_HOST=$(echo "${KC_DB_URL}" | sed -n 's|.*://\([^:]*\):.*|\1|p')

# Generate RDS IAM authentication token
# Token is valid for 15 minutes and changes on every invocation
KC_DB_PASSWORD=$(aws rds generate-db-auth-token \
    --hostname "${DB_HOST}" \
    --port 3306 \
    --username "${KC_DB_USERNAME}" \
    --region "${AWS_REGION}")

export KC_DB_PASSWORD

# Execute original Keycloak entrypoint
exec /opt/keycloak/bin/kc.sh "$@"
```

#### Step 8: Update Dockerfile to Use Custom Entrypoint
**File:** Updates to existing Dockerfile or create override

The Keycloak container image (`quay.io/keycloak/keycloak:25.0`) is used as-is with a custom entrypoint overlay:

```dockerfile
FROM quay.io/keycloak/keycloak:25.0

# Copy custom entrypoint
COPY keycloak-entrypoint.sh /usr/local/bin/keycloak-entrypoint.sh
RUN chmod +x /usr/local/bin/keycloak-entrypoint.sh

# Override default entrypoint
ENTRYPOINT ["/usr/local/bin/keycloak-entrypoint.sh"]
CMD ["start"]
```

#### Step 9: Remove Database Password from Secrets Manager
**File:** `terraform/aws-ecs/keycloak-database.tf`

Option A: Keep secret with username only (recommended for backward compat):
```hcl
resource "aws_secretsmanager_secret_version" "keycloak_db_secret" {
  secret_id = aws_secretsmanager_secret.keycloak_db_secret.id
  # Only store username - password is now IAM-generated at runtime
  secret_string = jsonencode({
    username = var.keycloak_database_username
  })
}
```

Option B: Delete secret entirely (more secure):
```hcl
# Mark secret for deletion on next terraform apply
# resource "aws_secretsmanager_secret" "keycloak_db_secret" { ... }
# Add lifecycle { prevent_destroy = false } and run terraform destroy
```

### Error Handling

1. **Token Generation Failure**: If IAM token generation fails, container fails to start with clear error message
2. **IAM Permission Missing**: Task fails with "Access Denied" for `rds-db:connect` - captured in CloudWatch logs
3. **Database User Not Created with IAM**: Connection fails with authentication error - requires Step 2 to be run

### Logging

| Event | Log Level | Location |
|-------|-----------|----------|
| IAM token generation attempt | INFO | CloudWatch /ecs/keycloak |
| Token generation success | DEBUG | CloudWatch |
| Token generation failure | ERROR | CloudWatch |
| DB connection with IAM | INFO | CloudWatch |
| DB connection failure | ERROR | CloudWatch |

## Observability

### CloudWatch Logs

Keycloak logs already route to `/ecs/keycloak` CloudWatch log group. Add entrypoint script logging:

```bash
echo "[INFO] Generating RDS IAM auth token..."
aws rds generate-db-auth-token ...
echo "[INFO] IAM token generated, starting Keycloak..."
```

### Metrics

No new metrics required. Existing Keycloak metrics continue to work.

### Traceability

- IAM `rds-db:connect` calls are logged in CloudTrail
- Secrets Manager access is logged in CloudTrail
- ECS task state changes are logged

## Scaling Considerations

- **Token Generation**: Each new ECS task generates its own token (~1-2 seconds)
- **Token Caching**: The `iamauthcache=1` JDBC parameter caches the IAM token within the connection, avoiding per-query token generation
- **RDS Proxy**: Already in place for connection pooling; continues to work with IAM auth
- **Horizontal Scaling**: New tasks generate tokens independently - no shared state

## File Changes

### New Files

| File Path | Description |
|-----------|-------------|
| `docker/keycloak-entrypoint.sh` | Entrypoint script to generate IAM token |
| `docker/Dockerfile.keycloak` | Optional custom Dockerfile (if not using override) |

### Modified Files

| File Path | Lines | Change Description |
|-----------|-------|--------------------|
| `terraform/aws-ecs/keycloak-database.tf` | ~50, ~10, ~280 | Enable IAM auth on cluster and proxy; update SSM param |
| `terraform/aws-ecs/keycloak-ecs.tf` | ~80-105, ~140-210 | Remove KC_DB_PASSWORD secret; add rds-db:connect policy |
| `terraform/aws-ecs/variables.tf` | ~97-101 | Remove keycloak_database_password variable |
| `terraform/aws-ecs/secret-rotation.tf` | TBD | May need to disable or remove Lambda |
| `terraform/aws-ecs/secret-rotation-config.tf` | TBD | May need modification |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| New code (entrypoint + terraform) | ~80 |
| Modified code | ~40 |
| Removed code | ~15 |
| **Total** | **~135** |

## Testing Strategy

*Pointer to testing.md - the full plan lives there*

## Alternatives Considered

### Alternative 1: Use RDS Proxy IAM Auth Only (Not Cluster)
**Description:** Enable IAM on RDS Proxy but keep password auth on Aurora cluster.

**Pros:**
- Simpler change (proxy handles IAM, not Keycloak)
- Keycloak container stays unchanged

**Cons:**
- Password still stored in Secrets Manager
- Doesn't fully address the security concern
- Two auth mechanisms to maintain

**Why Rejected:** Doesn't fully solve the static credentials problem.

### Alternative 2: Use AWS Secrets Manager Cache with IAM
**Description:** Keep Secrets Manager but add IAM-based token generation wrapper.

**Pros:**
- More gradual migration
- Can roll back easier

**Cons:**
- Still relies on Secrets Manager for something
- Extra infrastructure complexity

**Why Rejected:** Adds complexity without significant benefit.

### Alternative 3: Use PostgreSQL Instead of MySQL
**Description:** Migrate from Aurora MySQL to Aurora PostgreSQL which has native IAM support.

**Pros:**
- Native IAM support in Keycloak (KC_DB=postgres)
- No custom entrypoint needed

**Cons:**
- Database migration required
- Significant testing needed for Keycloak data
- Out of scope for this issue

**Why Rejected:** Out of scope - database engine migration is separate work.

## Comparison Matrix

| Criteria | Chosen (IAM Auth) | Alt 1 (Proxy Only) | Alt 2 (SM Cache) |
|----------|-------------------|--------------------|--------------------|
| Eliminates static passwords | Yes | Partial | No |
| Complexity | Medium | Low | Medium |
| Keycloak changes | Modified entrypoint | Minimal | None |
| Rollback complexity | Medium | Low | Low |
| Out of scope | N/A | N/A | N/A |

## Rollout Plan

### Phase 1: Implementation (Day 1)
1. Add `keycloak_use_iam_auth` variable to Terraform
2. Enable IAM auth on Aurora cluster (no-op if feature flag disabled)
3. Create DB user with IAM auth support (null_resource)
4. Update RDS Proxy auth configuration
5. Add rds-db:connect IAM policy to task execution role
6. Remove KC_DB_PASSWORD from ECS secrets
7. Create entrypoint script
8. Update database URL in SSM

### Phase 2: Testing (Day 2)
1. Deploy to staging environment
2. Verify Keycloak starts successfully
3. Verify IAM token is being generated
4. Test Keycloak login and session creation
5. Verify database operations work
6. Run load test to verify connections are pooled via RDS Proxy

### Phase 3: Production Cutover (Day 3-4)
1. Set `keycloak_use_iam_auth = true` in production
2. Deploy Terraform changes
3. Run null_resource to create IAM-enabled DB user
4. Deploy ECS task definition with new entrypoint
5. Old tasks drain gracefully, new tasks use IAM auth
6. Verify no session drops
7. Monitor CloudWatch for any errors
8. Remove password rotation Lambda (or disable)

### Rollback Procedure
1. Set `keycloak_use_iam_auth = false`
2. Revert Terraform and ECS changes
3. Keycloak reconnects using old password flow
4. Re-enable password rotation Lambda

## Open Questions

- **Database User Creation**: The null_resource approach for creating the IAM-enabled user requires database connectivity. Is there a better way to ensure this runs only once?
- **Password Removal Timing**: Should the password be removed from Secrets Manager before or after confirming IAM auth works?
- **Backwards Compatibility**: Should the Terraform continue to accept `keycloak_database_password` but ignore it (for users who haven't updated their tfvars)?

## References

- [AWS RDS IAM Authentication Documentation](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/UsingWithRDS.IAMDBAuth.html)
- [AWS RDS generate-db-auth-token CLI](https://awscli.amazonaws.com/v2/documentation/api/latest/reference/rds/generate-db-auth-token.html)
- [Keycloak Database Configuration](https://www.keycloak.org/server/db)
- [RDS Proxy IAM Authentication](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/rds-proxy.html#rds-proxy-auth.iam)
- Issue #1026 (previous secrets Manager pattern)