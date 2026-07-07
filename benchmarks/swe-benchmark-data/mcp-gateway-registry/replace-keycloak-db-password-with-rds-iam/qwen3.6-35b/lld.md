# Low-Level Design: Replace Keycloak DB Password with RDS IAM Authentication

*Created: 2026-07-06*
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
The Keycloak Aurora MySQL database uses password-based authentication. The password is stored in Terraform variables, passed through Secrets Manager to the ECS task as an environment variable, and rotated by a Lambda every 30 days. This creates several security risks:
- Static credentials appear in Terraform state and can leak through environment variables.
- The password rotation Lambda adds operational complexity without eliminating the root risk (the password exists somewhere).
- IAM database authentication provides per-connection short-lived tokens with automatic rotation.

### Goals
- Replace password authentication with RDS IAM Database Authentication for the Keycloak Aurora MySQL cluster.
- Eliminate the `keycloak_database_password` Terraform variable.
- Remove or simplify the Secrets Manager secret and rotation Lambda for Keycloak DB credentials.
- Update the ECS task definition to generate IAM auth tokens at runtime.
- Update the RDS Proxy configuration or remove it if no longer needed.

### Non-Goals
- Migrating other databases (DocumentDB, registry MongoDB) to IAM auth.
- Changing the Keycloak Docker image or its custom providers.
- Updating the docker-compose local development setup (uses PostgreSQL, not MySQL).
- Performance benchmarking of IAM auth vs password auth.

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `terraform/aws-ecs/keycloak-database.tf` | Aurora MySQL cluster, RDS Proxy, Secrets Manager, KMS key | Primary file to modify: enable IAM auth, update RDS Proxy auth, remove password from cluster, create MySQL IAM user |
| `terraform/aws-ecs/keycloak-ecs.tf` | ECS task definition, task/execution roles, container secrets | Modify container secrets to remove `KC_DB_PASSWORD`, update `KC_DB_URL` with IAM auth parameters, add `rds-db:Connect` to task role |
| `terraform/aws-ecs/variables.tf` | Variable definitions | Remove `keycloak_database_password`, possibly remove `keycloak_database_username` if hardcoded |
| `terraform/aws-ecs/secret-rotation.tf` | Rotation Lambda function and IAM policy | Remove or gate the `rotate-rds` Lambda and its CloudWatch log group |
| `terraform/aws-ecs/secret-rotation-config.tf` | Secret rotation configuration | Remove `aws_secretsmanager_secret_rotation.keycloak_db_secret` |
| `terraform/aws-ecs/lambda/rotate-rds/index.py` | Rotation Lambda Python code | Delete entirely or gate it out |
| `docker-compose.yml` | Local development setup | No changes needed (uses PostgreSQL, not MySQL) |
| `terraform/aws-ecs/locals.tf` | Local variables | Check for any references to keycloak database password |

### Existing Patterns Identified
1. **Terraform variable conventions**: All sensitive variables use `type = string`, `sensitive = true`. Non-sensitive variables have sensible defaults.
2. **Secrets Manager pattern**: The `keycloak_db_secret` stores both username and password as JSON keys. ECS task reads them via `valueFrom = "${secret-arn}:key::"` syntax.
3. **RDS Proxy pattern**: Uses `auth_scheme = "SECRETS"` with `client_password_auth_type = "MYSQL_CACHING_SHA2_PASSWORD"`. The proxy fetches credentials from Secrets Manager.
4. **Rotation Lambda pattern**: Follows the AWS 4-step rotation process (createSecret, setSecret, testSecret, finishSecret). Deployed as a Python 3.13 Lambda inside the VPC.
5. **Security group pattern**: `keycloak_db` security group allows inbound 3306 from `keycloak_ecs` and `rotation_lambda` security groups.
6. **Checkov skip pattern**: CKV_AWS_162 is currently skipped with comment "IAM database authentication not used - Keycloak uses password auth".

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| Aurora MySQL cluster | Modifies | Enable `enable_http_authentication = true` for IAM auth |
| RDS Proxy | Modifies | Either enable IAM auth on proxy or remove proxy entirely |
| ECS task definition | Modifies | Remove password secret, add token generation, update JDBC URL |
| ECS task role | Modifies | Add `rds-db:Connect` IAM policy |
| Secrets Manager | Removes | Delete `keycloak_db_secret` and its rotation config |
| Rotation Lambda | Removes | Delete Lambda function and its resources |
| SSM parameters | May need update | `keycloak_database_url` SSM param is still used for `KC_DB_URL` |
| docker-compose.yml | No change | Uses PostgreSQL locally; IAM auth is an AWS-only concern |

