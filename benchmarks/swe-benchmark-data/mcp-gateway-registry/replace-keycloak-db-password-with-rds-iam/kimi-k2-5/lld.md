# Low-Level Design: Replace Keycloak Database Password with RDS IAM Authentication

*Created: 2026-06-25*
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
The current MCP Gateway infrastructure uses static password-based authentication for the Keycloak Aurora MySQL database. This requires:
- Terraform-managed passwords stored in state
- A Lambda function for password rotation (30-day cycle)
- Secrets Manager to store and distribute credentials
- RDS Proxy configured with password-based auth

This approach has security and operational drawbacks: static credentials in state files, complex rotation infrastructure, and manual credential management.

### Goals
- Replace static password authentication with RDS IAM authentication
- Eliminate the need for password rotation Lambda
- Remove sensitive credentials from Terraform state
- Use short-lived IAM auth tokens (15-minute validity)
- Maintain backwards compatibility for existing deployments (opt-in)

### Non-Goals
- Changing DocumentDB authentication (separate concern)
- Modifying Keycloak admin credentials (KEYCLOAK_ADMIN)
- Supporting non-Aurora MySQL databases
- Automatic migration of existing deployments

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `terraform/aws-ecs/keycloak-database.tf` | RDS Aurora cluster, RDS Proxy, Secrets Manager | **Primary target**: All database auth configuration |
| `terraform/aws-ecs/keycloak-ecs.tf` | ECS task definition, IAM roles for Keycloak | **Primary target**: Task secrets, IAM policies |
| `terraform/aws-ecs/variables.tf` | Terraform variables | Remove password vars, add IAM auth flag |
| `terraform/aws-ecs/secret-rotation.tf` | Lambda for password rotation | **Remove**: No longer needed with IAM auth |
| `terraform/aws-ecs/secret-rotation-config.tf` | Rotation configuration | **Remove**: No longer needed |
| `terraform/aws-ecs/lambda/rotate-rds/` | Lambda rotation code | **Remove**: No longer needed |
| `docker/keycloak/Dockerfile` | Keycloak image build | **Reference**: Understand entrypoint patterns |
| `charts/keycloak-configure/` | Helm chart for Keycloak config | **Out of scope**: Terraform-focused change |

### Existing Patterns Identified

1. **ECS Secrets Pattern**: Environment variables use `valueFrom` to reference Secrets Manager ARNs
   - Files: `keycloak-ecs.tf` lines 77-105
   - Pattern: `{"name": "KC_DB_PASSWORD", "valueFrom": "${aws_secretsmanager_secret.keycloak_db_secret.arn}:password::"}`

2. **IAM Policy Pattern**: IAM policies embedded in Terraform using `jsonencode`
   - Files: `keycloak-ecs.tf` lines 168-209
   - Pattern: Separate policy resources attached to roles

3. **Conditional Resource Creation**: Uses `count = local.is_aws_documentdb ? 1 : 0` pattern
   - Files: `secret-rotation.tf`
   - Pattern: Feature gating via local variables

4. **RDS Proxy Configuration**: RDS Proxy uses `iam_auth = "DISABLED"` with Secrets Manager auth
   - Files: `keycloak-database.tf` lines 6-28

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| RDS Aurora Cluster | Extends | Enable `iam_database_authentication_enabled` |
| RDS Proxy | Modifies | May need removal or reconfiguration |
| ECS Task Role | Extends | Add `rds-db:connect` permission |
| ECS Task Definition | Modifies | Change KC_DB_PASSWORD source |
| Secrets Manager | Removes | Remove `keycloak_db_secret` resource |
| Lambda Rotation | Removes | Delete rotation function and triggers |

### Constraints and Limitations Discovered

