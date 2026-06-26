# Low-Level Design: Replace Keycloak Database Password with RDS IAM Authentication

*Created: 2026-06-25*
*Author: Claude (Qwen Qwen3-Coder-Next)*
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
Keycloak connects to an Aurora MySQL database using static credentials stored in AWS Secrets Manager. The current implementation uses `keycloak_database_password` variable in Terraform and stores both username and password in the `keycloak/database` Secrets Manager secret. The ECS task reads these credentials at startup and uses them for database authentication.

RDS IAM database authentication provides short-lived, automatically-rotating credentials that:
- Eliminate the need to store static passwords
- Integrate with AWS IAM for centralized access management
- Provide automatic credential rotation (tokens valid for 15 minutes)

### Goals
- Remove static database password from Terraform state and Secrets Manager
- Migrate Keycloak to use IAM database authentication tokens
- Maintain backward compatibility during transition
- Enable automatic credential rotation via IAM

### Non-Goals
- Changing the database engine (stays Aurora MySQL)
- Modifying Keycloak application code significantly (only startup configuration)
- Updating the RDS proxy configuration (will continue using Secrets Manager)

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `terraform/aws-ecs/keycloak-database.tf` | Aurora MySQL cluster and RDS proxy config | Contains current password-based auth setup |
| `terraform/aws-ecs/keycloak-ecs.tf` | Keycloak ECS task definition | ECS task reads credentials from Secrets Manager |
| `terraform/aws-ecs/variables.tf` | Terraform variables | Contains `keycloak_database_password` variable |
| `terraform/aws-ecs/modules/mcp-gateway/iam.tf` | IAM policies for ECS services | Contains secrets access policies |
| `docker-compose.yml` | Local development compose | Uses `KEYCLOAK_DB_PASSWORD` env var |
| `docker-compose.podman.yml` | Podman compose for rootless | Uses `KEYCLOAK_DB_PASSWORD` env var |

### Existing Patterns Identified
1. **Secrets Manager Integration**: All AWS deployments use Secrets Manager for sensitive credentials via ECS task secrets
2. **SSM Parameter Store for Non-Sensitive Creds**: Database URL, admin username stored in SSM
3. **Environment Variable Configuration**: Database connection via `KC_DB_URL`, `KC_DB_USERNAME`, `KC_DB_PASSWORD`
4. **ECS Task Execution Role Pattern**: Separate task exec role with policy attachments for secret access

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| Aurora MySQL Cluster | Authentication | Currently uses Secrets Manager password for master credentials |
| Keycloak ECS Task | Credential Retrieval | Reads `keycloak/database` secret for username/password |
| RDS Proxy | Authentication | Currently uses `DISABLED` for IAM auth, needs enablement |
| IAM Role | Permissions | Needs `rds-db:connect` permission with DB username condition |

### Constraints and Limitations Discovered
- **Aurora MySQL Engine**: Uses MySQL-compatible engine; IAM auth requires special connection string format
- **RDS Proxy**: Currently configured with `iam_auth = "DISABLED"`; may need reconfiguration
- **Keycloak Startup**: Keycloak reads all DB credentials at startup; IAM token must be available before startup
- **Sealed Secrets Pattern**: Current setup uses Secrets Manager as source of truth; new approach needs token generation at runtime

## Architecture

### System Context Diagram
```
┌─────────────────────────────────────────────────────────────────────┐
│                         AWS Account                                  │
│                                                                     │
│  ┌──────────────────┐         ┌──────────────────┐                  │
│  │   Keycloak ECS   │─────┐   │  Secrets Manager │                  │
│  │    Task          │     │   │  keycloak/       │                  │
│  │  (Fargate)       │     │   │  database        │                  │
│  │                  │     │   │  - username      │                  │
│  └────────┬─────────┘     │   │  - password      │                  │
│           │               │   └──────────────────┘                  │
│           │               │                                         │
│           │               │                                         │
│           ▼               │                                         │
│  ┌──────────────────┐     │    ┌──────────────────┐                 │
│  │  IAM Role        │─────┼────►  Aurora MySQL    │                 │
│  │  keycloak-task   │     │    │  Cluster         │                 │
│  └──────────────────┘     │    │  - IAM Auth      │                 │
│           │               │    │  - RDS Proxy     │                 │
│           │               │    └──────────────────┘                 │
│           ▼               │                                         │
│  ┌──────────────────┐     │                                         │
│  │  RDS DB          │◀────┘                                         │
│  │  Authentication  │                                               │
│  └──────────────────┘                                               │
└─────────────────────────────────────────────────────────────────────┘
```