### Constraints and Limitations Discovered
1. **Aurora MySQL IAM auth requires `enable_http_authentication = true`**: This is the Terraform flag for enabling IAM authentication on Aurora MySQL serverless v2 clusters (not `iam_database_authentication_enabled` which is for provisioned clusters).
2. **MySQL user must be created with AWSAuthenticationPlugin**: This requires a one-time SQL command to create the user with `AWSAuthenticationPlugin` and `IAMAuth` enabled. Terraform cannot do this directly -- it needs a `null_resource` with remote-exec, a Lambda, or manual execution.
3. **RDS Proxy with IAM auth requires `auth_scheme = "AWS_IAM"`**: If we keep the proxy, we must change from `SECRETS` to `AWS_IAM` auth scheme and update the auth block accordingly.
4. **ECS task needs boto3 or AWS CLI for token generation**: The container must generate the auth token at runtime. Options include a sidecar container, an entrypoint wrapper script, or a custom Keycloak entrypoint.
5. **Token TTL is 15 minutes**: If Keycloak restarts within 15 minutes of container start, it needs a fresh token. The entrypoint script should always generate a fresh token before starting Keycloak.
6. **Local development uses PostgreSQL**: The docker-compose setup uses PostgreSQL with password auth. This change only affects the Terraform-managed production deployment.

## Architecture

### System Context Diagram

```
                    +---------------------+
                    |   AWS RDS Aurora    |
                    |   MySQL Serverless  |
                    |                     |
                    |  +---------------+  |
                    |  | IAM Auth      |  |
                    |  | (rds-db:Connect) |
                    |  +---------------+  |
                    |         |           |
                    |  +---------------+  |
                    |  | Aurora MySQL  |  |
                    |  | Engine        |  |
                    |  +---------------+  |
                    +---------------------+
                              ^
                              | JDBC with IAM token
                              |
                    +---------------------+
                    |  RDS Proxy          |
                    |  (optional)         |
                    |  auth=AWS_IAM       |
                    +---------------------+
                              ^
                              |
                    +---------------------+
                    |  ECS Task (Fargate) |
                    |                     |
                    |  +---------------+  |
                    |  | Entrypoint    |  |
                    |  | (boto3/token) |  |
                    |  +---------------+  |
                    |         |           |
                    |  +---------------+  |
                    |  | Keycloak      |  |
                    |  | (KC_DB_URL)   |  |
                    |  +---------------+  |
                    +---------------------+
                              |
                              | ECS Task Role
                              | rds-db:Connect
```

### Sequence Diagram

```
ECS Task Start
     |
     v
Entrypoint Script (boto3)
     |  generate_db_auth_token(DBClusterIdentifier=keycloak, Username=keycloak)
     v
AWS RDS GenerateDBAuthToken API (scoped via IAM)
     |  returns 15-min token
     v
Keycloak starts with:
  KC_DB_URL=jdbc:mysql://proxy-or-cluster-url:3306/keycloak?ssl=true&...
  KC_DB_USERNAME=keycloak
  KC_DB_PASSWORD=<token-from-env>
     |
     v
RDS Proxy / Aurora MySQL validates token via IAM
     |  token verified, connection established
     v
Keycloak running with IAM auth
```

### Component Diagram