1. **Aurora Serverless v2 IAM Support**: Aurora Serverless v2 supports IAM authentication since 2022
2. **RDS Proxy IAM Auth**: Aurora MySQL RDS Proxy does NOT support IAM authentication (only Aurora PostgreSQL does) - we must connect directly or update the architecture
3. **Token Validity**: RDS IAM auth tokens are valid for 15 minutes only, requiring refresh
4. **MySQL Plugin**: Requires `AWSAuthenticationPlugin` installed on Aurora MySQL (available by default)
5. **Keycloak Restart**: Keycloak must be able to refresh tokens without full restart (or use sidecar pattern)

**Critical Finding**: RDS Proxy for Aurora MySQL does not support IAM authentication. The current architecture uses:
```
Keycloak ECS -> RDS Proxy -> Aurora MySQL (password auth)
```

With IAM auth, we must either:
1. Connect directly: `Keycloak ECS -> Aurora MySQL (IAM auth token)`
2. Keep proxy with fallback auth method

## Architecture

### System Context Diagram (Current)

```
+------------------+     password     +-------------------+     password     +------------------+
|                  | ----------------> |                   | ----------------> |                  |
|  Keycloak ECS    |                   |   RDS Proxy       |                   |  Aurora MySQL    |
|                  |                   |   (SECRETS auth)  |                   |  (static user)   |
+------------------+                   +-------------------+                   +------------------+
        |                                        |
        | reads from                             | reads from
        v                                        v
+-------------------+                   +-------------------+
|                   |                   |                   |
| Secrets Manager   | <---------------| Secrets Manager   |
| (static password) |   Lambda rotates| (same secret)     |
+-------------------+                   +-------------------+
```

### System Context Diagram (Proposed - IAM Auth)

```
+------------------+     IAM auth      +------------------+
|                  |    token (15min)   |                  |
|  Keycloak ECS    | ----------------> |  Aurora MySQL    |
|  (generates      |                   |  (rdsauthuser    |
|   dynamic token) |                   |   IAM user)      |
+------------------+                   +------------------+
        |
        | AssumeRole + rds-db:connect
        v
+-------------------+
|                   |
|   IAM (ECS Task   |
|    Role)          |
+-------------------+

Note: RDS Proxy removed - direct connection to Aurora
```

### Alternative Architecture (With Proxy Fallback)

If RDS Proxy is required for connection pooling:

```
+------------------+     password     +-------------------+     IAM auth     +------------------+
|                  |    (fallback)    |                   |    token         |                  |
|  Keycloak ECS    | ----------------> |   RDS Proxy       | ---------------> |  Aurora MySQL    |
|                  |                   |   (fallback auth) |                  |  (IAM enabled)   |
+------------------+                   +-------------------+                  +------------------+
        |                                        |                          (also has rdsauthuser)
        | can generate token                     |
        v                                        |
+-------------------+                            |
|   IAM (Optional -  |  generates fallback        |
|    fallback)       |  credentials               |
+-------------------+                            v
                                        +-------------------+
                                        | Secrets Manager   |
                                        | (fallback user)   |
                                        +-------------------+
```

**Recommendation**: Remove RDS Proxy for Keycloak's database connection. Keycloak has built-in connection pooling, and Aurora Serverless v2 handles connections well.

### Sequence Diagram: Token Generation Flow

```
Keycloak Container    ECS Task Metadata    AWS SDK/CLI    RDS Endpoint    Aurora MySQL
       |                      |                   |              |              |
       |---- startup -------->|                   |              |              |
       |                      |                   |              |              |
       |<--- credentials -----|                   |              |              |
       |   (task role ARN)   |                   |              |              |
       |                      |                   |              |              |
       |---- generate token --------------------->|              |              |
       |   (using task role)  |                   |              |              |
       |<------------- signed URL (15min) -------|              |              |
       |                      |                   |              |              |
       |---- connect with token -------------------------------->|              |
       |   (token as password)  |                   |              |              |
       |                      |                   |              |------------->|
       |                      |                   |              |  (IAM auth)  |
       |<--------------------------------------- connection established ------>
```

### Component Diagram