### Sequence Diagram
```
Keycloak ECS Task Start
    │
    ├─► IAM RoleSTS Assume Role (get temp credentials)
    │
    ├─► RDS Generate DB Auth Token
    │   └─► Returns: temporary IAM token (15min expiry)
    │
    ├─► Keycloak Container Startup
    │   ├─► KC_DB_URL=jdbc:mysql://proxy_endpoint:3306/keycloak?useSSL=true
    │   ├─► KC_DB_USERNAME=keycloak
    │   ├─► KC_DB_PASSWORD=<IAM token from step 2>
    │   └─► Connects to RDS Proxy
    │
    ├─► RDS Proxy
    │   └─► Validates IAM token with RDS
    │
    └─► Aurora MySQL
        └─► Auth successful, connection established
```

### Component Diagram
```
┌──────────────────────────────────────────────────────────────┐
│                    Keycloak Deployment                         │
├──────────────────────────────────────────────────────────────┤
│                                                                │
│  ┌──────────────────┐    ┌──────────────────┐                │
│  │  ECS Task        │    │  IAM Role        │                │
│  │  - keycloak-task │    │  keycloak-task   │                │
│  │  - task-exec     │    │  permissions:    │                │
│  │                  │    │  - rds-db:connect│                │
│  └────────┬─────────┘    └──────────────────┘                │
│           │                                                    │
│           │ get temp credentials                              │
│           ▼                                                    │
│  ┌──────────────────┐    ┌──────────────────┐                │
│  │  IAM Service     │    │  RDS Service     │                │
│  │  STS             │    │  IAM Auth Token  │                │
│  └────────┬─────────┘    └──────────────────┘                │
│           │              generate & validate                 │
│           │                                                    │
│           ▼                                                    │
│  ┌────────────────────────────────────────┐                  │
│  │     Aurora MySQL Cluster               │                  │
│  │  ┌──────────────────────────────────┐  │                  │
│  │  │  RDS Proxy (connection pooling)  │  │                  │
│  │  │  - IAM auth enabled              │  │                  │
│  │  └──────────────────────────────────┘  │                  │
│  └────────────────────────────────────────┘                  │
│                                                                │
└──────────────────────────────────────────────────────────────┘
```

## Data Models

### New Models

No new data models required. The IAM token is a runtime-generated string with the following properties:
- Format: JWT-like temporary token
- Expiry: 15 minutes
- Generated via: `boto3.client('rds').generate_db_auth_token()`

## Configuration Parameters

### New Environment Variables

| Variable Name | Type | Default | Required | Description |
|---------------|------|---------|----------|-------------|
| `KC_DB_USE_IAM` | bool | `false` | No | Enable IAM database authentication |  
| `KC_DB_REGION` | string | `us-west-2` | No | AWS region for RDS (defaults to ECS task region) |

### Existing Variables (Modified Behavior)

| Variable Name | Type | Current Use | After Change |
|---------------|------|-------------|--------------|
| `keycloak_database_password` | string |Stored in Terraform and Secrets Manager| **DEPRECATED**: Remove from Terraform config |
| `KC_DB_PASSWORD` | string | Used for password auth | Used for IAM token (generated at runtime) |

### Settings / Config Class Updates

No code-level config class updates needed. IAM authentication is handled at the connection level via JDBC driver.

### Deployment Surface Checklist

- [ ] `terraform/aws-ecs/keycloak-database.tf`: Update RDS cluster parameter to enable IAM auth
- [ ] `terraform/aws-ecs/keycloak-ecs.tf`: Update IAM policy with `rds-db:connect` permission
- [ ] `terraform/aws-ecs/variables.tf`: Remove `keycloak_database_password` variable
- [ ] `terraform/aws-ecs/main.tf`: Remove secret password from keycloak configuration
- [ ] `terraform/aws-ecs/modules/mcp-gateway/iam.tf`: Add IAM auth policy
- [ ] `docker-compose.yml`: Update Keycloak service to support IAM mode
- [ ] `docker-compose.podman.yml`: Update Keycloak service to support IAM mode
- [ ] `charts/mcp-gateway-registry-stack/values.yaml`: Add IAM auth option