```
  +--------------------------------------------------+
  |  terraform/aws-ecs/                              |
  |                                                  |
  |  +------------------------+                      |
  |  | keycloak-database.tf   |                      |
  |  | - RDS Cluster (IAM on) |                      |
  |  | - MySQL IAM User       |                      |
  |  | - RDS Proxy (IAM)      |                      |
  |  - Secrets (removed)      |                      |
  |  - KMS Key (keep)         |                      |
  +---------------------------+                      |
  |  +------------------------+                      |
  |  | keycloak-ecs.tf        |                      |
  |  | - ECS Task Role policy |                      |
  |  | - Container secrets    |                      |
  |  | - Container env        |                      |
  +---------------------------+                      |
  |  +------------------------+                      |
  |  | secret-rotation.tf     |                      |
  |  | - Lambda removed       |                      |
  |  +------------------------+                      |
  |  +------------------------+                      |
  |  | variables.tf           |                      |
  |  | - password removed     |                      |
  +--------------------------------------------------+
```

## Data Models

### Terraform Variable Changes

**Remove:**
```hcl
variable "keycloak_database_password" {
  description = "Keycloak database password"
  type        = string
  sensitive   = true
}
```

**Keep (unchanged):**
```hcl
variable "keycloak_database_username" {
  description = "Keycloak database username"
  type        = string
  sensitive   = true
  default     = "keycloak"
}
```

**Keep (unchanged):**
```hcl
variable "keycloak_database_min_acu" { ... }
variable "keycloak_database_max_acu" { ... }
```

### New Terraform Resources

**RDS DB User ARN for IAM Policy:**
The user ARN is constructed as:
```
arn:aws:rds-db:{region}:{account}:dbuser:{cluster-identifier}/{username}
```

This is computed in `locals.tf`:
```hcl
locals {
  keycloak_db_user_arn = "arn:aws:rds-db:${var.aws_region}:${data.aws_caller_identity.current.account_id}:dbuser:${aws_rds_cluster.keycloak.cluster_identifier}/${var.keycloak_database_username}"
}
```

**MySQL User with IAM Authentication:**
Terraform cannot directly create MySQL users on Aurora. Recommended approaches:
- Option A: Document the SQL commands for manual execution before `terraform apply`.
- Option B: Use a Lambda (deployed as part of the Terraform stack) that creates the user.
- Option C: Use `null_resource` with a remote-exec provisioner if the cluster has SSH access.

The SQL command is:
```sql
CREATE USER keycloak
  IDENTIFIED WITH AWSAuthenticationPlugin as IAM
  INITIAL PASSWORD 'temporary'
  IAMAuth ENABLE;
GRANT ALL PRIVILEGES ON keycloak.* TO 'keycloak'@'%';
FLUSH PRIVILEGES;
```

## API / CLI Design

No new CLI commands or API endpoints are added. The change is entirely infrastructure-level.

### ECS Task Environment Variable Changes

**Before:**
```python
{ name = "KC_DB_URL", valueFrom = ssm_parameter.arn }
{ name = "KC_DB_USERNAME", valueFrom = secretsmanager_secret.arn:username }
{ name = "KC_DB_PASSWORD", valueFrom = secretsmanager_secret.arn:password }
```

**After:**
```python
{ name = "KC_DB_URL", valueFrom = ssm_parameter.arn }
{ name = "KC_DB_USERNAME", value = var.keycloak_database_username }
# KC_DB_PASSWORD is generated at runtime by the entrypoint script
# and passed via a separate mechanism (see Implementation Details)
```

## Configuration Parameters

### New/Changed Environment Variables

| Variable | Before | After | Description |
|----------|--------|-------|-------------|
| `KC_DB_USERNAME` | From Secrets Manager | From variable | Username for IAM auth (same user that was the password-auth user) |
| `KC_DB_PASSWORD` | From Secrets Manager | Generated by entrypoint | IAM auth token (15-min TTL), not a static password |
| `KC_DB_URL` | `jdbc:mysql://endpoint:3306/keycloak` | `jdbc:mysql://endpoint:3306/keycloak?ssl=true&sslmode=require&enabledTLSProtocols=TLSv1.2` | JDBC URL with SSL required for IAM auth |

### Deployment Surface Checklist