```
+-------------------------------------------------------------+
|                    Terraform Configuration                  |
|  +------------------+  +------------------+                  |
|  |  RDS Cluster     |  |  ECS Task Role   |                  |
|  |  (iam_enabled)   |  |  (rds-db:connect)|                  |
|  +------------------+  +------------------+                  |
|                                                             |
|  +------------------+  +------------------+                  |
|  |  RDS Proxy       |  |  ECS Task Def    |                  |
|  |  (REMOVED)       |  |  (token source)  |                  |
|  +------------------+  +------------------+                  |
|                                                             |
+-------------------------------------------------------------+
                            |
                            v
+-------------------------------------------------------------+
|                    Keycloak ECS Task                        |
|  +------------------+  +------------------+                  |
|  | Token Generator  |  | Keycloak Server   |                  |
|  | (entrypoint)     |->| (uses token)      |                  |
|  +------------------+  +------------------+                  |
|                                                             |
+-------------------------------------------------------------+
                            |
                            v RDS connection
                      +------------------+
                      | Aurora MySQL     |
                      | (IAM auth user)  |
                      +------------------+
```

## Data Models

### New Resources

No new Pydantic models needed - this is an infrastructure change.

### Terraform Resource Changes

#### New RDS Cluster IAM Authentication

```hcl
resource "aws_rds_cluster" "keycloak" {
  # ... existing config ...
  
  # Enable IAM database authentication
  iam_database_authentication_enabled = true
}
```

#### DB IAM User Creation (Lambda-backed custom resource)

```python
# Custom resource Lambda - creates IAM database user
def create_iam_db_user(cluster_endpoint, db_name, iam_username):
    """Create MySQL user with AWSAuthenticationPlugin."""
    sql = f"""
    CREATE USER IF NOT EXISTS '{iam_username}'@'%' 
    IDENTIFIED WITH AWSAuthenticationPlugin AS 'RDS';
    
    GRANT ALL PRIVILEGES ON {db_name}.* 
    TO '{iam_username}'@'%';
    
    FLUSH PRIVILEGES;
    """
    # Execute via RDS Data API or direct connection
```

#### IAM Policy for ECS Task

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "rds-db:connect",
      "Resource": "arn:aws:rds-db:${region}:${account}:dbuser:${cluster-resource-id}/${db-iam-username}"
    }
  ]
}
```

## API / CLI Design

### New Terraform Variables

**Variable:** `keycloak_database_iam_auth_enabled`
- **Type:** `bool`
- **Default:** `false`
- **Description:** Enable RDS IAM authentication for Keycloak database

### Removed Terraform Variables

- `keycloak_database_password` (when IAM auth enabled)

### Entrypoint Script Changes

**File:** New file `docker/keycloak/keycloak-entrypoint.sh`

**Invocation:**
Keycloak container entrypoint generates token before starting Keycloak:

```bash
#!/bin/bash
set -e

# Generate RDS IAM auth token
DB_TOKEN=$(aws rds generate-db-auth-token \
  --hostname "${RDS_HOST}" \
  --port 3306 \
  --region "${AWS_REGION}" \
  --username "${DB_IAM_USER}")

# Export as KC_DB_PASSWORD
export KC_DB_PASSWORD="${DB_TOKEN}"

