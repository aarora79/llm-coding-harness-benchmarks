# Low-Level Design: Replace Keycloak Database Password with RDS IAM Authentication

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
15. [Open Questions](#open-questions)
16. [References](#references)

## Overview

### Problem Statement

Keycloak on AWS ECS authenticates to its Aurora MySQL Serverless v2 cluster with a static username/password. The password originates as a plaintext Terraform variable (`keycloak_database_password`), lands in AWS Secrets Manager (`keycloak/database`), is rotated every 30 days by a Lambda, and is injected into the ECS task as the `KC_DB_PASSWORD` container secret. This is a standing credential that must be stored, rotated, and protected, and it has historically caused restart crash-loops when the stored copy drifts from Aurora (Issue #1026).

This design replaces the static password with RDS IAM database authentication, gated behind a feature flag (`keycloak_db_iam_auth_enabled`, default `false`). When enabled, the ECS task presents a short-lived (15-minute) IAM auth token generated from its task role via the AWS Advanced JDBC Wrapper's IAM plugin, and no password is stored or rotated. When disabled, the current password-auth path is preserved byte-for-byte.

### Goals

- Enable RDS IAM database authentication on the Keycloak Aurora MySQL cluster behind a Terraform feature flag.
- Grant the Keycloak ECS **task role** `rds-db:connect` scoped to the specific cluster resource id and DB user.
- Have the Keycloak container generate short-lived IAM auth tokens at connection time (via the AWS Advanced JDBC Wrapper) instead of reading `KC_DB_PASSWORD` from Secrets Manager.
- Keep password authentication as a fully supported, default fallback, selectable via the flag with no Keycloak version change.
- Document a repeatable bootstrap step that creates the IAM-enabled MySQL user.

### Non-Goals

- Local docker-compose development (PostgreSQL container, not Aurora) is unchanged.
- Helm / EKS surfaces (none exist for Keycloak here).
- Decommissioning the password-rotation stack (kept for the fallback path).
- Migrating any other service to IAM auth.
- Changing the Keycloak version (`quay.io/keycloak/keycloak:25.0`).

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `terraform/aws-ecs/keycloak-database.tf` | Aurora MySQL cluster, RDS Proxy, DB Secrets Manager secret, SSM DB URL param, KMS key | Cluster gets `iam_database_authentication_enabled`; secret/rotation become conditional or bypassed |
| `terraform/aws-ecs/keycloak-ecs.tf` | ECS cluster/service/task-def, task + exec IAM roles, container env + secrets | Task role gets `rds-db:connect`; task env/secrets become conditional on the flag |
| `terraform/aws-ecs/variables.tf` | Keycloak/DB variables (lines 65-149) | New `keycloak_db_iam_auth_enabled` variable |
| `terraform/aws-ecs/terraform.tfvars.example` | Example credential values (lines 87-96) | Document the new flag; note password becomes optional when flag is on |
| `docker/keycloak/Dockerfile` | Custom optimized Keycloak image (`KC_DB=mysql` baked) | Bundle AWS Advanced JDBC Wrapper JAR; set driver/JDBC properties for IAM |
| `terraform/aws-ecs/secret-rotation*.tf`, `lambda/rotate-rds/index.py` | 30-day RDS password rotation | Skipped when IAM auth is on; retained for fallback |
| `terraform/aws-ecs/keycloak-security-groups.tf` | SGs (DB on 3306) | No change; ECS-to-DB path already open |
| `terraform/aws-ecs/scripts/init-keycloak.sh`, `post-deployment-setup.sh` | Realm/client bootstrap + orchestration | Hook point for the one-time IAM DB-user bootstrap |
| `terraform/aws-ecs/README.md`, `OPERATIONS.md` | Deployment / operations docs | Document enabling IAM auth, bootstrap, rollback |

### Existing Patterns Identified

1. **Feature-flag pattern (Terraform boolean + conditional resources/locals)**: The stack already uses booleans like `enable_cloudfront`, `enable_route53_dns`, and `entra_enabled` to select behavior, driving `count`/conditional locals. A future implementer should model `keycloak_db_iam_auth_enabled` the same way: a `bool` variable defaulting to `false`, consumed by conditional expressions in `keycloak-database.tf` and `keycloak-ecs.tf`.
   - Files: `terraform/aws-ecs/variables.tf`, `terraform/aws-ecs/keycloak-ecs.tf` (`locals` at lines 5-106).

2. **Container config via `locals` lists**: `keycloak_container_env` (env, lines 15-75) and `keycloak_container_secrets` (secrets, lines 77-105) are Terraform `local` lists spliced into the task definition. Toggle IAM auth by conditionally composing these lists (drop `KC_DB_PASSWORD`, add IAM-related `KC_DB_URL_PROPERTIES` / driver env) rather than editing the task-definition block directly.

3. **Secrets sourced via `valueFrom`**: `KC_DB_USERNAME`/`KC_DB_PASSWORD` are read from the `keycloak/database` Secrets Manager secret using the `"<arn>:<key>::"` syntax (lines 98-104). The IAM path keeps `KC_DB_USERNAME` (a username is still required) but removes `KC_DB_PASSWORD`.

4. **IAM policy as inline `aws_iam_role_policy`**: Roles attach inline JSON policies (e.g. `keycloak_task_ssm_policy`, lines 255-274). Add the `rds-db:connect` grant as a new conditional inline policy on the **task role** (`aws_iam_role.keycloak_task_role`, lines 232-249), which today has only SSM Session Manager permissions.

5. **checkov skip comments with justification**: e.g. `#checkov:skip=CKV_AWS_162:...` at `keycloak-database.tf:43`. When IAM auth becomes available, this skip must be removed/rewritten.

6. **Non-optimized stock image vs custom build**: The task runs the stock public image with `command = ["start"]` (non-optimized). The IAM path needs the AWS Advanced JDBC Wrapper JAR on the classpath, which the stock image lacks, so enabling IAM auth requires deploying the custom-built image from `docker/keycloak/Dockerfile`.

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| `aws_rds_cluster.keycloak` | Modifies | Add conditional `iam_database_authentication_enabled` |
| `aws_iam_role.keycloak_task_role` | Extends | New conditional `rds-db:connect` inline policy |
| `keycloak_container_env` / `keycloak_container_secrets` locals | Modifies | Conditionally drop `KC_DB_PASSWORD`, add JDBC wrapper properties |
| `docker/keycloak/Dockerfile` | Extends | Bundle JDBC wrapper JAR, set `KC_DB_DRIVER` / URL properties |
| `keycloak-database.tf` SSM `keycloak_database_url` | Modifies | JDBC URL gains wrapper scheme + IAM plugin properties when flag on |
| `secret-rotation*.tf` | Conditional | Rotation resources gated off when IAM auth on |
| `post-deployment-setup.sh` / new bootstrap script | Extends | One-time creation of the IAM MySQL user |

### Constraints and Limitations Discovered

- **Keycloak has no native RDS IAM support.** Keycloak reads `KC_DB_PASSWORD` as a literal string; it cannot call `GenerateDBAuthToken`. The token generation must be delegated to the JDBC layer. The clean, supported way is the **AWS Advanced JDBC Wrapper** (`software.amazon.jdbc.Driver`) with the `iam` connection plugin, which mints and caches tokens per physical connection and refreshes them before the 15-minute expiry. This is why a custom image is required when the flag is on.
- **TLS is mandatory for IAM auth.** RDS rejects IAM-authenticated connections that are not TLS-encrypted. The container must trust the Amazon RDS CA bundle and the JDBC URL must enable SSL (`sslMode`/`useSSL`).
- **The task role, not the execution role, needs `rds-db:connect`.** The execution role pulls secrets/images at launch; the task role is the runtime identity the JDBC wrapper signs tokens with. Today the task role has only `ssmmessages:*`.
- **`rds-db:connect` resource ARN uses the cluster *resource id*, not the cluster name.** Format: `arn:aws:rds-db:<region>:<account>:dbuser:<cluster-resource-id>/<db-user>`. This is `aws_rds_cluster.keycloak.cluster_resource_id` in Terraform.
- **RDS Proxy interaction.** A proxy exists but the JDBC URL currently targets the cluster endpoint directly (`keycloak-database.tf:281`). IAM auth is simplest end-to-end against the cluster endpoint; the proxy stays with `iam_auth = "DISABLED"` and is not on the Keycloak connection path. Enabling IAM through the proxy is a deliberate non-goal here.
- **The IAM MySQL user cannot be created by Terraform.** It requires a SQL statement (`CREATE USER ... IDENTIFIED WITH AWSAuthenticationPlugin AS 'RDS'`) executed against the running database. This is a bootstrap step, not a Terraform resource.

## Architecture

### System Context Diagram

```
                         Password auth (flag = false, DEFAULT)
  +-----------------+     KC_DB_PASSWORD (from Secrets Manager)     +------------------+
  | Keycloak ECS    | --------------------------------------------> | Aurora MySQL     |
  | task (mysql     |     TCP 3306, password login                  | cluster endpoint |
  | JDBC driver)    |                                               +------------------+
  +-----------------+                                                        ^
        ^                                                                    |
        | secrets: KC_DB_USERNAME, KC_DB_PASSWORD           rotation Lambda updates password (30d)
        +--- Secrets Manager keycloak/database <---------------------+


                         IAM auth (flag = true)
  +---------------------+   1. task role signs token (rds-db:connect)   +------------------+
  | Keycloak ECS task   |   ------------------------------------------> | Aurora MySQL     |
  | AWS Adv. JDBC       |   2. token used as password, over TLS          | (IAM auth        |
  | Wrapper + iam plugin|   ------------------------------------------> |  enabled)        |
  +---------------------+                                               +------------------+
        ^   no KC_DB_PASSWORD; token minted in-process, cached < 15 min
        |
        +--- task role: rds-db:connect on dbuser:<cluster-resource-id>/keycloak_iam
```

### Sequence Diagram (IAM auth, flag = true)

```
ECS starts task
  |
  |-- execution role pulls image + KC_DB_URL, KC_DB_USERNAME (no KC_DB_PASSWORD)
  v
Keycloak boots -> AWS Advanced JDBC Wrapper opens first physical connection
  |
  |-- iam plugin: uses task-role credentials (from ECS metadata endpoint)
  |     to generate an RDS auth token for host:port/user  (GenerateDBAuthToken)
  |
  |-- opens TLS connection to Aurora, presents token as password
  v
Aurora validates token against IAM (rds-db:connect) -> connection established
  |
  |-- token cached; refreshed automatically before ~15 min expiry on new connections
```

### Component Diagram

```
terraform/aws-ecs/
  variables.tf ................ keycloak_db_iam_auth_enabled (bool, default false)
        |
        v
  keycloak-database.tf ........ aws_rds_cluster.keycloak
        |                         iam_database_authentication_enabled = var.<flag>
        |                       aws_ssm_parameter.keycloak_database_url
        |                         value = IAM-aware JDBC URL when flag on
        v
  keycloak-ecs.tf ............. locals.keycloak_container_env / _secrets (conditional)
        |                       aws_iam_role_policy.keycloak_task_rds_connect (count = flag ? 1 : 0)
        v
  ECS task definition ......... custom image w/ AWS Advanced JDBC Wrapper (flag on)

docker/keycloak/Dockerfile .... bundles aws-advanced-jdbc-wrapper JAR + RDS CA
scripts/bootstrap-iam-db-user.sh  one-time CREATE USER ... AWSAuthenticationPlugin
```

## Data Models

This is an infrastructure/configuration change. There are no application-level Pydantic models. The "data model" here is the Terraform variable and the composed container env/secrets structures.

### New Terraform Variable

```hcl
variable "keycloak_db_iam_auth_enabled" {
  description = <<-EOT
    Enable RDS IAM database authentication for the Keycloak Aurora MySQL cluster.
    When true: the cluster enables IAM auth, the ECS task role is granted
    rds-db:connect, and Keycloak connects with short-lived IAM tokens via the
    AWS Advanced JDBC Wrapper (no KC_DB_PASSWORD). Requires the custom-built
    Keycloak image that bundles the JDBC wrapper. When false (default): static
    password auth via Secrets Manager, unchanged from prior behavior.
  EOT
  type    = bool
  default = false
}
```

### Composed container secrets (conditional)

```hcl
# keycloak-ecs.tf, locals block
locals {
  # Base secrets always present
  keycloak_base_secrets = [
    { name = "KEYCLOAK_ADMIN",          valueFrom = aws_ssm_parameter.keycloak_admin.arn },
    { name = "KEYCLOAK_ADMIN_PASSWORD", valueFrom = aws_ssm_parameter.keycloak_admin_password.arn },
    { name = "KC_DB_URL",               valueFrom = aws_ssm_parameter.keycloak_database_url.arn },
    { name = "KC_DB_USERNAME",          valueFrom = "${aws_secretsmanager_secret.keycloak_db_secret.arn}:username::" },
  ]

  # KC_DB_PASSWORD only when using password auth (flag off)
  keycloak_password_secret = var.keycloak_db_iam_auth_enabled ? [] : [
    { name = "KC_DB_PASSWORD", valueFrom = "${aws_secretsmanager_secret.keycloak_db_secret.arn}:password::" },
  ]

  keycloak_container_secrets = concat(local.keycloak_base_secrets, local.keycloak_password_secret)
}
```

## API / CLI Design

No new HTTP endpoints or application CLI commands. The externally observable "commands" are:

### 1. Enable IAM auth via Terraform

**Description:** Operator sets the flag and applies.

**Invocation:**
```bash
cd terraform/aws-ecs
# in terraform.tfvars:
#   keycloak_db_iam_auth_enabled = true
#   keycloak_image_uri           = "<account>.dkr.ecr.<region>.amazonaws.com/keycloak:1.24.4"  # custom image
terraform plan
terraform apply
```

**Expected result:** Cluster shows `IAMDatabaseAuthenticationEnabled = true`; task definition has no `KC_DB_PASSWORD`; task role has `rds-db:connect`.

**Error cases:**
- Flag `true` but `keycloak_image_uri` still the stock public image: Keycloak fails to load `software.amazon.jdbc.Driver` and the task health check fails. Surfaced as a plan-time warning in docs and (optionally) a Terraform validation.

### 2. Bootstrap the IAM DB user (one-time)

**Description:** Create the MySQL user that maps to IAM auth.

**Invocation (documented, run once after IAM auth is enabled on the cluster):**
```bash
# Executed as the master user against the Aurora endpoint (see Implementation Details)
CREATE USER 'keycloak_iam'@'%' IDENTIFIED WITH AWSAuthenticationPlugin AS 'RDS';
GRANT ALL PRIVILEGES ON keycloak.* TO 'keycloak_iam'@'%';
FLUSH PRIVILEGES;
```

**Expected output:** User created; `SELECT plugin FROM mysql.user WHERE user='keycloak_iam';` returns `AWSAuthenticationPlugin`.

## Configuration Parameters

### New Environment Variables (container)

| Variable Name | Type | Default | Required | Description |
|---------------|------|---------|----------|-------------|
| `KC_DB_DRIVER` | string | (unset) | When flag on | Set to `software.amazon.jdbc.Driver` so Keycloak uses the AWS Advanced JDBC Wrapper |
| `KC_DB_PASSWORD` | secret | from Secrets Manager | When flag off only | Removed from the task when IAM auth is enabled |

Note: The IAM plugin activation and TLS settings are carried in the JDBC URL (`KC_DB_URL`) rather than separate env vars, keeping the change centralized in one SSM parameter.

### New Terraform Variables

| Variable Name | Type | Default | Required | Description |
|---------------|------|---------|----------|-------------|
| `keycloak_db_iam_auth_enabled` | bool | `false` | No | Master switch for IAM DB auth (see Data Models) |

### JDBC URL forms (SSM `/keycloak/database/url`)

```hcl
# Password auth (flag off) - unchanged
"jdbc:mysql://${aws_rds_cluster.keycloak.endpoint}:3306/keycloak"

# IAM auth (flag on) - AWS Advanced JDBC Wrapper scheme + iam plugin + TLS
"jdbc:aws-wrapper:mysql://${aws_rds_cluster.keycloak.endpoint}:3306/keycloak?wrapperPlugins=iam&sslMode=VERIFY_IDENTITY"
```

### Deployment Surface Checklist

Every surface that must be touched for the new flag:

- [ ] `terraform/aws-ecs/variables.tf` - declare `keycloak_db_iam_auth_enabled`.
- [ ] `terraform/aws-ecs/terraform.tfvars.example` - document the flag and that `keycloak_database_password` is optional when it is `true`.
- [ ] `terraform/aws-ecs/keycloak-database.tf` - conditional `iam_database_authentication_enabled`; conditional JDBC URL; gate rotation.
- [ ] `terraform/aws-ecs/keycloak-ecs.tf` - conditional env/secrets locals; new task-role policy.
- [ ] `terraform/aws-ecs/secret-rotation.tf` / `secret-rotation-config.tf` - `count` gated off when flag on.
- [ ] `docker/keycloak/Dockerfile` - bundle JDBC wrapper JAR + RDS CA; set `KC_DB_DRIVER`.
- [ ] `terraform/aws-ecs/scripts/bootstrap-iam-db-user.sh` (new) + wire into `post-deployment-setup.sh`.
- [ ] `terraform/aws-ecs/README.md` and `OPERATIONS.md` - enable/rollback/bootstrap docs.
- [ ] docker-compose*.yml - NO CHANGE (local PostgreSQL path, out of scope).

## New Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `aws-advanced-jdbc-wrapper` (`software.amazon.jdbc`) | `2.5.x` (latest stable) | Generates and caches short-lived RDS IAM auth tokens at the JDBC layer; only added to the Keycloak container image, only exercised when the flag is on |

No new Python or application-runtime dependencies. The wrapper is a Java JAR bundled into the Keycloak image; the base MySQL JDBC driver Keycloak already ships is retained for the password-auth path.

## Implementation Details

### Step-by-Step Plan (for a future implementer)

#### Step 1: Add the feature-flag variable
**File:** `terraform/aws-ecs/variables.tf` (new block near line 101, after `keycloak_database_password`)

```hcl
variable "keycloak_db_iam_auth_enabled" {
  description = "Enable RDS IAM database authentication for Keycloak (see LLD). Default false = static password auth."
  type        = bool
  default     = false
}
```

#### Step 2: Enable IAM auth on the cluster conditionally
**File:** `terraform/aws-ecs/keycloak-database.tf` (line 48-81 block)

```hcl
resource "aws_rds_cluster" "keycloak" {
  # ...existing...
  master_username = var.keycloak_database_username
  master_password = var.keycloak_database_password  # still required to create the cluster / master user

  iam_database_authentication_enabled = var.keycloak_db_iam_auth_enabled
  # ...
}
```

Remove or rewrite the checkov skip at line 43. When the flag can enable IAM auth, replace the blanket skip with a conditional-justified comment, e.g.:
```hcl
#checkov:skip=CKV_AWS_162:IAM database authentication is opt-in via keycloak_db_iam_auth_enabled
```

Note: `master_password` remains set because the master user still uses password auth and is needed to bootstrap the IAM user. Only the Keycloak *application* user switches to IAM. This keeps the fallback trivially available.

#### Step 3: Make the JDBC URL IAM-aware
**File:** `terraform/aws-ecs/keycloak-database.tf` (line 277-283)

```hcl
resource "aws_ssm_parameter" "keycloak_database_url" {
  name   = "/keycloak/database/url"
  type   = "SecureString"
  key_id = aws_kms_key.rds.id
  value = var.keycloak_db_iam_auth_enabled ? (
    "jdbc:aws-wrapper:mysql://${aws_rds_cluster.keycloak.endpoint}:3306/keycloak?wrapperPlugins=iam&sslMode=VERIFY_IDENTITY"
  ) : (
    "jdbc:mysql://${aws_rds_cluster.keycloak.endpoint}:3306/keycloak"
  )
  tags = local.common_tags
}
```

#### Step 4: Grant the task role rds-db:connect (conditional)
**File:** `terraform/aws-ecs/keycloak-ecs.tf` (new resource after the task role, ~line 274)

```hcl
resource "aws_iam_role_policy" "keycloak_task_rds_connect" {
  count = var.keycloak_db_iam_auth_enabled ? 1 : 0
  name  = "keycloak-task-rds-connect-policy"
  role  = aws_iam_role.keycloak_task_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["rds-db:connect"]
        Resource = "arn:aws:rds-db:${var.aws_region}:${data.aws_caller_identity.current.account_id}:dbuser:${aws_rds_cluster.keycloak.cluster_resource_id}/${local.keycloak_iam_db_user}"
      }
    ]
  })
}
```

Add `local.keycloak_iam_db_user = "keycloak_iam"` (or make it a variable) in the `locals` block.

#### Step 5: Conditionally compose env + secrets
**File:** `terraform/aws-ecs/keycloak-ecs.tf` (locals lines 15-106)

- Add `KC_DB_DRIVER = software.amazon.jdbc.Driver` to `keycloak_container_env` only when the flag is on (conditional local + `concat`).
- Change `KC_DB_USERNAME` to source from the IAM user name when the flag is on. Simplest: keep reading `KC_DB_USERNAME` from the Secrets Manager `username` key but ensure that key equals the IAM user; or set it directly from `local.keycloak_iam_db_user` via a plain `environment` entry when IAM is on. Recommended: when IAM on, put `KC_DB_USERNAME` in `environment` (non-secret; it is just a username) set to `local.keycloak_iam_db_user`, and drop it from secrets.
- Drop `KC_DB_PASSWORD` from `keycloak_container_secrets` when the flag is on (see Data Models `concat` pattern).

```hcl
locals {
  keycloak_iam_db_user = "keycloak_iam"

  keycloak_iam_env = var.keycloak_db_iam_auth_enabled ? [
    { name = "KC_DB_DRIVER",   value = "software.amazon.jdbc.Driver" },
    { name = "KC_DB_USERNAME", value = local.keycloak_iam_db_user },
  ] : []

  keycloak_container_env = concat(local.keycloak_base_env, local.keycloak_iam_env)
}
```

#### Step 6: Gate the rotation stack
**Files:** `terraform/aws-ecs/secret-rotation.tf`, `secret-rotation-config.tf`

Add `count = var.keycloak_db_iam_auth_enabled ? 0 : 1` to the rotation Lambda, its schedule (`aws_secretsmanager_secret_rotation`), and rotation-only SGs/policies. Under IAM auth there is no password to rotate. Keep the Secrets Manager secret itself present (the master password still lives there) but stop scheduled rotation. Guard any references with the `[0]` index / `try(...)`.

#### Step 7: Bundle the JDBC wrapper + CA into the image
**File:** `docker/keycloak/Dockerfile`

```dockerfile
FROM quay.io/keycloak/keycloak:25.0 as builder
ENV KC_HEALTH_ENABLED=true
ENV KC_METRICS_ENABLED=true
ENV KC_FEATURES=token-exchange
ENV KC_DB=mysql
WORKDIR /opt/keycloak

# Bundle AWS Advanced JDBC Wrapper for optional RDS IAM auth. Harmless when
# password auth is used; activated only when KC_DB_URL uses the aws-wrapper scheme.
ARG AWS_JDBC_WRAPPER_VERSION=2.5.4
ADD https://github.com/aws/aws-advanced-jdbc-wrapper/releases/download/${AWS_JDBC_WRAPPER_VERSION}/aws-advanced-jdbc-wrapper-${AWS_JDBC_WRAPPER_VERSION}.jar \
    /opt/keycloak/providers/aws-advanced-jdbc-wrapper.jar

RUN keytool -genkeypair -storepass password -storetype PKCS12 -keyalg RSA -keysize 2048 -dname "CN=server" -alias server -ext "SAN:c=DNS:localhost,IP:127.0.0.1" -keystore conf/server.keystore
RUN /opt/keycloak/bin/kc.sh build

FROM quay.io/keycloak/keycloak:25.0
COPY --from=builder /opt/keycloak/ /opt/keycloak/
WORKDIR /opt/keycloak
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8080/health/ready || exit 1
USER keycloak
ENTRYPOINT ["/opt/keycloak/bin/kc.sh", "start", "--optimized"]
```

TLS trust: The AWS Advanced JDBC Wrapper ships an embedded RDS CA bundle it uses for `VERIFY_IDENTITY`, so no extra truststore step is strictly required; if the deployment pins a custom truststore, add the RDS global CA there. Document this.

#### Step 8: Bootstrap script for the IAM DB user
**File:** `terraform/aws-ecs/scripts/bootstrap-iam-db-user.sh` (new)

A hardened Bash script (`set -euo pipefail`, no emojis) that:
1. Reads the master credentials from Secrets Manager (`aws secretsmanager get-secret-value --secret-id keycloak/database`) and the cluster endpoint from the SSM/Terraform outputs.
2. Runs the `CREATE USER ... IDENTIFIED WITH AWSAuthenticationPlugin AS 'RDS'`, `GRANT`, `FLUSH` statements via the `mysql` client over TLS.
3. Is idempotent (`CREATE USER IF NOT EXISTS`).

Wire an optional invocation into `terraform/aws-ecs/scripts/post-deployment-setup.sh` guarded by an env check (only when IAM auth is enabled). Use `AWS_REGION="${AWS_REGION:-us-east-1}"` matching the existing convention.

### Error Handling

- **Missing driver JAR (flag on, stock image):** Keycloak logs `ClassNotFoundException: software.amazon.jdbc.Driver` and fails readiness. Docs must state IAM auth requires the custom image; consider a Terraform `precondition` on the task definition asserting `keycloak_image_uri` is not the stock default when the flag is on.
- **IAM user not bootstrapped:** Aurora returns access-denied for `keycloak_iam`. Bootstrap must run before/at first IAM-enabled deploy; the script is idempotent and safe to re-run.
- **Token generation failure (missing `rds-db:connect`):** Connection denied. The conditional task-role policy must be applied in the same `terraform apply`.
- **Non-TLS connection:** RDS rejects. The IAM JDBC URL always sets `sslMode=VERIFY_IDENTITY`.

### Logging

- No application code logging changes. Rely on Keycloak's JDBC connection logs (CloudWatch `/ecs/keycloak`) and set `KEYCLOAK_LOGLEVEL=DEBUG` transiently to confirm the wrapper/iam plugin is active during rollout.
- The bootstrap script logs each step at INFO to stdout (captured by the operator's shell), never echoing the master password.

## Observability

### Tracing / Metrics / Logging Points

- **CloudWatch Logs `/ecs/keycloak`:** watch for successful DB connection on boot and absence of "Access denied".
- **RDS CloudWatch metrics:** `DatabaseConnections` should remain stable across the switch. Optionally enable the RDS `general`/`error` log exports temporarily to confirm IAM logins.
- **CloudTrail:** `rds-db:connect` is not logged per-connection, but IAM policy simulator / Access Analyzer can validate the scoped grant.
- **Task health check:** existing `curl :9000/health/ready` is the primary signal that DB connectivity works end-to-end.

## Scaling Considerations

- **Token generation cost:** The wrapper mints a token per new physical connection and caches it (~15 min). With a small Keycloak connection pool (default) on a single-task service (`desired_count = 1`, autoscale to 4), token generation volume is negligible.
- **Connection churn:** IAM tokens expire at 15 minutes; existing connections stay valid, only new connections need a fresh token. The wrapper refreshes transparently. No change to pool sizing.
- **Autoscaling:** Each new task assumes the same task role and independently generates tokens; scales linearly with task count. No shared bottleneck.
- **RDS Proxy:** Not on the IAM path in this design; if connection multiplexing becomes a need at higher scale, enabling `iam_auth = "REQUIRED"` on the proxy and pointing Keycloak at the proxy endpoint is a documented follow-up.

## File Changes

### New Files

| File Path | Description |
|-----------|-------------|
| `terraform/aws-ecs/scripts/bootstrap-iam-db-user.sh` | Idempotent one-time creation of the IAM-enabled MySQL user |

### Modified Files

| File Path | Lines | Change Description |
|-----------|-------|--------------------|
| `terraform/aws-ecs/variables.tf` | ~+6 | New `keycloak_db_iam_auth_enabled` variable |
| `terraform/aws-ecs/keycloak-database.tf` | ~+8 / mod | Conditional `iam_database_authentication_enabled`; conditional JDBC URL; rewrite checkov skip |
| `terraform/aws-ecs/keycloak-ecs.tf` | ~+40 | Conditional env/secrets locals; new `keycloak_task_rds_connect` policy; `keycloak_iam_db_user` local |
| `terraform/aws-ecs/secret-rotation.tf` | ~+6 | `count` gate on rotation resources |
| `terraform/aws-ecs/secret-rotation-config.tf` | ~+2 | `count` gate on `aws_secretsmanager_secret_rotation` |
| `docker/keycloak/Dockerfile` | ~+5 | Bundle AWS Advanced JDBC Wrapper JAR |
| `terraform/aws-ecs/scripts/post-deployment-setup.sh` | ~+10 | Optional bootstrap invocation when IAM auth on |
| `terraform/aws-ecs/terraform.tfvars.example` | ~+6 | Document the flag |
| `terraform/aws-ecs/README.md` | ~+40 | Enable/rollback/bootstrap docs |
| `terraform/aws-ecs/OPERATIONS.md` | ~+30 | Operational runbook for IAM auth |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| New code (Terraform + bootstrap script + Dockerfile) | ~120 |
| New tests (bootstrap dry-run + plan assertions + docs test steps) | ~60 |
| Modified code | ~80 |
| Documentation | ~110 |
| **Total** | **~370** |

## Testing Strategy

See `./testing.md` for the full plan. In brief: Terraform `plan` assertions for both flag states, backwards-compat verification that `false` produces an unchanged plan/task definition, deployment tests that confirm IAM login on the cluster and absence of `KC_DB_PASSWORD`, bootstrap-script idempotency, and a rollback test flipping the flag back to `false`.

## Alternatives Considered

### Alternative 1: Custom ECS entrypoint wrapper that calls `aws rds generate-db-auth-token` and exports `KC_DB_PASSWORD`
**Description:** A shell entrypoint mints a token at container start and sets `KC_DB_PASSWORD` to it before launching Keycloak.
**Pros:** No JDBC wrapper JAR; works with the stock image (plus AWS CLI).
**Cons:** Tokens expire at 15 minutes; Keycloak reconnects (pool refresh, network blips) after expiry would fail because the env var is fixed at boot. Would require a sidecar/refresh loop and Keycloak restart on expiry. Fragile and outage-prone.
**Why Rejected:** Does not survive the 15-minute token lifetime; the JDBC wrapper is purpose-built to refresh tokens per connection.

### Alternative 2: Route Keycloak through RDS Proxy with `iam_auth = REQUIRED`
**Description:** Flip the existing proxy to IAM and point Keycloak at the proxy endpoint.
**Pros:** Proxy already exists; connection pooling; token handling can be centralized.
**Cons:** Still needs the client to present an IAM token to the proxy (same wrapper requirement), adds a hop, and changes the connection endpoint. More moving parts for the same client-side change.
**Why Rejected:** Does not remove the client-side wrapper requirement and adds complexity. Kept as a documented scaling follow-up.

### Alternative 3: Sidecar container that generates tokens and writes to a shared volume
**Description:** A sidecar refreshes a token file that Keycloak reads.
**Pros:** Keeps Keycloak image unmodified.
**Cons:** Keycloak has no mechanism to re-read a password from a file per connection; same expiry problem as Alternative 1, plus volume-sharing complexity.
**Why Rejected:** Keycloak cannot consume a rotating file-based password natively.

### Comparison Matrix

| Criteria | Chosen (JDBC wrapper) | Alt 1 (entrypoint token) | Alt 2 (RDS Proxy IAM) | Alt 3 (sidecar) |
|----------|-----------------------|--------------------------|-----------------------|-----------------|
| Survives 15-min token expiry | Yes | No | Yes | No |
| Client image change required | Yes (JAR) | Small (CLI) | Yes (JAR) | No |
| Extra infra hops | No | No | Yes | Yes |
| Complexity | Low | Medium | Medium | High |
| Fallback preserved cleanly | Yes | Yes | Partial | Yes |

## Rollout Plan

- **Phase 0 (prep):** Build and push the custom Keycloak image with the JDBC wrapper. Merge Terraform changes with the flag defaulting to `false` (no behavior change).
- **Phase 1 (bootstrap):** In a target environment, run `terraform apply` with `keycloak_db_iam_auth_enabled = true` to enable IAM auth on the cluster (this alone changes nothing about how Keycloak logs in yet if the app user is unchanged). Run `bootstrap-iam-db-user.sh` to create `keycloak_iam`.
- **Phase 2 (switch):** Deploy the task definition using the IAM JDBC URL, `KC_DB_DRIVER`, and no `KC_DB_PASSWORD`. Verify health and DB connectivity.
- **Phase 3 (rollback if needed):** Set the flag back to `false`, `terraform apply`, redeploy. Password auth via Secrets Manager resumes immediately (master password and secret were never removed).
- **Phase 4 (future, out of scope):** Once IAM auth is proven the default, decommission the rotation stack and consider removing the master password from state.

## Open Questions

- Should `keycloak_iam_db_user` be a Terraform variable (operator-overridable) or a fixed local? Default: fixed local `keycloak_iam`, documented.
- Do we want a Terraform `precondition` that fails the plan when the flag is `true` but `keycloak_image_uri` is still the stock public image? Recommended yes, to prevent a foot-gun.
- Should the master user itself eventually move to IAM auth, or remain password-based for break-glass/bootstrap? This design keeps it password-based intentionally.

## References

- AWS Advanced JDBC Wrapper - IAM Authentication Plugin: https://github.com/aws/aws-advanced-jdbc-wrapper
- RDS IAM database authentication (MySQL): https://docs.aws.amazon.com/AmazonRDS/latest/AeruroraUserGuide/UsingWithRDS.IAMDBAuth.html
- Keycloak database configuration (`KC_DB_DRIVER`, `KC_DB_URL`): https://www.keycloak.org/server/db
- Repo files: `terraform/aws-ecs/keycloak-database.tf`, `terraform/aws-ecs/keycloak-ecs.tf`, `docker/keycloak/Dockerfile`
- Related: Issue #1026 (DB password rotation drift), Issue #1122 (Keycloak 25 config)