## New Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `boto3` | latest | AWS SDK for Python (generates IAM tokens) |
| `rds-auth-plugin` | latest | MySQL driver plugin for IAM auth (if needed) |

**Note:** If the Keycloak image does not include AWS SDK, the token generation must happen in a sidecar or init container.

## Implementation Details

### Step-by-Step Plan (for a future implementer)

#### Step 1: Enable IAM Authentication on Aurora MySQL

**File:** `terraform/aws-ecs/keycloak-database.tf`
**Lines:** 48-96 (aws_rds_cluster.keycloak)

```hcl
# Update the RDS cluster to enable IAM database authentication
resource "aws_rds_cluster" "keycloak" {
  # ... existing configuration ...
  
  # Enable IAM database authentication
  iam_database_authentication_enabled = true
  
  # ... rest of existing configuration ...
}
```

Also update the RDS proxy to use IAM auth:

```hcl
resource "aws_db_proxy" "keycloak" {
  # ... existing configuration ...
  
  auth {
    auth_scheme               = "SECRETS"
    secret_arn                = aws_secretsmanager_secret.keycloak_db_secret.arn
    client_password_auth_type = "MYSQL_CACHING_SHA2_PASSWORD"
    iam_auth                  = "ENABLED"  # Changed from DISABLED
  }
  
  # ... rest of existing configuration ...
}
```

#### Step 2: Update ECS Task IAM Policy

**File:** `terraform/aws-ecs/keycloak-ecs.tf`
**Lines:** 169-209 (aws_iam_role_policy.keycloak_task_exec_ssm_policy)

```hcl
resource "aws_iam_role_policy" "keycloak_task_exec_ssm_policy" {
  name = "keycloak-task-exec-ssm-policy"
  role = aws_iam_role.keycloak_task_exec_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # ... existing SSM and Secrets Manager policies ...
      
      # Add IAM database authentication permission
      {
        Effect = "Allow"
        Action = [
          "rds-db:connect"
        ]
        Resource = [
          "arn:aws:rds-db:${data.aws_region.current.id}:${data.aws_caller_identity.current.account_id}:dbuser:${aws_rds_cluster.keycloak.master_username}/*"
        ]
      },
      
      # ... existing KMS policy ...
    ]
  })
}
```

#### Step 3: Create IAM Auth Token Generator Script

**File:** `terraform/aws-ecs/scripts/generate-iam-token.sh` (new file)

```bash
#!/bin/bash
# Generate RDS IAM authentication token for Keycloak
# This script is called by the Keycloak container startup

# Get the RDS endpoint and port from environment or defaults
DB_HOST="${DB_HOST:-${AWS_RDS_PROXY_ENDPOINT:-keycloak-proxy}}"
DB_PORT="${DB_PORT:-3306}"
DB_USER="${DB_USER:-keycloak}"
DB_REGION="${DB_REGION:-${AWS_REGION:-us-west-2}}"

# Generate IAM token
TOKEN=$(aws rds generate-db-auth-token \
    --hostname "$DB_HOST" \
    --port "$DB_PORT" \
    --username "$DB_USER" \
    --region "$DB_REGION" \
    2>/dev/null)

echo "$TOKEN"
```

#### Step4: Update ECS Task Definition for IAM Auth

**File:** `terraform/aws-ecs/keycloak-ecs.tf`
**Lines:** 15-106 (keycloak_container_secrets)

```hcl
locals {
  # Determine if IAM auth is enabled
  use_iam_auth = var.keycloak_use_iam_auth
  
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
    # IAM Auth Mode: Generate token at runtime
    # Password mode: Read from Secrets Manager (legacy)
    local.use_iam_auth ? {
      name      = "KC_DB_PASSWORD"
      valueFrom = "arn:aws:lambda:${data.aws_region.current.id}:${data.aws_caller_identity.current.account_id}:function:keycloak-iam-token-generator"  # Lambda to generate token
    } : {
      name      = "KC_DB_PASSWORD"
      valueFrom = "${aws_secretsmanager_secret.keycloak_db_secret.arn}:password::"
    }
  ]
}
```