# Start Keycloak with the token as password
exec /opt/keycloak/bin/kc.sh start "$@"
```

## Configuration Parameters

### New Environment Variables

| Variable Name | Type | Default | Required | Description |
|---------------|------|---------|----------|-------------|
| `KC_DB_IAM_AUTH_ENABLED` | bool | `false` | No | Enable IAM authentication for DB |
| `KC_DB_IAM_USER` | string | `keycloak_iam` | No | IAM database username |
| `AWS_REGION` | string | - | Yes | AWS region for token generation |

### Terraform Variable Changes

| Variable | Change | Default |
|----------|--------|---------|
| `keycloak_database_password` | Conditional - optional when IAM auth enabled | `""` |
| `keycloak_database_iam_auth_enabled` | **NEW** | `false` |
| `keycloak_database_iam_user` | **NEW** | `keycloak_iam` |

### Deployment Surface Checklist

All locations requiring `keycloak_database_password` update:

- [ ] `terraform/aws-ecs/variables.tf` - Variable definition
- [ ] `terraform/aws-ecs/keycloak-database.tf` - RDS cluster password (conditional)
- [ ] `terraform/aws-ecs/keycloak-database.tf` - Secrets Manager secret value
- [ ] `.env.example` - Environment variable template
- [ ] `terraform.tfvars.example` - Example variable values
- [ ] Documentation (README, deployment guides)

## New Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `awscli` | `latest` | Token generation in container entrypoint |

If using custom entrypoint script:
- AWS CLI must be available in Keycloak container

**Alternative**: Use AWS SDK for Python/Java to generate tokens without AWS CLI.

## Implementation Details

### Step-by-Step Plan

#### Step 1: Update RDS Cluster to Enable IAM Authentication

**File:** `terraform/aws-ecs/keycloak-database.tf`
**Lines:** ~48-80 (aws_rds_cluster.keycloak)

```hcl
resource "aws_rds_cluster" "keycloak" {
  # ... existing configuration ...
  
  # Enable IAM database authentication (NEW)
  iam_database_authentication_enabled = true
  
  # Make master_password conditional (MODIFY)
  master_password = var.keycloak_database_iam_auth_enabled ? null : var.keycloak_database_password
  
  # ... rest of configuration ...
}
```

**Note:** Aurora MySQL requires the master password for initial setup even with IAM auth enabled. Keep it for cluster creation, then use IAM auth for application connections.

#### Step 2: Create IAM Database User

**File:** New `terraform/aws-ecs/iam-db-user.tf`

Approach: Use a Lambda-backed custom resource or null_resource with local-exec:

```hcl
# Option A: Lambda-backed custom resource (recommended for production)
resource "aws_lambda_function" "iam_db_user_creator" {
  # Lambda that executes SQL to create IAM user
  # Triggered on cluster creation/modification
}

# Option B: null_resource with local-exec (simpler, for dev/test)
resource "null_resource" "iam_db_user" {
  triggers = {
    cluster_id = aws_rds_cluster.keycloak.id
  }
  
  provisioner "local-exec" {
    command = <<-EOT
      aws rds-data execute-statement \
        --resource-arn "${aws_rds_cluster.keycloak.arn}" \
        --secret-arn "${aws_secretsmanager_secret.keycloak_db_secret.arn}" \
        --database "keycloak" \
        --sql "CREATE USER IF NOT EXISTS '${var.keycloak_database_iam_user}'@'%' IDENTIFIED WITH AWSAuthenticationPlugin AS 'RDS'; GRANT ALL PRIVILEGES ON keycloak.* TO '${var.keycloak_database_iam_user}'@'%';"
    EOT
  }
  
  depends_on = [aws_rds_cluster.keycloak]
}
```

**Recommendation**: Use Option A with RDS Data API for cleaner separation of concerns.

#### Step 3: Update ECS Task IAM Role

**File:** `terraform/aws-ecs/keycloak-ecs.tf`
**Lines:** ~231-274 (aws_iam_role.keycloak_task_role)

Add policy for RDS IAM auth:

```hcl
# New policy resource
resource "aws_iam_role_policy" "keycloak_task_rds_iam_auth" {
  count = var.keycloak_database_iam_auth_enabled ? 1 : 0
  
  name = "keycloak-task-rds-iam-auth"
  role = aws_iam_role.keycloak_task_role.id
  
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = "rds-db:connect"
        Resource = "arn:aws:rds-db:${var.aws_region}:${data.aws_caller_identity.current.account_id}:dbuser:${aws_rds_cluster.keycloak.cluster_resource_id}/${var.keycloak_database_iam_user}"
      }
    ]
  })
}
```

#### Step 4: Update ECS Task Definition

**File:** `terraform/aws-ecs/keycloak-ecs.tf`
**Lines:** ~276-340 (aws_ecs_task_definition.keycloak)

Modify container definition to support IAM auth:

```hcl
# Modify keycloak_container_secrets in locals block
keycloak_container_secrets = concat(
  [
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
  ],
  # Conditional: use IAM auth or password auth
  var.keycloak_database_iam_auth_enabled ? [
    {
      name      = "KC_DB_USERNAME"
      value     = var.keycloak_database_iam_user  # IAM username
    },
    # KC_DB_PASSWORD will be set by entrypoint script
  ] : [
    {
      name      = "KC_DB_USERNAME"
      valueFrom = "${aws_secretsmanager_secret.keycloak_db_secret.arn}:username::"
    },
    {
      name      = "KC_DB_PASSWORD"
      valueFrom = "${aws_secretsmanager_secret.keycloak_db_secret.arn}:password::"
    },
  ]
)