- [ ] `terraform/aws-ecs/keycloak-database.tf` -- cluster, proxy, MySQL user, secrets removal
- [ ] `terraform/aws-ecs/keycloak-ecs.tf` -- ECS task definition, task role policy, container env/secrets
- [ ] `terraform/aws-ecs/variables.tf` -- remove `keycloak_database_password` variable
- [ ] `terraform/aws-ecs/secret-rotation.tf` -- remove rotation Lambda and RDS rotation resources
- [ ] `terraform/aws-ecs/secret-rotation-config.tf` -- remove rotation config for Keycloak DB secret
- [ ] `terraform/aws-ecs/lambda/rotate-rds/` -- delete entire directory
- [ ] `terraform/aws-ecs/locals.tf` -- check for any references to `keycloak_database_password`
- [ ] `.env.example` -- check for any references to `KEYCLOAK_DB_PASSWORD`
- [ ] `docker-compose.yml` -- no changes (PostgreSQL for local dev)
- [ ] `build_and_run.sh` -- check for any references to Keycloak DB password

## New Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `boto3` | (already in ECS image) | Generate RDS auth tokens in the entrypoint script |

This change uses only existing dependencies. The ECS Fargate container image already includes the AWS CLI (and optionally boto3 via Python) which is needed to generate authentication tokens. No new Terraform providers or Python packages are required.

## Implementation Details

### Step-by-Step Plan (for a future implementer)

#### Step 1: Enable IAM Authentication on the RDS Cluster

**File:** `terraform/aws-ecs/keycloak-database.tf`

Modify the `aws_rds_cluster.keycloak` resource:

```hcl
resource "aws_rds_cluster" "keycloak" {
  # ... existing fields ...

  # Enable IAM database authentication for Aurora MySQL serverless v2
  enable_http_authentication = true

  # NOTE: master_password must remain on existing clusters -- AWS does not
  # allow removing it. It is no longer used for authentication.
}
```

#### Step 2: Create the MySQL User with IAM Authentication

**File:** `terraform/aws-ecs/keycloak-database.tf` (new resource) or documented SQL commands

Since Terraform cannot directly create MySQL users on Aurora, use one of these approaches:

**Option A (Recommended): Document the SQL commands for manual execution**

Connect to the Aurora cluster using the existing master credentials and run:
```sql
CREATE USER keycloak
  IDENTIFIED WITH AWSAuthenticationPlugin as IAM
  INITIAL PASSWORD 'temporary'
  IAMAuth ENABLE;
GRANT ALL PRIVILEGES ON keycloak.* TO 'keycloak'@'%';
FLUSH PRIVILEGES;
```

After the user is created, remove the `INITIAL PASSWORD` line on subsequent runs.

#### Step 3: Update the RDS Proxy

**File:** `terraform/aws-ecs/keycloak-database.tf`

Update the RDS Proxy to use IAM authentication:

```hcl
resource "aws_db_proxy" "keycloak" {
  name          = "keycloak-proxy"
  engine_family = "MYSQL"

  auth {
    auth_scheme = "AWS_IAM"
    iam_auth    = "ENABLED"
  }

  # Remove:
  # - secret_arn reference
  # - client_password_auth_type
  # - depends_on for secret version

  role_arn               = aws_iam_role.rds_proxy_role.arn
  vpc_subnet_ids         = module.vpc.private_subnets
  vpc_security_group_ids = [aws_security_group.keycloak_db.id]
  require_tls            = false

  tags = local.common_tags

  # Remove depends_on block
}
```

Update the RDS Proxy IAM policy to not need Secrets Manager access:

```hcl
resource "aws_iam_role_policy" "rds_proxy_policy" {
  name = "keycloak-rds-proxy-policy"
  role = aws_iam_role.rds_proxy_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = []
    # Remove secretsmanager:GetSecretValue - no longer needed with IAM auth
  })
}
```

#### Step 4: Add rds-db:Connect Policy to ECS Task Role

**File:** `terraform/aws-ecs/keycloak-ecs.tf`

Add a new policy to the task role:

```hcl
resource "aws_iam_role_policy" "keycloak_task_rds_db_policy" {
  name = "keycloak-task-rds-db-policy"
  role = aws_iam_role.keycloak_task_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "rds-db:Connect"
        ]
        Resource = local.keycloak_db_user_arn
      },
    ]
  })
}
```