**Alternative approach using init container:**

```hcl
container_definitions = jsonencode([
  {
    # Init container to generate IAM token
    name               = "iam-token-generator"
    image              = var.keycloak_image_uri  # Reuse same image
    versionConsistency = "disabled"
    essential          = false
    
    command = ["sh", "-c", <<EOT
aws rds generate-db-auth-token \
    --hostname ${aws_db_proxy.keycloak.endpoint} \
    --port 3306 \
    --username ${var.keycloak_database_username} \
    --region ${var.aws_region} > /tokens/iam_token
EOT
    ]
    
    environment = [
      {
        name  = "AWS_REGION"
        value = var.aws_region
      }
    ]
    
    volumeMounts = [
      {
        name          = "tokens"
        containerPath = "/tokens"
      }
    ]
  },
  
  # Keycloak container
  {
    name               = "keycloak"
    # ... existing configuration ...
    
    secrets = local.use_iam_auth ? [
      # ... other secrets ...
      {
        name      = "KC_DB_PASSWORD"
        valueFrom = "/tokens/iam_token"  # Read from mounted volume
      }
    ] : local.keycloak_container_secrets
    
    volumeMounts = local.use_iam_auth ? [
      {
        name          = "tokens"
        containerPath = "/tokens"
      }
    ] : []
  }
])

volume = [
  {
    name = "tokens"
    efs_volume_configuration = {
      file_system_id     = module.efs.id
      access_point_id    = module.efs.access_points["tokens"].id
      transit_encryption = "ENABLED"
    }
  }
]
```

#### Step 5: Remove Password from Secrets Manager

**File:** `terraform/aws-ecs/keycloak-database.tf`
**Lines:** 268-274 (aws_secretsmanager_secret_version.keycloak_db_secret)

```hcl
resource "aws_secretsmanager_secret_version" "keycloak_db_secret" {
  secret_id = aws_secretsmanager_secret.keycloak_db_secret.id
  secret_string = jsonencode({
    username = var.keycloak_database_username
    # password field removed for IAM auth mode
    # password = var.keycloak_database_password  # DEPRECATED
  })
}
```

**Alternative:** Keep password in secret for backward compatibility but mark as optional:

```hcl
resource "aws_secretsmanager_secret_version" "keycloak_db_secret" {
  secret_id = aws_secretsmanager_secret.keycloak_db_secret.id
  secret_string = jsonencode({
    username        = var.keycloak_database_username
    password        = var.keycloak_database_password  # Keep for fallback
    iam_auth_mode   = var.keycloak_use_iam_auth
    last_updated    = timestamp()
  })
}
```

### Error Handling

The key failure points and responses:

1. **Token Generation Failure**
   - Check AWS credentials are available in ECS task
   - Verify IAM role has `rds-db:connect` permission
   - Check network connectivity to RDS endpoint

2. **Token Expired**
   - IAM tokens expire after 15 minutes
   - For long-running transactions, Keycloak may need to refresh
   - Consider using a sidecar container that refreshes tokens

3. **Database Connection Failure**
   - Verify IAM authentication is enabled on the RDS cluster
   - Check the RDS proxy configuration
   - Verify VPC security groups allow traffic

### Logging

- Log IAM token generation events (success/failure)
- Log token refresh events (for long-running sessions)
- Log database connection events with IAM auth status
- Use structured logging with: `{event: "iam_auth_attempt", status: "success|failure"}`

## Observability
### Tracing / Metrics / Logging Points

| Component | Event | Log Level | Fields |
|-----------|-------|-----------|--------|
| IAM Token Generator | Token generated | DEBUG | `region`, `db_user`, `expiry` |
| IAM Token Generator | Token generation failed | ERROR | `region`, `db_user`, `error` |
| Keycloak Startup | Database connection attempt | INFO | `auth_method: iam` |
| Keycloak Startup | Database connection failed | ERROR | `auth_method: iam`, `error`, `retry_count` |
| Keycloak Runtime | Token refresh | DEBUG | `new_expiry`, `time_to_expiry` |