# Add new environment variables for IAM auth
keycloak_container_env = concat(
  local.keycloak_container_env_base,
  var.keycloak_database_iam_auth_enabled ? [
    {
      name  = "KC_DB_IAM_AUTH_ENABLED"
      value = "true"
    },
    {
      name  = "KC_DB_IAM_USER"
      value = var.keycloak_database_iam_user
    }
  ] : []
)
```

#### Step 5: Create Entrypoint Script

**File:** New `docker/keycloak/keycloak-entrypoint.sh`

```bash
#!/bin/bash
set -e

# Keycloak entrypoint with RDS IAM authentication support
# Generates IAM auth token before starting Keycloak

if [[ "${KC_DB_IAM_AUTH_ENABLED}" == "true" ]]; then
    echo "Generating RDS IAM authentication token..."
    
    # Extract hostname from KC_DB_URL or use direct value
    # Expected format: jdbc:mysql://hostname:3306/database
    if [[ -n "${KC_DB_URL}" ]]; then
        # Extract hostname from JDBC URL
        DB_HOST=$(echo "${KC_DB_URL}" | sed -n 's/jdbc:mysql:\/\/\([^:]*\):.*/\1/p')
    else
        echo "Error: KC_DB_URL not set"
        exit 1
    fi
    
    # Generate IAM auth token using AWS CLI
    echo "Requesting auth token for user: ${KC_DB_IAM_USER}"
    KC_DB_PASSWORD=$(aws rds generate-db-auth-token \
        --hostname "${DB_HOST}" \
        --port 3306 \
        --region "${AWS_REGION}" \
        --username "${KC_DB_IAM_USER}" \
        2>/dev/null)
    
    if [[ -z "${KC_DB_PASSWORD}" ]]; then
        echo "Error: Failed to generate RDS auth token"
        exit 1
    fi
    
    export KC_DB_PASSWORD
    echo "RDS IAM auth token generated successfully"
    
    # Set username to IAM user
    export KC_DB_USERNAME="${KC_DB_IAM_USER}"
fi

# Start Keycloak
exec /opt/keycloak/bin/kc.sh start "$@"
```

**File:** Update `docker/keycloak/Dockerfile`

```dockerfile
FROM quay.io/keycloak/keycloak:25.0 as builder
# ... existing builder stage ...

FROM quay.io/keycloak/keycloak:25.0

# Install AWS CLI (required for token generation)
USER root
RUN apt-get update && apt-get install -y \
    awscli \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/keycloak/ /opt/keycloak/

# Copy custom entrypoint
COPY keycloak-entrypoint.sh /opt/keycloak/bin/
RUN chmod +x /opt/keycloak/bin/keycloak-entrypoint.sh

WORKDIR /opt/keycloak
USER keycloak

