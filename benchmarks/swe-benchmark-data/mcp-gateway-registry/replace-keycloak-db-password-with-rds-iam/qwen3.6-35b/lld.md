# Low-Level Design: Replace Keycloak DB Password with RDS IAM Authentication

*Created: 2026-07-22*
*Author: Claude (qwen3.6-35b)*
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

The Keycloak ECS task on AWS uses a static database password stored in AWS Secrets Manager (`keycloak/database`) to connect to its Aurora MySQL Serverless v2 cluster. The password is read from Secrets Manager via ECS container secrets and passed as `KC_DB_PASSWORD` to the Keycloak container. The RDS Proxy is configured with `auth_scheme = "SECRETS"` and `iam_auth = "DISABLED"`. The Terraform has a checkov skip `CKV_AWS_162` explicitly acknowledging that IAM database authentication is not used.

Key issues with this approach:
- Static credentials persist in Secrets Manager, ECS task definitions, and Keycloak's JDBC pool.
- Secret rotation Lambda adds operational overhead and has drift risk (Issue #1026).
- Compliance gap: security frameworks flag the absence of IAM database auth.

### Goals

- Replace static password authentication with RDS IAM Database Authentication for the Keycloak Aurora MySQL cluster on ECS.
- Add a `KEYCLOAK_DB_IAM_AUTH_ENABLED` feature flag with default `false` so password auth remains available as a fallback without redeployment.
- Remove static DB credentials from the ECS task when IAM auth is active.
- Update the RDS Proxy to support IAM authentication.
- Attach a scoped `rds-db:connect` IAM policy to the Keycloak ECS task role.
- Do NOT change the Keycloak Docker image (stays at `quay.io/keycloak/keycloak:25.0`).

### Non-Goals

- Migrating other databases (DocumentDB, registry MongoDB) to IAM auth.
- Changing the Keycloak Docker image (still `quay.io/keycloak/keycloak:25.0`).
- Updating the docker-compose local development setup (uses PostgreSQL, not MySQL).
- Helm/EKS deployment support (only ECS + Terraform, per task scope).
- Performance benchmarking of IAM auth vs password auth.

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `terraform/aws-ecs/keycloak-database.tf` | Aurora MySQL cluster, RDS Proxy, Secrets Manager secret, KMS key, SSM params | Primary file: enable IAM auth at user level, update RDS Proxy auth block, remove secret, update SSM URL with SSL params, remove checkov skip |
| `terraform/aws-ecs/keycloak-ecs.tf` | ECS cluster, task definition, task/execution roles, container env/secrets | Add IAM auth policy, add feature flag env var, update container command for token generation, remove KC_DB_PASSWORD from secrets when IAM auth active |
| `terraform/aws-ecs/variables.tf` | Variable definitions | Add `keycloak_db_iam_auth_enabled`, deprecate `keycloak_database_password` |
| `terraform/aws-ecs/locals.tf` | Local variables | Add `keycloak_db_user_arn` local for IAM policy resource ARN |
| `terraform/aws-ecs/secret-rotation.tf` | Secret rotation Lambda | Remove rotate-rds Lambda (IAM auth eliminates need for password rotation) |
| `terraform/aws-ecs/secret-rotation-config.tf` | Rotation config | Remove Keycloak DB secret rotation config |
| `docker/keycloak/Dockerfile` | Keycloak image for ECS | No change needed; the wrapper script is inline in the ECS task definition |
| `docker-compose.yml` | Local development | No change needed; uses PostgreSQL with password auth |
| `.env.example` | Environment variable reference (~1300 lines) | Add `KEYCLOAK_DB_IAM_AUTH_ENABLED` entry |

### Existing Patterns Identified

1. **Terraform variable conventions**: Sensitive variables use `type = string`, `sensitive = true`. Non-sensitive booleans use `type = bool` with `default = true/false`. Variables for feature flags follow the pattern `enable_*` (e.g., `keycloak_iam_auth_enabled`).

2. **Secrets Manager injection**: ECS task reads secrets via `valueFrom = "${secret-arn}:key::"` syntax in the container's `secrets` block. Example from keycloak-ecs.tf lines 97-104:
   ```hcl
   {
     name      = "KC_DB_USERNAME"
     valueFrom = "${aws_secretsmanager_secret.keycloak_db_secret.arn}:username::"
   }
   ```

3. **RDS Proxy config**: Currently uses `auth_scheme = "SECRETS"` with `client_password_auth_type = "MYSQL_CACHING_SHA2_PASSWORD"` and `iam_auth = "DISABLED"`. The proxy IAM role has `secretsmanager:GetSecretValue` permission for the Keycloak DB secret.

4. **ECS task role pattern**: The `keycloak_task_role` (`keycloak-ecs.tf` line 232) has SSM Session Manager permissions. The `keycloak_task_exec_role` (`keycloak-ecs.tf` line 141) has SSM read + Secrets Manager read + KMS decrypt. New policies should attach to the task role, not the exec role.

5. **IAM policy scoping**: Policies use `jsonencode()` with specific resource ARNs, not wildcards. Example: `rds-db:connect` on `local.keycloak_db_user_arn`.

6. **Checkov skip pattern**: `keycloak-database.tf` line 43: `#checkov:skip=CKV_AWS_162:IAM database authentication not used - Keycloak uses password auth`. This skip should be removed when IAM auth is enabled.

7. **Container command**: Current ECS task uses `command = ["start"]` (line 297 of keycloak-ecs.tf), which runs as `kc.sh start` because the image's entrypoint is `kc.sh`. The stock image does not include the AWS CLI.

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| Aurora MySQL cluster | Modifies | MySQL user `keycloak` must be created/updated with `AWSAuthenticationPlugin` |
| RDS Proxy | Modifies | Auth block changes from `SECRETS`/`DISABLED` to `DEFAULT`/`ENABLED` |
| ECS task definition | Modifies | New entrypoint wrapper, IAM auth policy, feature flag env, removed KC_DB_PASSWORD secret |
| ECS task role | Modifies | New `rds-db:connect` IAM policy scoped to Keycloak DB user ARN |
| Secrets Manager | Modifies | `keycloak/database` secret removed (when IAM auth enabled) |
| Rotation Lambda | Modifies | `rotate-rds` Lambda removed (IAM auth eliminates password rotation need) |
| SSM parameters | Modifies | `KC_DB_URL` SSM param updated with SSL parameters |

### Constraints and Limitations Discovered

1. **RDS Proxy auth_scheme for MySQL is critical**: For MySQL proxies, `auth_scheme = "AWS_IAM"` is NOT valid -- it only works for PostgreSQL and SQL Server. The correct MySQL configuration is `auth_scheme = "DEFAULT"` with `iam_auth = "ENABLED"`. The `DEFAULT` scheme supports both password and IAM token authentication for client connections.

2. **IAM auth is enabled at the MySQL user level, not the cluster level**: Aurora MySQL does NOT have a Terraform `iam_auth` attribute on `aws_rds_cluster`. IAM authentication is enabled by creating the MySQL user with `AWSAuthenticationPlugin as IAM`. There is also no `enable_http_authentication` flag for this purpose -- that controls the HTTP interface (a serverless v2 feature), unrelated to IAM auth.

3. **One-time SQL required for MySQL IAM user**: The user must be created/updated via SQL:
   ```sql
   CREATE USER keycloak IDENTIFIED WITH AWSAuthenticationPlugin as IAM IAMAuth ENABLE;
   ```
   This cannot be done via Terraform natively. A `null_resource` with `local-exec` provisioner using the MySQL client can handle this, or it must be a documented manual step.

4. **ECS task needs AWS CLI for token generation**: The stock `quay.io/keycloak/keycloak:25.0` image does NOT include the AWS CLI. Options: (a) install at runtime via a wrapper script in the ECS `command` field, (b) use a custom image, (c) use a sidecar init container. Option (a) adds ~10 seconds to first boot.

5. **Token TTL is 15 minutes**: Generated IAM auth tokens are valid for 15 minutes. Keycloak's JDBC connection pool holds connections for longer. Options: restart the container to regenerate the token, or implement a background refresh. For this design, the 15-minute window is acceptable -- ECS Fargate tasks are naturally replaced on health check failures, and a container restart during the 15-minute window generates a fresh token.

6. **`auth_scheme` change forces RDS Proxy re-creation**: Changing from `SECRETS` to `DEFAULT` is an immutable field change in AWS, which forces the proxy to be destroyed and recreated. This causes a brief connectivity gap (5-10 minutes). Plan as a maintenance window operation.

7. **Existing RDS clusters require `master_password`**: AWS does not allow removing `master_password` from an existing `aws_rds_cluster`. The field remains in the Terraform resource but is not used for the Keycloak application user -- only for the master account.

8. **docker-compose uses PostgreSQL, not MySQL**: The local development stack uses `keycloak-db` with PostgreSQL. IAM authentication is AWS-specific and does not apply to the local development setup.

## Architecture

### System Context Diagram

```
+------------------------------------------------------------------+
|                        AWS VPC                                   |
|                                                                  |
|  +------------------------------------------------------------+  |
|  |  ECS Task (Fargate)                                        |  |
|  |  keycloak-task-role: rds-db:connect                        |  |
|  |                                                            |  |
|  |  +----------------------------------------------------+    |  |
|  |  | Entrypoint Wrapper                                 |    |  |
|  |  | [IAM auth] aws rds generate-db-auth-token ->       |    |  |
|  |  |                    KC_DB_PASSWORD                   |    |  |
|  |  | [Password auth] read KC_DB_PASSWORD from secrets   |    |  |
|  |  +--------------------------+-------------------------+    |  |
|  |                             |                               |  |
|  |  +--------------------------v-------------------------+    |  |
|  |  | Keycloak (quay.io/keycloak/keycloak:25.0)         |    |  |
|  |  | kc.sh start                                        |    |  |
|  |  | Reads KC_DB_URL, KC_DB_USERNAME, KC_DB_PASSWORD   |    |  |
|  |  +---------------------------------------------------+    |  |
|  +------------------------------------------------------------+  |
|                                  | JDBC                         |
|                                  v                              |
|  +------------------------------------------------------------+  |
|  |  RDS Proxy (keycloak-proxy)                                |  |
|  |  auth_scheme = "DEFAULT"                                   |  |
|  |  iam_auth = "ENABLED"                                      |  |
|  +--------------------------+----------------------------------+  |
|                             |                                  |
|                             v                                  |
|  +------------------------------------------------------------+  |
|  |  Aurora MySQL Serverless v2 (keycloak)                     |  |
|  |  User: keycloak IDENTIFIED WITH AWSAuthenticationPlugin    |  |
|  |  Engine: 8.0.mysql_aurora.3.10.3                           |  |
|  +------------------------------------------------------------+  |
+------------------------------------------------------------------+
```

### Sequence Diagram

```
[Step 1] Terraform Apply
    |
    +-- [Manual] Create MySQL IAM user via SQL
    |
    +-- Update RDS cluster parameters
    +-- Update RDS Proxy (forces destroy/recreate - brief outage)
    +-- Update ECS task definition
    +-- Attach IAM policy to task role
    |
    v
[Step 2] ECS Task Starts
    |
    +-- [KEYCLOAK_DB_IAM_AUTH_ENABLED=true]
    |   |
    |   +-- Wrapper installs AWS CLI (if missing)
    |   +-- Generates IAM auth token:
    |   |      aws rds generate-db-auth-token \
    |   |        --hostname <proxy-endpoint> \
    |   |        --port 3306 \
    |   |        --username keycloak \
    |   |        --region <AWS_REGION>
    |   +-- Exports KC_DB_PASSWORD=$TOKEN
    |
    +-- [KEYCLOAK_DB_IAM_AUTH_ENABLED=false]
    |   +-- KC_DB_PASSWORD from ECS secrets (Secrets Manager)
    |
    v
[Step 3] Keycloak starts (kc.sh start)
    |
    +-- JDBC connects via RDS Proxy
    |
    +-- [IAM mode] RDS Proxy validates IAM token via AWS
    +-- [Password mode] RDS Proxy validates password
    |
    v
[Step 4] Keycloak running and serving OIDC requests
    |
    +-- Every 10-12 min: ECS task restarts (Fargate health checks)
    |   -> Fresh IAM token generated on next start
```

### Component Diagram

```
  +--------------------------------------------------------------+
  |  terraform/aws-ecs/                                          |
  |                                                              |
  |  +----------------------------------------+                  |
  |  | keycloak-database.tf                   |                  |
  |  | - RDS Cluster: keep master_password    |                  |
  |  | - MySQL IAM user: CREATE/ALTER USER    |                  |
  |  |   with AWSAuthenticationPlugin         |                  |
  |  | - RDS Proxy: auth=DEFAULT, iam=ENABLED |                  |
  |  | - SSM: KC_DB_URL with SSL params       |                  |
  |  | - Secret: removed (iam mode)           |                  |
  |  | - Checkov CKV_AWS_162: removed         |                  |
  |  +---------------------------+            |                  |
  |  +----------------------------------------+                  |
  |  | keycloak-ecs.tf                          |                  |
  |  | - New policy: keycloak-rds-db-auth       |                  |
  |  | - Container env: KEYCLOAK_DB_IAM_AUTH    |                  |
  |  | - Container command: wrapper + token gen |                  |
  |  | - Container secrets: KC_DB_PASSWORD      |                  |
  |  |   conditional on feature flag            |                  |
  |  +---------------------------+            |                  |
  |  +----------------------------------------+                  |
  |  | variables.tf                             |                  |
  |  | - keycloak_db_iam_auth_enabled added     |                  |
  |  +---------------------------+            |                  |
  |  +----------------------------------------+                  |
  |  | locals.tf                                |                  |
  |  | - keycloak_db_user_arn added             |                  |
  |  +---------------------------+            |                  |
  |  +----------------------------------------+                  |
  |  | secret-rotation.tf                       |                  |
  |  | - rotate-rds Lambda removed (iam mode)   |                  |
  |  +----------------------------------------+                  |
  +--------------------------------------------------------------+
```

## Data Models

### Terraform Variable Changes

**Add to `variables.tf`:**
```hcl
variable "keycloak_db_iam_auth_enabled" {
  description = "Enable RDS IAM database authentication for Keycloak. When true, the ECS task generates short-lived IAM auth tokens at startup. When false, falls back to password auth from Secrets Manager."
  type        = bool
  default     = false
}
```

**Deprecate `keycloak_database_password` (keep for fallback mode):**
```hcl
variable "keycloak_database_password" {
  description = "[DEPRECATED] Keycloak database password. Required only when keycloak_db_iam_auth_enabled is false."
  type        = string
  sensitive   = true
  default     = null
}
```

### New Terraform Locals

**Add to `locals.tf` (or inline in keycloak-ecs.tf):**
```hcl
locals {
  # ARN for rds-db:connect IAM policy - scoped to the Keycloak DB user
  keycloak_db_user_arn = "arn:aws:rds-db:${var.aws_region}:${data.aws_caller_identity.current.account_id}:dbuser:${aws_rds_cluster.keycloak.cluster_identifier}/${var.keycloak_database_username}"
}
```

### ECS Task Environment Variable Changes

| Variable | Before (always) | After (IAM auth = true) | After (IAM auth = false) |
|----------|-----------------|-------------------------|--------------------------|
| `KC_DB_URL` | From SSM | From SSM (+ SSL params) | From SSM (+ SSL params) |
| `KC_DB_USERNAME` | From Secrets Manager | From Secrets Manager (kept) | From Secrets Manager |
| `KC_DB_PASSWORD` | From Secrets Manager | Generated by wrapper script | From Secrets Manager |
| `KEYCLOAK_DB_IAM_AUTH_ENABLED` | N/A | `"true"` | `"false"` |

### ECS Task Secrets Changes

When `keycloak_db_iam_auth_enabled = true`:
- Remove `KC_DB_PASSWORD` from the `secrets` block (wrapper generates it).
- Keep `KEYCLOAK_ADMIN`, `KEYCLOAK_ADMIN_PASSWORD`, `KC_DB_URL`, `KC_DB_USERNAME`.

When `keycloak_db_iam_auth_enabled = false`:
- Keep all existing secrets including `KC_DB_PASSWORD` from Secrets Manager.

## API / CLI Design

No new CLI commands or API endpoints. This is an infrastructure-only change.

### Container Command Change

The current ECS task definition uses `command = ["start"]` (keycloak-ecs.tf line 297). This runs the stock image's entrypoint `kc.sh start`.

When IAM auth is enabled, the command changes to an inline shell wrapper:

```json
[
  "/bin/sh", "-c",
  "set -e; " +
  "if [ \"${KEYCLOAK_DB_IAM_AUTH_ENABLED:-false}\" = \"true\" ]; then " +
  "  if ! command -v aws >/dev/null 2>&1; then " +
  "    (command -v pip3 >/dev/null 2>&1 && pip3 install --quiet awscli) || " +
  "    (command -v pip >/dev/null 2>&1 && pip install --quiet awscli) || " +
  "    (apk add --no-cache awscli 2>/dev/null) || " +
  "    (apt-get update -qq && apt-get install -y -qq awscli) || " +
  "    echo 'WARNING: AWS CLI not available, IAM auth token generation will fail'; " +
  "  fi; " +
  "  TOKEN=$(aws rds generate-db-auth-token " +
  "    --hostname ${aws_rds_cluster.keycloak.endpoint} " +
  "    --port 3306 " +
  "    --username ${var.keycloak_database_username} " +
  "    --region ${var.aws_region}) && " +
  "  export KC_DB_PASSWORD=\"$TOKEN\"; " +
  "fi; " +
  "exec /opt/keycloak/bin/kc.sh start"
]
```

This inline wrapper:
1. Checks if `KEYCLOAK_DB_IAM_AUTH_ENABLED` is `"true"`.
2. If so, installs the AWS CLI if not already present (tries pip3, pip, apk, apt in order of availability).
3. Generates an IAM auth token via `aws rds generate-db-auth-token`.
4. Exports `KC_DB_PASSWORD` with the token value.
5. Executes `kc.sh start` with the token set.

## Configuration Parameters

### New Environment Variables

| Variable Name | Type | Default | Required | Description |
|---------------|------|---------|----------|-------------|
| `KEYCLOAK_DB_IAM_AUTH_ENABLED` | bool | `false` | No | Enable RDS IAM auth for Keycloak DB connection. When false, falls back to password auth from Secrets Manager. |

### Terraform Variable Changes

| Variable | Before | After |
|----------|--------|-------|
| `keycloak_db_iam_auth_enabled` | N/A | `bool`, default `false` |
| `keycloak_database_password` | `sensitive = true`, no default | `sensitive = true`, `default = null` (deprecated) |

### Deployment Surface Checklist

- [ ] `terraform/aws-ecs/keycloak-database.tf` -- cluster (no cluster-level IAM flag for MySQL), RDS Proxy auth block update, SSM URL with SSL params, secrets removal, checkov skip removal
- [ ] `terraform/aws-ecs/keycloak-ecs.tf` -- new IAM policy, container command, container env/secrets, conditional secret block
- [ ] `terraform/aws-ecs/variables.tf` -- add `keycloak_db_iam_auth_enabled`, deprecate `keycloak_database_password`
- [ ] `terraform/aws-ecs/locals.tf` -- add `keycloak_db_user_arn` local
- [ ] `terraform/aws-ecs/secret-rotation.tf` -- remove rotate-rds Lambda and associated resources
- [ ] `terraform/aws-ecs/secret-rotation-config.tf` -- remove rotation config for Keycloak DB secret
- [ ] `docker-compose.yml` -- no changes (PostgreSQL for local dev)
- [ ] `.env.example` -- add `KEYCLOAK_DB_IAM_AUTH_ENABLED` entry

## New Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| AWS CLI | Bundled with pip/apk/apt | Generate RDS IAM auth tokens at container startup |

No new Terraform providers, Python packages, or Helm charts are required. The AWS CLI is installed at runtime in the container when IAM auth is enabled. The stock Keycloak image is based on a Linux distribution that supports at least one of pip3, pip, apk, or apt for AWS CLI installation.

## Implementation Details

### Step-by-Step Plan (for a future implementer)

#### Step 1: Add `keycloak_db_user_arn` local

**File:** `terraform/aws-ecs/locals.tf` (or inline in `keycloak-ecs.tf`)

```hcl
locals {
  keycloak_db_user_arn = "arn:aws:rds-db:${var.aws_region}:${data.aws_caller_identity.current.account_id}:dbuser:${aws_rds_cluster.keycloak.cluster_identifier}/${var.keycloak_database_username}"
}
```

#### Step 2: Add Terraform variable for feature flag

**File:** `terraform/aws-ecs/variables.tf`

Add after the existing `keycloak_database_max_acu` variable (around line 113):

```hcl
variable "keycloak_db_iam_auth_enabled" {
  description = "Enable RDS IAM database authentication for Keycloak."
  type        = bool
  default     = false
}

# Update keycloak_database_password to have null default (deprecated)
variable "keycloak_database_password" {
  description = "[DEPRECATED] Keycloak database password. Required when keycloak_db_iam_auth_enabled is false."
  type        = string
  sensitive   = true
  default     = null
}
```

#### Step 3: Enable IAM authentication at the MySQL user level

**File:** `terraform/aws-ecs/keycloak-database.tf`

IAM auth for MySQL is NOT enabled via a Terraform attribute on `aws_rds_cluster`. It is enabled by creating/updating the MySQL user with `AWSAuthenticationPlugin`.

Create a `null_resource` with `local-exec` to run the SQL command:

```hcl
resource "null_resource" "keycloak_mysql_iam_user" {
  triggers = {
    # Re-run if the username or cluster endpoint changes
    user      = var.keycloak_database_username
    endpoint  = aws_rds_cluster.keycloak.endpoint
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -e
      # Wait for the cluster to be available
      sleep 30
      # Create or update the Keycloak MySQL user with IAM authentication
      mysql -h ${aws_rds_cluster.keycloak.endpoint} \
        -u ${var.keycloak_database_username} \
        -p"${var.keycloak_database_password}" \
        -e "CREATE USER IF NOT EXISTS '${var.keycloak_database_username}'@'%' IDENTIFIED WITH AWSAuthenticationPlugin as IAM IAMAuth ENABLE; FLUSH PRIVILEGES;"
    EOT
  }

  depends_on = [aws_rds_cluster.keycloak]
}
```

**Important:** Do NOT set `enable_http_authentication` on the RDS cluster for IAM auth. That flag controls the HTTP interface (a serverless v2 feature), not IAM authentication. The `master_password` field on `aws_rds_cluster` must remain set (AWS does not allow removing it from existing clusters).

Also remove the checkov skip:
```hcl
# Remove this line:
# #checkov:skip=CKV_AWS_162:IAM database authentication not used - Keycloak uses password auth
```

#### Step 4: Update the RDS Proxy to IAM auth

**File:** `terraform/aws-ecs/keycloak-database.tf`

Update the `aws_db_proxy.keycloak` auth block:

```hcl
resource "aws_db_proxy" "keycloak" {
  name          = "keycloak-proxy"
  engine_family = "MYSQL"

  auth {
    auth_scheme = "DEFAULT"
    iam_auth    = "ENABLED"
    # Removed: secret_arn, client_password_auth_type
    # DEFAULT scheme with IAM auth = ENABLED works for MySQL
  }

  role_arn      = aws_iam_role.rds_proxy_role.arn
  vpc_subnet_ids = module.vpc.private_subnets
  vpc_security_group_ids = [aws_security_group.keycloak_db.id]

  require_tls = true  # Changed from false for defense-in-depth

  tags = local.common_tags

  depends_on = [
    aws_secretsmanager_secret_version.keycloak_db_secret
  ]
}
```

**Warning:** Changing `auth_scheme` from `SECRETS` to `DEFAULT` forces the RDS Proxy to be destroyed and recreated. This is an immutable field change in AWS. The Keycloak service will lose database connectivity briefly (5-10 minutes). Plan this during a maintenance window.

The RDS Proxy IAM policy can be simplified or removed since `DEFAULT` scheme no longer requires `secretsmanager:GetSecretValue` internally:

```hcl
resource "aws_iam_role_policy" "rds_proxy_policy" {
  name = "keycloak-rds-proxy-policy"
  role = aws_iam_role.rds_proxy_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = []  # No internal secret access needed with DEFAULT scheme
  })
}
```

#### Step 5: Attach rds-db:connect policy to ECS task role

**File:** `terraform/aws-ecs/keycloak-ecs.tf`

Add a new conditional policy:

```hcl
resource "aws_iam_role_policy" "keycloak_task_rds_db_policy" {
  count  = var.keycloak_db_iam_auth_enabled ? 1 : 0
  name   = "keycloak-task-rds-db-policy"
  role   = aws_iam_role.keycloak_task_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "rds-db:connect"
        ]
        Resource = local.keycloak_db_user_arn
      },
    ]
  })
}
```

#### Step 6: Update ECS task container definition

**File:** `terraform/aws-ecs/keycloak-ecs.tf`

Add the feature flag to `keycloak_container_env`:

```hcl
{
  name  = "KEYCLOAK_DB_IAM_AUTH_ENABLED"
  value = var.keycloak_db_iam_auth_enabled ? "true" : "false"
}
```

Update `keycloak_container_secrets` to conditionally include `KC_DB_PASSWORD`:

```hcl
locals {
  keycloak_container_secrets = concat([
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
    },
  ], var.keycloak_db_iam_auth_enabled ? [] : [
    {
      name      = "KC_DB_PASSWORD"
      valueFrom = "${aws_secretsmanager_secret.keycloak_db_secret.arn}:password::"
    }
  ])
}
```

Update the ECS task `command` to include the IAM token generation wrapper (when IAM auth enabled):

```hcl
command = var.keycloak_db_iam_auth_enabled ? [
  "/bin/sh", "-c",
  "set -e; " +
  "if ! command -v aws >/dev/null 2>&1; then " +
  "  (pip3 install --quiet awscli 2>/dev/null) || " +
  "  (pip install --quiet awscli 2>/dev/null) || " +
  "  (apk add --no-cache awscli 2>/dev/null) || " +
  "  (apt-get update -qq && apt-get install -y -qq awscli 2>/dev/null) || " +
  "  true; " +
  "fi; " +
  "aws rds generate-db-auth-token " +
  "  --hostname ${aws_rds_cluster.keycloak.endpoint} " +
  "  --port 3306 " +
  "  --username ${var.keycloak_database_username} " +
  "  --region ${var.aws_region} | " +
  "while read -r token; do export KC_DB_PASSWORD=\"$token\"; done; " +
  "exec /opt/keycloak/bin/kc.sh start"
] : ["start"]
```

#### Step 7: Update SSM Parameter for JDBC URL

**File:** `terraform/aws-ecs/keycloak-database.tf`

Add SSL parameters to the JDBC URL:

```hcl
resource "aws_ssm_parameter" "keycloak_database_url" {
  name   = "/keycloak/database/url"
  type   = "SecureString"
  key_id = aws_kms_key.rds.id
  value  = "jdbc:mysql://${aws_rds_cluster.keycloak.endpoint}:3306/keycloak?ssl=true&sslmode=require&enabledTLSProtocols=TLSv1.2"
  tags   = local.common_tags
}
```

#### Step 8: Remove Secrets Manager secret and rotation Lambda (IAM auth mode)

**When `keycloak_db_iam_auth_enabled = true`:**

**File:** `terraform/aws-ecs/keycloak-database.tf`
- Remove `aws_secretsmanager_secret.keycloak_db_secret`
- Remove `aws_secretsmanager_secret_version.keycloak_db_secret`

**File:** `terraform/aws-ecs/secret-rotation.tf`
- Remove the `rotate-rds` Lambda function and its associated resources
- Remove references to `keycloak/database` from the rotation Lambda policy

**File:** `terraform/aws-ecs/secret-rotation-config.tf`
- Remove rotation config for `keycloak_db_secret`

**File:** `terraform/aws-ecs/keycloak-ecs.tf`
- Remove `secretsmanager:GetSecretValue` for `keycloak_db_secret` from the task exec role SSM policy
- Update the `depends_on` in the RDS proxy to not reference the removed secret

**When `keycloak_db_iam_auth_enabled = false`:**
- Keep all existing Secrets Manager resources and rotation Lambda.

**File:** `terraform/aws-ecs/variables.tf`
- Update `keycloak_database_password` description to indicate deprecation.

#### Step 9: Update `.env.example`

Add the new feature flag:

```bash
# Keycloak Database Authentication
# true = RDS IAM authentication (generates short-lived tokens at startup)
# false = Static password from Secrets Manager (default)
KEYCLOAK_DB_IAM_AUTH_ENABLED=false
```

### Error Handling

- If the entrypoint wrapper fails to install the AWS CLI, the `true` fallback means the script continues to `kc.sh start` without `KC_DB_PASSWORD` set, which will cause Keycloak to fail to connect and the container will be restarted by ECS.
- If the `aws rds generate-db-auth-token` call fails (e.g., IAM policy misconfigured), the container exits with an error and ECS restarts it.
- If the RDS Proxy is being recreated (auth_scheme change), Keycloak will fail to connect until the proxy is available again. This is a planned brief outage.

### Logging

- Log token generation at INFO level: `logger.info(f"IAM auth token generated for Keycloak DB connection")`.
- Log token generation failure at ERROR level with context.
- Do NOT log the token value (it is a secret).
- Log the RDS endpoint and username used for token generation (without the token itself).

## Observability

### Tracing / Metrics / Logging Points

- **Token generation**: Log at INFO when the wrapper successfully generates an IAM auth token. Log at ERROR if token generation fails.
- **RDS connections**: Enable Aurora MySQL audit logging to track IAM auth connections vs password auth connections.
- **CloudWatch Logs**: ECS task logs capture the entrypoint output (AWS CLI install + token generation).
- **Recommended CloudWatch Alarms** (not in scope for this change but recommended):
  - `DBUserNotAuthorized` metric on the Keycloak cluster -- alerts if IAM auth is misconfigured.
  - `ProxySpillover` on the RDS Proxy -- alerts if connection pooling is overwhelmed.

## Scaling Considerations

### Current Load Assumptions

- Single Keycloak instance on Fargate (1 vCPU, 2 GB). Auto-scaling up to 4 instances.
- RDS Proxy handles connection pooling for multiple ECS tasks.

### Horizontal Scaling

- Each ECS task generates its own IAM auth token independently at startup. No coordination required between tasks.
- The `rds-db:connect` IAM permission is scoped per task role, so scaling out does not require any additional IAM changes.

### Bottlenecks

- IAM auth token generation is a fast AWS API call (~100ms). No caching is needed.
- RDS Proxy with IAM auth handles connection pooling the same way as with password auth.
- AWS CLI installation at first container start adds ~10 seconds to boot time. Subsequent starts are fast since the CLI remains cached in the Fargate task.

### Caching Strategy

- No token caching. Each container generates a fresh token at startup. Tokens expire in 15 minutes. ECS Fargate tasks are replaced on health check failures or rolling updates, which naturally triggers token regeneration.

## File Changes

### New Files

| File Path | Description |
|-----------|-------------|
| (none) | No new files required. The entrypoint wrapper is inline in the ECS task definition. |

### Modified Files

| File Path | Lines | Change Description |
|-----------|-------|--------------------|
| `terraform/aws-ecs/keycloak-database.tf` | ~60 | Remove Secrets Manager secret, update RDS Proxy auth block, update SSM URL with SSL params, remove checkov skip, add null_resource for MySQL IAM user |
| `terraform/aws-ecs/keycloak-ecs.tf` | ~60 | Add IAM auth policy, add feature flag env, update container command, update secrets block, remove KC_DB_PASSWORD from exec role policy |
| `terraform/aws-ecs/variables.tf` | ~10 | Add `keycloak_db_iam_auth_enabled`, deprecate `keycloak_database_password` |
| `terraform/aws-ecs/locals.tf` | ~5 | Add `keycloak_db_user_arn` local |
| `terraform/aws-ecs/secret-rotation.tf` | ~20 | Remove rotate-rds Lambda and associated resources |
| `terraform/aws-ecs/secret-rotation-config.tf` | ~10 | Remove rotation config for Keycloak DB secret |
| `.env.example` | ~5 | Add `KEYCLOAK_DB_IAM_AUTH_ENABLED` entry |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| New code | ~40 |
| New tests | ~0 (Terraform plan-time validation) |
| Modified code | ~80 |
| Deleted code | ~50 |
| **Total** | **~170** |

## Testing Strategy

See `testing.md` for the full test plan covering functional tests, backwards compatibility, deployment surface tests, and E2E tests.

## Alternatives Considered

### Alternative 1: Inline Shell Command (Chosen)
**Description:** Install the AWS CLI at runtime inside the ECS task via an inline shell command in the ECS task definition's `command` field.
**Pros:** No image change, simplest implementation, no new containers or build pipeline, the wrapper is entirely managed in Terraform.
**Cons:** ~10-second delay on first container start for AWS CLI installation. The `pip install` or `apk add` approach may vary depending on the base image's package manager.
**Why Chosen:** Best balance of simplicity and no operational impact. The Keycloak image must stay at `quay.io/keycloak/keycloak:25.0` (per task requirements), so a custom image is out. The wrapper is inline in the task definition, meaning no new files or build steps.

### Alternative 2: Sidecar / Init Container for Token Generation
**Description:** Run a small sidecar container (e.g., `public.ecr.aws/aws-cli/aws-cli:latest`) that generates and refreshes the IAM auth token, writes it to an `emptyDir` volume, and signals Keycloak to pick it up.
**Pros:** Decouples token generation from Keycloak startup. Can refresh token periodically without restarting Keycloak. The sidecar is dedicated to this task.
**Cons:** Adds complexity: extra container in the task definition, shared volume, coordination logic, and two containers to monitor. Requires changes to the ECS task definition structure.
**Why Rejected:** Overly complex for the gain. Token generation at startup is sufficient given Fargate's natural restart patterns. The 15-minute token window is acceptable for this workload.

### Alternative 3: Custom Keycloak Image with AWS CLI Pre-installed
**Description:** Build a custom Docker image (Dockerfile based on `quay.io/keycloak/keycloak:25.0`) with the AWS CLI pre-installed, push to ECR, and update the Terraform `keycloak_image_uri` variable.
**Pros:** No ~10-second delay on first start. Token generation is immediate. Cleaner separation of concerns.
**Cons:** Requires a new Dockerfile, build pipeline (CI/CD), and ECR repository management. Changes the deployment surface (new image to deploy and monitor). The task explicitly says "No Keycloak version change" and minimizing changes is a priority.
**Why Rejected:** Adds unnecessary build pipeline and deployment surface. The 10-second delay on first boot is acceptable for an infrastructure change.

### Comparison Matrix

| Criteria | Chosen (Inline Shell) | Alt 2 (Sidecar) | Alt 3 (Custom Image) |
|----------|----------------------|-----------------|---------------------|
| Security | High (no stored credentials) | High (no stored credentials) | High (no stored credentials) |
| Complexity | Low | High | Medium |
| Breaking Change | No (feature flag) | No (feature flag) | No (feature flag) |
| Operational Overhead | Low | Medium | Medium |
| Image Change | None | None (new container) | Yes (custom image) |
| Why Chosen | Best balance of security and simplicity | Too complex for the gain | Adds unnecessary build pipeline |

## Rollout Plan

- **Phase 1:** Implement Terraform changes (RDS Proxy, IAM policy, secrets removal, feature flag).
- **Phase 2:** Create the MySQL IAM user via SQL (one-time `null_resource` or manual step before apply).
- **Phase 3:** Run `terraform plan` to verify expected deltas (no unexpected resource replacements).
- **Phase 4:** Run `terraform apply` during a maintenance window (RDS Proxy re-creation causes brief outage).
- **Phase 5:** Deploy the new ECS task definition with `KEYCLOAK_DB_IAM_AUTH_ENABLED=false` (verify password auth fallback still works).
- **Phase 6:** Flip `KEYCLOAK_DB_IAM_AUTH_ENABLED=true` and force-new-deploy Keycloak.
- **Phase 7:** Verify Keycloak connects to the database (check CloudWatch Logs for startup errors).
- **Phase 8:** Verify Keycloak serves OIDC requests (test admin login, M2M token exchange).
- **Phase 9:** Confirm no Keycloak connections use password auth (check Aurora MySQL audit logs).
- **Phase 10:** Monitor CloudWatch Logs for connection errors for 24 hours.

## Open Questions

1. **Can `master_password` be removed from an existing RDS cluster?** The AWS API does not allow removing `master_password` from an existing cluster. The Terraform field must remain set until the cluster is replaced or migrated.

2. **What happens if the IAM token expires during a long-running Keycloak session?** The token is used only at JDBC connection time. If the token expires, the existing connection drops and Keycloak must restart to get a fresh token. For this design, we accept the 15-minute window: Fargate tasks are naturally replaced on health check failures, and a rolling update forces a restart. A background refresh mechanism could be added in a follow-up.

3. **Does the RDS Proxy with `auth_scheme = "DEFAULT"` and `iam_auth = "ENABLED"` work with Aurora MySQL Serverless v2?** The RDS Proxy documentation for MySQL IAM auth supports Aurora MySQL 3.0+. The current cluster runs `8.0.mysql_aurora.3.10.3` which is Aurora MySQL 3.x, so it should work. Verify in a test environment first. Note: `auth_scheme = "AWS_IAM"` is NOT valid for MySQL -- only `DEFAULT` with `iam_auth = "ENABLED"`.

4. **What package manager does the Keycloak 25.0 base image use?** The image may use Alpine (apk), Debian (apt), or a minimal environment with only pip. The wrapper tries pip3, pip, apk, apt in order of preference to maximize the chance of a successful install.

## References

- [Aurora MySQL IAM Database Authentication](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/UsingWithRDS.IAMDBAuth.html)
- [RDS Proxy IAM Authentication](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/rds-proxy.iam.html)
- [GenerateDBAuthToken API](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/UsingWithRDS.IAMDBAuth.AutomatingWithIAM.html)
- [Keycloak JDBC Configuration](https://www.keycloak.org/server/database)
- [MySQL AWSAuthenticationPlugin](https://dev.mysql.com/doc/refman/8.0/en/aws-aurora-iam-authentication.html)