## Scaling Considerations
- Current load assumptions: Single RDS proxy handles connection pooling
- Horizontal scaling: IAM authentication is stateless; each task generates its own token
- Bottlenecks: Token generation is fast (<100ms); not expected to be a bottleneck
- Caching strategy: Tokens are short-lived; no beneficial caching

## File Changes

### New Files

| File Path | Description |
|-----------|-------------|
| `terraform/aws-ecs/scripts/generate-iam-token.sh` | Script to generate IAM auth tokens |
| `terraform/aws-ecs/iam-auth.tf` | New IAM policy for IAM database authentication |
| `docker/scripts/iam-token-init.sh` | Init script for docker-compose IAM mode |

### Modified Files

| File Path | Lines | Change Description |
|-----------|-------|--------------------|
| `terraform/aws-ecs/keycloak-database.tf` | 48-106 | Enable IAM auth on RDS cluster, update proxy |
| `terraform/aws-ecs/keycloak-ecs.tf` | 169-209 | Add `rds-db:connect` permission to IAM policy |
| `terraform/aws-ecs/variables.tf` | 97-101 | Mark `keycloak_database_password` as deprecated, add `keycloak_use_iam_auth` variable |
| `docker-compose.yml` | 700-767 | Add IAM auth mode options |
| `terraform/aws-ecs/modules/mcp-gateway/iam.tf` | 1-52 | Add IAM auth policy to ECS service module |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| New code (scripts, IAM configs) | ~150 |
| Terraform modifications | ~50 |
| Docker Compose modifications | ~30 |
| Documentation updates | ~100 |
| **Total** | **~330** |

## Testing Strategy
See `testing.md` for comprehensive testing plan.

## Alternatives Considered

### Alternative 1: Keep Secrets Manager + Manual Rotation
**Description:** Continue using Secrets Manager but set up automatic password rotation via Lambda.

**Pros / Cons:**
- Pros: Minimal code changes, leverages existing infrastructure
- Cons: Still involves storing passwords, rotation adds complexity

**Why Rejected:** IAM auth provides better security with less operational overhead.

### Alternative 2: AWS Secrets Manager with Rotation
**Description:** Keep using Secrets Manager but implement automatic rotation.

**Pros / Cons:**
- Pros: Minimal code changes, AWS-managed rotation
- Cons: Password still stored (even if rotated), double encryption layer

**Why Rejected:** IAM auth is the recommended AWS best practice for RDS authentication.

### Alternative 3: Sidecar Container for Token Generation
**Description:** Use a dedicated sidecar container that generates and refreshes IAM tokens.

**Pros / Cons:**
- Pros: Clean separation of concerns, automatic token refresh
- Cons: Adds complexity, requires volume sharing

**Why Rejected (for now):** The init container approach is simpler for this use case.

### Comparison Matrix

| Criteria | Current (Password) | IAM Auth (Chosen) | Secrets Rotation |
|----------|-------------------|-------------------|------------------|
| Security | Medium | High | Medium |
| Operational Overhead | Low | Low | High |
| Code Changes | N/A | ~300 lines | ~100 lines |
| AWS Best Practice | No | Yes | Yes |

## Rollout Plan
- Phase 1: Implementation (out of scope for this skill)
  - Update Terraform files
  - Add IAM auth script
  - Update Docker Compose
- Phase 2: Testing
  - Test IAM auth in staging environment
  - Verify token generation and rotation
  - Test failover scenarios
- Phase 3: Deployment
  - Deploy with IAM auth in production
  - Monitor for issues
  - Optional: Deprecate password-based auth after validation

## Open Questions
1. Should we keep the old password in Secrets Manager for backward compatibility during migration?
2. Do we need a token refresh mechanism for long-running Keycloak sessions?
3. Should the IAM auth mode be configurable via environment variable or Terraform variable?

## References
- [AWS RDS IAM Database Authentication Documentation](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/UsingWithRDS.IAMDBAuth.html)
- [Keycloak Database Configuration](https://www.keycloak.org/server/database)
- [RDS Proxy with IAM Authentication](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/rds-proxy-iam.html)