ENTRYPOINT ["/opt/keycloak/bin/keycloak-entrypoint.sh"]
CMD ["start"]
```

#### Step 6: Update ECS Task Execution Policy

**File:** `terraform/aws-ecs/keycloak-ecs.tf`
**Lines:** ~166-209 (keycloak_task_exec_ssm_policy)

If using AWS CLI in container, the task execution role doesn't need changes (task role handles it). But if using sidecar approach, add policy to task execution role.

#### Step 7: Remove or Disable RDS Proxy

**File:** `terraform/aws-ecs/keycloak-database.tf`

Option A: Add `count` to disable (allows rollback):

```hcl
resource "aws_db_proxy" "keycloak" {
  count = var.keycloak_database_iam_auth_enabled ? 0 : 1
  # ... existing configuration ...
}

resource "aws_db_proxy_target" "keycloak" {
  count = var.keycloak_database_iam_auth_enabled ? 0 : 1
  # ... existing configuration ...
}
```

Option B: Remove entirely (cleaner, but no rollback):

Delete `aws_db_proxy` and `aws_db_proxy_target` resources.

#### Step 8: Remove Password Rotation Lambda

**File:** `terraform/aws-ecs/secret-rotation.tf`

When IAM auth is enabled, disable rotation:

```hcl
resource "aws_secretsmanager_secret_rotation" "keycloak_db_secret" {
  count = var.keycloak_database_iam_auth_enabled ? 0 : 1
  
  secret_id           = aws_secretsmanager_secret.keycloak_db_secret.id
  rotation_lambda_arn = aws_lambda_function.rds_rotation.arn
  rotation_rules {
    automatically_after_days = 30
  }
  depends_on = [
    aws_lambda_permission.rds_rotation,
    aws_secretsmanager_secret_version.keycloak_db_secret
  ]
}
```

**File:** `terraform/aws-ecs/keycloak-database.tf`

Make `aws_secretsmanager_secret_version` conditional:

```hcl
resource "aws_secretsmanager_secret_version" "keycloak_db_secret" {
  count = var.keycloak_database_iam_auth_enabled ? 0 : 1
  
  secret_id = aws_secretsmanager_secret.keycloak_db_secret.id
  secret_string = jsonencode({
    username = var.keycloak_database_username
    password = var.keycloak_database_password
  })
}
```

#### Step 9: Update Database Connection String

**File:** `terraform/aws-ecs/keycloak-database.tf`
**Lines:** ~277-283 (aws_ssm_parameter.keycloak_database_url)

With RDS Proxy removed, update the connection URL:

```hcl
resource "aws_ssm_parameter" "keycloak_database_url" {
  name   = "/keycloak/database/url"
  type   = "SecureString"
  key_id = aws_kms_key.rds.id
  value  = var.keycloak_database_iam_auth_enabled ? \
    "jdbc:mysql://${aws_rds_cluster.keycloak.endpoint}:3306/keycloak" : \
    "jdbc:mysql://${aws_rds_cluster.keycloak.endpoint}:3306/keycloak"
  # Note: URL format is the same, but endpoint changes if using proxy
  tags   = local.common_tags
}
```

If keeping RDS Proxy:
```hcl
value  = var.keycloak_database_iam_auth_enabled ? \
  "jdbc:mysql://${aws_rds_cluster.keycloak.endpoint}:3306/keycloak" : \
  "jdbc:mysql://${aws_db_proxy.keycloak[0].endpoint}:3306/keycloak"
```

#### Step 10: Add Terraform Variables

**File:** `terraform/aws-ecs/variables.tf`

Add new variables:

```hcl
variable "keycloak_database_iam_auth_enabled" {
  description = "Enable RDS IAM authentication for Keycloak database"
  type        = bool
  default     = false
}

variable "keycloak_database_iam_user" {
  description = "IAM database username for Keycloak"
  type        = string
  default     = "keycloak_iam"
}