Add to locals block:
```hcl
locals {
  keycloak_db_user_arn = "arn:aws:rds-db:${var.aws_region}:${data.aws_caller_identity.current.account_id}:dbuser:${aws_rds_cluster.keycloak.cluster_identifier}/${var.keycloak_database_username}"
}
```

#### Step 5: Update ECS Task Container Secrets and Environment

**File:** `terraform/aws-ecs/keycloak-ecs.tf`

Modify `keycloak_container_secrets`:

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
    # KC_DB_USERNAME: remove -- switched to env var or keep in secrets
    # KC_DB_PASSWORD: REMOVE -- replaced by runtime token
  ]
}
```

Remove the `KC_DB_PASSWORD` secret entry entirely.

#### Step 6: Create an Entrypoint Script for Token Generation

**File:** New file or inline in ECS task definition

The ECS task's `command` field should generate a fresh IAM auth token before starting Keycloak.

Option 1 (inline, AWS CLI):
```json
{
  "command": [
    "/bin/sh", "-c",
    "export KC_DB_PASSWORD=$(/usr/bin/aws rds generate-db-auth-token "
    "--hostname ${KEYCLOAK_DB_HOST:-proxy-endpoint} "
    "--port 3306 "
    "--username keycloak "
    "--region ${AWS_REGION}) && "
    "kc.sh start"
  ]
}
```

Option 2 (boto3 entrypoint script, packaged in custom image):
```python
#!/usr/bin/env python3
import boto3
import os