# Make password optional
variable "keycloak_database_password" {
  description = "Keycloak database password (not required when using IAM auth)"
  type        = string
  sensitive   = true
  default     = ""
}
```

#### Step 11: Add Validation

**File:** `terraform/aws-ecs/main.tf` or `terraform/aws-ecs/variables.tf`

Add conditional validation:

```hcl
resource "terraform_data" "keycloak_db_password_validation" {
  lifecycle {
    precondition {
      condition     = var.keycloak_database_iam_auth_enabled || var.keycloak_database_password != ""
      error_message = "keycloak_database_password is required when keycloak_database_iam_auth_enabled is false"
    }
  }
}
```

### Error Handling

1. **Token Generation Failure**: Entrypoint script exits with error code, container restart triggered by ECS
2. **Database Connection Failure**: Keycloak's built-in health checks handle this
3. **IAM Permission Denied**: CloudWatch logs show AWS CLI error with specific permission issue

### Logging

- Log IAM auth token generation attempts (without logging the actual token)
- Log database connection attempts with IAM user name
- Log token refresh events (if implementing refresh)

## Observability

### Tracing / Metrics / Logging Points

| Location | Event | Log Level |
|----------|-------|-----------|
| Entrypoint script | "Generating RDS IAM authentication token" | INFO |
| Entrypoint script | "RDS IAM auth token generated successfully" | INFO |
| Entrypoint script | "Error: Failed to generate RDS auth token" | ERROR |
| Terraform | IAM policy attachment changes | INFO (apply) |
| CloudTrail | rds-db:connect API calls | Audit |

### CloudWatch Alarms

- Keycloak task restart rate (increase may indicate token issues)
- Database connection failure count

## Scaling Considerations

- **Token Validity**: 15 minutes. Keycloak maintains persistent connections, should not need frequent re-tokens
- **Connection Pooling**: Remove RDS Proxy, rely on Keycloak's built-in connection pool
- **Horizontal Scaling**: Each ECS task generates its own token using the task role
- **Cold Start**: Token generation adds ~1-2 seconds to container startup

## File Changes

### New Files

| File Path | Description |
|-----------|-------------|
| `terraform/aws-ecs/iam-db-user.tf` | IAM database user creation (Lambda or custom resource) |
| `docker/keycloak/keycloak-entrypoint.sh` | Custom entrypoint with token generation |

### Modified Files

| File Path | Lines | Change Description |
|-----------|-------|--------------------|
| `terraform/aws-ecs/keycloak-database.tf` | ~48 | Add `iam_database_authentication_enabled = true` |
| `terraform/aws-ecs/keycloak-database.tf` | ~6-28 | Make RDS Proxy conditional (count = 0 when IAM auth) |
| `terraform/aws-ecs/keycloak-database.tf` | ~268 | Make secret version conditional |
| `terraform/aws-ecs/keycloak-ecs.tf` | ~231 | Add IAM auth policy to task role |
| `terraform/aws-ecs/keycloak-ecs.tf` | ~276 | Update task definition secrets/env |
| `terraform/aws-ecs/variables.tf` | ~90 | Add IAM auth variables, make password optional |
| `terraform/aws-ecs/secret-rotation.tf` | ~35 | Make rotation configuration conditional |
| `docker/keycloak/Dockerfile` | ~1 | Update entrypoint, install AWS CLI |

### Removed Resources (Conditional)

| Resource | Condition |
|----------|-----------|
| `aws_db_proxy.keycloak` | When `keycloak_database_iam_auth_enabled = true` |
| `aws_db_proxy_target.keycloak` | When `keycloak_database_iam_auth_enabled = true` |
| `aws_secretsmanager_secret_rotation.keycloak_db_secret` | When `keycloak_database_iam_auth_enabled = true` |
| `aws_lambda_function.rds_rotation` | Optional - can keep for fallback |
| `aws_secretsmanager_secret_version.keycloak_db_secret` | When `keycloak_database_iam_auth_enabled = true` |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| New code | ~200 |
| Modified code | ~100 |
| Removed (conditional) | ~150 (gated by flag) |
| **Total** | **~450** |

## Testing Strategy

See `testing.md` for detailed testing plan.

## Alternatives Considered

### Alternative 1: Keep RDS Proxy with Fallback

**Description**: Keep RDS Proxy for connection pooling, configure it with native password auth as a fallback mechanism

**Pros:**
- Maintains connection pooling benefits
- Graceful degradation if IAM auth fails

**Cons:**
- RDS Proxy doesn't support IAM auth for Aurora MySQL
- Still requires some password management
- Adds complexity with dual auth paths

**Why Rejected**: RDS Proxy for Aurora MySQL does not support IAM authentication at all. Must choose between proxy or IAM auth.

### Alternative 2: Sidecar Container Pattern

**Description**: Separate container generates tokens and writes to shared volume

**Pros:**
- Cleaner separation of concerns
- Could implement token refresh without restart

**Cons:**
- More complex ECS task definition
- Requires shared volume configuration
- Keycloak would need hot-reload support for credentials

**Why Rejected**: Keycloak requires restart to pick up new credentials anyway. Sidecar adds complexity without benefit.

### Alternative 3: AWS Secrets Manager Rotation with IAM Token

**Description**: Use Secrets Manager to store IAM tokens, rotated every 15 minutes by Lambda

**Pros:**
- Existing Secret Manager integration
- No changes to Keycloak container

**Cons:**
- Still requires rotation Lambda (not eliminated)
- 15-minute rotation is too frequent
- Tokens are only valid for 15 minutes anyway

**Why Rejected**: Defeats the purpose of eliminating rotation. Tokens expire before rotation completes.

### Comparison Matrix

| Criteria | Chosen (Direct) | Keep Proxy | Sidecar | Secrets Rotation |
|----------|-----------------|------------|---------|------------------|
| Complexity | Low | Med | High | Med |
| Security | High | Med | High | Med |
| Operations Overhead | Low | Med | Med | High |
| Connection Pooling | Built-in | Proxy | Built-in | Built-in |
| Token Freshness | On-start | Static | Configurable | 15min (max) |

## Rollout Plan

### Phase 1: Preparation
1. Create IAM database user creation Lambda
2. Update Terraform with conditional IAM auth flag
3. Build new Keycloak container with AWS CLI

### Phase 2: Testing
1. Deploy to dev environment with `keycloak_database_iam_auth_enabled = false`
2. Verify existing deployment still works
3. Deploy with `keycloak_database_iam_auth_enabled = true` to test environment
4. Verify IAM auth works end-to-end

### Phase 3: Production Rollout
1. Set `keycloak_database_iam_auth_enabled = true` in production
2. Run Terraform apply
3. Monitor Keycloak logs for successful token generation
4. Monitor CloudTrail for successful rds-db:connect calls

### Phase 4: Cleanup
1. After stable period, remove password-based auth code paths
2. Remove RDS Proxy resources
3. Remove rotation Lambda

## Open Questions

1. **HA Support**: How does this work with multiple Keycloak replica tasks? Each generates its own token - should work fine.

2. **Token Refresh**: Keycloak doesn't support credential refresh. If connection drops after 15 minutes, will it reconnect automatically? Aurora connections >15min stay valid, even after token expiry. New connections use fresh token on startup.

3. **Password Fallback**: Should we keep the master password as a fallback? Aurora supports multiple auth methods simultaneously.

4. **Backup/Restore**: How does this affect database backup/restore procedures? No impact - backups don't include IAM users (they're重建 via custom resource).

## References

- [RDS IAM Authentication Documentation](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/UsingWithRDS.IAMDBAuth.html)
- [Aurora Serverless v2 IAM Auth](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-serverless-v2.iam-auth.html)
- [RDS Proxy Limitations](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/rds-proxy.how-it-works.html#rds-proxy.limitations)
- Keycloak Documentation: Database Configuration