client = boto3.client("rds", region_name=os.environ["AWS_REGION"])
token = client.generate_db_auth_token(
    DBClusterIdentifier="keycloak",
    Username=os.environ["KEYCLOAK_DB_USERNAME"],
    Port=3306
)
os.environ["KC_DB_PASSWORD"] = token
exec(["kc.sh", "start"])
```

**Critical detail:** The `KC_DB_URL` SSM parameter value must include the SSL parameters:
```
jdbc:mysql://keycloak-proxy.x.us-east-1.rds.amazonaws.com:3306/keycloak?ssl=true&sslmode=require&enabledTLSProtocols=TLSv1.2
```

Update `aws_ssm_parameter.keycloak_database_url` to include these parameters.

#### Step 7: Update the SSM Parameter for KC_DB_URL

**File:** `terraform/aws-ecs/keycloak-database.tf`

```hcl
resource "aws_ssm_parameter" "keycloak_database_url" {
  name   = "/keycloak/database/url"
  type   = "SecureString"
  key_id = aws_kms_key.rds.id
  value  = "jdbc:mysql://${aws_rds_cluster.keycloak.endpoint}:3306/keycloak?ssl=true&sslmode=require&enabledTLSProtocols=TLSv1.2"
  tags   = local.common_tags
}
```

#### Step 8: Remove Secrets Manager Secret and Rotation

**File:** `terraform/aws-ecs/keycloak-database.tf` -- Remove these resources:
- `aws_secretsmanager_secret.keycloak_db_secret`
- `aws_secretsmanager_secret_version.keycloak_db_secret`

**File:** `terraform/aws-ecs/secret-rotation-config.tf` -- Remove:
- `aws_secretsmanager_secret_rotation.keycloak_db_secret`

**File:** `terraform/aws-ecs/secret-rotation.tf` -- Remove:
- References to `aws_secretsmanager_secret.keycloak_db_secret.arn` in the rotation policy's Resource list
- The `rds_rotation` CloudWatch log group (only used by the rotate-rds Lambda)

**File:** `terraform/aws-ecs/variables.tf` -- Remove:
```hcl
variable "keycloak_database_password" { ... }
```

**File:** `terraform/aws-ecs/keycloak-ecs.tf` -- Update SSM policy:
Remove `secretsmanager:GetSecretValue` for `keycloak_db_secret` from the task execution role policy.

#### Step 9: Remove the Rotation Lambda Package

**File:** Delete `terraform/aws-ecs/lambda/rotate-rds/` directory entirely

The `rotate-rds` Lambda handles only the Keycloak DB secret. The `rotate-documentdb` Lambda is separate and should be kept.

#### Step 10: Update Checkov Skips

**File:** `terraform/aws-ecs/keycloak-database.tf`

Remove the checkov skip:
```hcl
#checkov:skip=CKV_AWS_162:IAM database authentication not used - Keycloak uses password auth
```

This skip is no longer needed because IAM auth is now enabled.

### Error Handling
- If the entrypoint script fails to generate an IAM auth token, the container should exit with a non-zero status so ECS restarts the task.
- If the RDS cluster does not have IAM auth enabled, the connection will fail with an authentication error. Terraform should fail early if the cluster configuration is inconsistent.

### Logging
- Log token generation success/failure at INFO level.
- Do NOT log the token value (it is a secret).
- Log the RDS endpoint and username used for token generation (without the token itself).

## Observability

### Tracing / Metrics / Logging Points
- **Token generation**: Log at INFO level when the entrypoint successfully generates an IAM auth token. Log at ERROR level if token generation fails.
- **RDS connections**: Enable Aurora MySQL audit logging to track IAM auth vs password auth connections.
- **CloudWatch Logs**: ECS task logs will capture the entrypoint output. No additional metrics are needed.

## Scaling Considerations

### Current Load Assumptions
- Single Keycloak instance on Fargate (1 vCPU, 2 GB). Auto-scaling up to 4 instances.
- RDS Proxy handles connection pooling for multiple ECS tasks.

### Horizontal Scaling
- Each ECS task generates its own IAM auth token independently. No coordination required.
- The `rds-db:Connect` IAM permission is scoped per task role, so scaling out does not require any changes.

### Bottlenecks
- IAM auth token generation is a fast AWS API call (~100ms). No caching is needed.
- RDS Proxy with IAM auth handles connection pooling the same way as with password auth.

### Caching Strategy
- No token caching. Each container generates a fresh token at startup. This is the correct behavior because tokens expire in 15 minutes and generating a new one is cheap.

## File Changes

### New Files

| File Path | Description |
|-----------|-------------|
| (none) | No new files required if using inline token generation in ECS task definition |

### Modified Files

| File Path | Lines | Change Description |
|-----------|-------|--------------------|
| `terraform/aws-ecs/keycloak-database.tf` | ~50 | Enable IAM auth on cluster, update RDS Proxy auth, create MySQL IAM user, remove secrets, remove checkov skip |
| `terraform/aws-ecs/keycloak-ecs.tf` | ~30 | Remove password secret, add rds-db policy, update container env for KC_DB_URL SSL params |
| `terraform/aws-ecs/variables.tf` | ~5 | Remove `keycloak_database_password` variable |
| `terraform/aws-ecs/secret-rotation.tf` | ~10 | Remove rotation Lambda and RDS rotation resources |
| `terraform/aws-ecs/secret-rotation-config.tf` | ~10 | Remove rotation config for Keycloak DB secret |
| `terraform/aws-ecs/locals.tf` | ~5 | Add `keycloak_db_user_arn` local |
| `terraform/aws-ecs/lambda/rotate-rds/` | (delete) | Delete entire directory |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| New code | ~0 |
| New tests | ~0 (Terraform plan-time validation) |
| Modified code | ~100 |
| Deleted code | ~50 |
| **Total** | **~150** |

## Testing Strategy
See `testing.md` for the full test plan.

## Alternatives Considered

### Alternative 1: Keep Password Auth, Improve Secret Management
**Description:** Instead of switching to IAM auth, keep the current password auth but improve how secrets are managed (e.g., use AWS Secrets Manager exclusively, reduce rotation Lambda complexity).

**Pros:** No changes to ECS task definition, no MySQL user migration, no entrypoint script changes.
**Cons:** Does not eliminate the root security concern (passwords still exist).

**Why Rejected:** Does not meet the security requirement of eliminating stored credentials.

### Alternative 2: Use RDS Proxy Only (No Direct IAM Auth)
**Description:** Update the RDS Proxy to use IAM auth (`auth_scheme = "AWS_IAM"`) but keep the ECS task connecting through the proxy as before. The proxy handles the IAM token validation.

**Pros:** ECS task does not need to generate tokens directly; the RDS Proxy handles IAM auth transparently.
**Cons:** Requires RDS Proxy IAM auth configuration which is more complex; the proxy still needs to know the user but validates via IAM.

**Why This Is Part of the Chosen Design:** This is the approach taken -- update the RDS Proxy to use IAM auth. The ECS task sends the IAM token through the proxy, and the proxy validates it.

### Alternative 3: Use a Sidecar Container for Token Refresh
**Description:** Run a small sidecar container that continuously refreshes the IAM auth token and writes it to a shared volume or environment variable.

**Pros:** Decouples token generation from Keycloak startup.
**Cons:** Adds complexity (extra container, shared volume, coordination).

**Why Rejected:** The entrypoint script approach is simpler and sufficient because the token only needs to be fresh at container start time. Keycloak will use the same token until the container is restarted (which is acceptable given the 15-minute TTL and typical Fargate restart patterns).

### Comparison Matrix

| Criteria | Chosen (Entrypoint + RDS Proxy IAM) | Alt 1 (Improve Secrets) | Alt 3 (Sidecar) |
|----------|-------------------------------------|-------------------------|-----------------|
| Security | High (no stored credentials) | Medium (passwords still stored) | High (no stored credentials) |
| Complexity | Medium | Low | High |
| Breaking Change | Yes (requires migration) | No | Yes (new container) |
| Operational Overhead | Low | Low | Medium |
| Why Chosen | Best balance of security and simplicity | Rejected on security grounds | Overly complex for the gain |

## Rollout Plan

### Phase 1: Terraform Changes (Infrastructure)
1. Run `terraform plan` to verify the RDS cluster IAM auth enablement, RDS Proxy update, and policy changes.
2. Create the MySQL IAM user (manual SQL or Lambda) before applying cluster changes.
3. Apply the Terraform changes.
4. Verify the RDS cluster is in `available` state with IAM auth enabled.

### Phase 2: ECS Task Redeployment
1. Update the ECS task definition with the new container env/secrets and entrypoint.
2. Force a new deployment of the Keycloak ECS service (`ecs update-service --force-new-deployment`).
3. Verify the health check passes (`curl -f http://localhost:9000/health/ready`).
4. Verify Keycloak can connect to the database using IAM auth.

### Phase 3: Cleanup
1. Verify no Keycloak connections use password auth anymore (check Aurora MySQL audit logs).
2. Confirm the rotation Lambda is no longer invoked.
3. Manually delete the Secrets Manager secret `keycloak/database` (Terraform will destroy it).
4. Remove the `rotate-rds` Lambda and its resources.

### Phase 4: Verification
1. Run the full test suite (see `testing.md`).
2. Monitor CloudWatch Logs for connection errors.
3. Verify auto-scaling works with the new auth method.

## Open Questions
1. **Can `master_password` be removed from an existing RDS cluster?** The AWS API may require the cluster to always have a master password even when IAM auth is enabled. If so, the password variable can be removed but the cluster field must remain (set to a dummy value or derived from the Secrets Manager secret temporarily during the transition).
2. **Does the RDS Proxy with `auth_scheme = "AWS_IAM"` work with Aurora MySQL serverless v2?** The RDS Proxy documentation for MySQL IAM auth may have specific requirements.
3. **What happens if the token expires during a long-running Keycloak session?** The token is used only at connection time (Keycloak connections are long-lived). If the token expires, the database connection drops and Keycloak must restart the container to get a new token. This means the container startPeriod should account for token generation time.

## References
- [Aurora MySQL IAM Database Authentication](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/UsingWithRDS.IAMDBAuth.html)
- [RDS Proxy IAM Authentication](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/rds-proxy.iam.html)
- [GenerateDBAuthToken API](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/UsingWithRDS.IAMDBAuth.AutomatingWithIAM.html)
- [Keycloak JDBC configuration](https://www.keycloak.org/server/database)