# Low-Level Design: Replace Keycloak Database Password with RDS IAM Authentication

*Created: 2026-06-25*
*Author: Claude (claude-opus-4-8)*
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

The Keycloak ECS service authenticates to its Aurora database with a static
username/password. The password is stored in the Secrets Manager secret
`keycloak/database`, injected into the Keycloak container as `KC_DB_PASSWORD`, and
rotated every 30 days by a Lambda. We want to remove the standing password entirely
and replace it with **RDS IAM database authentication**: the database user is
IAM-authenticated, and the Keycloak task presents a short-lived (15 minute) signed
token minted from its ECS task role identity. Database access then becomes a
function of the `rds-db:connect` IAM permission, revocable instantly via IAM and
never materialized as a long-lived secret.

### Critical Finding: The Production Database Is Aurora MySQL, Not PostgreSQL

The originating request (#1303) calls the database "PostgreSQL." That is accurate
only for the local `docker-compose*.yml` developer stack, which runs a plain
`postgres:16` container. The **production** Terraform under `terraform/aws-ecs/`
provisions **Aurora MySQL 8.0**:

```hcl
# terraform/aws-ecs/keycloak-database.tf:48-81
resource "aws_rds_cluster" "keycloak" {
  cluster_identifier = "keycloak"
  engine             = "aurora-mysql"
  engine_version     = "8.0.mysql_aurora.3.10.3"
  database_name      = "keycloak"
  master_username    = var.keycloak_database_username
  master_password    = var.keycloak_database_password
  ...
}
```

and an RDS Proxy in front of it:

```hcl
# terraform/aws-ecs/keycloak-database.tf:6-28
resource "aws_db_proxy" "keycloak" {
  name          = "keycloak-proxy"
  engine_family = "MYSQL"
  auth {
    auth_scheme               = "SECRETS"
    secret_arn                = aws_secretsmanager_secret.keycloak_db_secret.arn
    client_password_auth_type = "MYSQL_CACHING_SHA2_PASSWORD"
    iam_auth                  = "DISABLED"
  }
  ...
}
```

RDS IAM authentication is supported on Aurora MySQL 8.0, so the task is achievable
on the real stack. This design **targets Aurora MySQL** and flags every place the
engine difference matters (JDBC driver, port 3306, token-signing username,
MySQL `AWSAuthenticationPlugin` instead of `rds_iam` role grant). The
docker-compose PostgreSQL stack is explicitly out of scope.

### Goals

- Enable IAM database authentication on the Aurora MySQL cluster.
- Create an IAM-authenticated DB user used by Keycloak instead of the master user.
- Grant the Keycloak ECS **task role** a tightly scoped `rds-db:connect` permission.
- Mint a short-lived IAM auth token at container start and feed it to Keycloak as
  `KC_DB_PASSWORD`, refreshing connections before the 15-minute TTL expires.
- Remove the static application password from Terraform inputs, the ECS task
  `secrets` block, and the application-auth secret.
- Require TLS on the DB connection (mandatory for IAM auth) and reconcile the RDS
  Proxy auth path.

### Non-Goals

- Changing the database engine.
- Adding IAM auth to the local docker-compose PostgreSQL stack.
- Changing Keycloak admin credentials or realm bootstrap.
- IAM auth for DocumentDB or any other datastore.

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `terraform/aws-ecs/keycloak-database.tf` | Aurora MySQL cluster, RDS Proxy, KMS key, `keycloak/database` secret, DB URL SSM param | Primary target: enable IAM auth, reconcile proxy, retire app password |
| `terraform/aws-ecs/keycloak-ecs.tf` | Keycloak ECS task definition, task role, task-exec role, env/secrets | Primary target: task role `rds-db:connect`, drop `KC_DB_PASSWORD` secret, entrypoint/command |
| `terraform/aws-ecs/keycloak-security-groups.tf` | SGs for ECS, DB, proxy on port 3306 | Verify egress/ingress still correct (TLS still 3306) |
| `terraform/aws-ecs/variables.tf` | `keycloak_database_username/password`, `keycloak_image_uri`, ACU vars | Retire/repurpose password var; possibly new IAM-user var |
| `terraform/aws-ecs/locals.tf` | `keycloak_container_env`, hostname locals | Add pool/TLS env, IAM token bootstrap env |
| `terraform/aws-ecs/secret-rotation.tf` + `secret-rotation-config.tf` | Lambda rotating `keycloak/database` every 30 days | Remove/repoint rotation once app password is gone |
| `terraform/aws-ecs/keycloak-ecr.tf` | ECR repo for a custom Keycloak image | Custom image is where AWS CLI + CA bundle + entrypoint go |
| `terraform/aws-ecs/codebuild.tf` | Builds `docker/keycloak/Dockerfile` and pushes to ECR | Build path for the modified image |
| `docker/keycloak/Dockerfile` | Custom Keycloak 25.0 image, `kc.sh build` with `KC_DB=mysql`, `--optimized` start | Add token tooling, CA bundle, entrypoint wrapper |
| `terraform/aws-ecs/scripts/init-keycloak.sh` | Post-deploy realm/init script | Confirm it does not depend on the static DB password |
| `docker-compose*.yml`, `.env.example` | Local dev stack (PostgreSQL) | Out of scope; must remain working with password auth |

### Existing Patterns Identified

1. **Inline `jsonencode()` IAM policies.** Every IAM policy in the stack is an
   inline `aws_iam_role_policy` built with `jsonencode({...})`; the repo does
   **not** use `aws_iam_policy_document` data sources.
   - Files: `keycloak-ecs.tf:169-229`, `keycloak-ecs.tf:255-274`,
     `keycloak-database.tf:239-255`.
   - A future implementer must add the `rds-db:connect` statement as an inline
     `aws_iam_role_policy` on `aws_iam_role.keycloak_task_role`, matching this style.

2. **Two-role ECS pattern.** `keycloak_task_exec_role` pulls images and reads
   secrets/SSM at task launch; `keycloak_task_role` holds runtime permissions used
   by the container process. Today the exec role reads the DB secret; the **task
   role** is the correct home for `rds-db:connect`, because the *running container*
   mints the token at runtime, not the ECS agent at launch.
   - Files: `keycloak-ecs.tf:141-209` (exec role), `keycloak-ecs.tf:232-274` (task role).

3. **Container config via `locals.tf` arrays.** `local.keycloak_container_env`
   (`locals.tf:15-75`) and `local.keycloak_container_secrets` (`keycloak-ecs.tf:77-105`)
   are arrays composed into the `jsonencode()` `container_definitions`. New env
   (pool sizing, TLS, region) goes into the env local; removed secrets come out of
   the secrets local.

4. **Non-optimized public-image fallback vs optimized custom image.** The ECS task
   defaults to `var.keycloak_image_uri = "quay.io/keycloak/keycloak:25.0"` with
   `command = ["start"]` (`keycloak-ecs.tf:289,297`), but a custom image built from
   `docker/keycloak/Dockerfile` runs `kc.sh start --optimized`. The IAM-token
   entrypoint wrapper belongs in the custom image; the public-image fallback cannot
   carry a wrapper script and must be documented as unsupported for IAM auth.

5. **Secrets-as-single-source-of-truth (issue #1026).** The comment at
   `keycloak-database.tf:285-297` records that SSM copies of the DB password were
   removed because they drifted from Secrets Manager after rotation and crashed
   Keycloak. The lesson: **do not introduce a second copy of any credential.** IAM
   auth helps here because the token is generated on demand and never stored.

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| `aws_rds_cluster.keycloak` | Modifies | Add `iam_database_authentication_enabled = true`; remove checkov skip CKV_AWS_162 |
| `aws_iam_role.keycloak_task_role` | Extends | New inline policy granting `rds-db:connect` on the `dbuser` ARN |
| `aws_ecs_task_definition` (keycloak) | Modifies | Remove `KC_DB_PASSWORD`/`KC_DB_USERNAME` secrets; add TLS+pool env; entrypoint wrapper |
| `docker/keycloak/Dockerfile` | Modifies | Install AWS CLI v2 + RDS CA bundle; add `docker-entrypoint-iam.sh` wrapper |
| `aws_db_proxy.keycloak` | Modifies or removes | Decision: enable `iam_auth` on proxy OR connect direct-to-cluster (see Architecture) |
| `aws_secretsmanager_secret.keycloak_db_secret` | Reduces/removes | No longer the app-login source; retained only if master user still needs it |
| `aws_secretsmanager_secret_rotation.keycloak_db_secret` | Removes/repoints | App password rotation no longer needed for Keycloak login |
| `aws_security_group_rule.keycloak_ecs_egress_db` | Verifies | Port 3306 egress still required (IAM auth is still a 3306 TLS connection) |

### Constraints and Limitations Discovered

- **15-minute token TTL vs long-lived JDBC connections.** Keycloak uses the Agroal
  pool. A connection authenticated with an IAM token keeps working after the token
  expires (the token authenticates at *connect* time only), but **new** connections
  opened after expiry need a fresh token. The wrapper generates the token once at
  startup; any connection opened later in the task lifetime (pool growth, recycle)
  would use a stale `KC_DB_PASSWORD`. This is the central design risk; see
  Implementation Details for the mitigation (cap pool max-lifetime and disable pool
  growth, or use a JDBC credentials provider plugin).
- **IAM auth requires TLS.** Aurora rejects IAM-auth connections that are not TLS.
  The current proxy sets `require_tls = false` (`keycloak-database.tf:21`) and the
  JDBC URL (`keycloak-database.tf:280`) has no `sslMode`. Both must change.
- **RDS Proxy + IAM is a separate axis.** The proxy currently authenticates to the
  DB with the Secrets Manager secret (`auth_scheme = "SECRETS"`, `iam_auth = "DISABLED"`).
  Client-to-proxy IAM auth and proxy-to-DB auth are distinct; enabling end-to-end
  IAM through a proxy is more complex than connecting directly. The design proposes
  bypassing the proxy for the Keycloak login path (documented trade-off).
- **Public-image fallback cannot run a wrapper.** IAM auth requires the custom ECR
  image; `var.keycloak_image_uri` defaulting to the public quay.io image is
  incompatible and must be guarded.
- **Token-signing username must match the DB user exactly.** The IAM token is
  signed for a specific `--username`; it must equal the MySQL user created with the
  `AWSAuthenticationPlugin`, and the `rds-db:connect` ARN must reference that same
  user. A mismatch yields `Access denied`.

## Architecture

### System Context Diagram

```
                         Before (static password)
+------------------+      KC_DB_USERNAME / KC_DB_PASSWORD (from Secrets Manager)
| Keycloak ECS     |----------------------------+
| task (container) |                            v
+------------------+                  +----------------------+
        |  KC_DB_URL (SSM)            | Secrets Manager      |
        |  3306 (no TLS)             | keycloak/database    |
        v                            | {username, password} |
+------------------+   SECRETS auth   +----------------------+
| RDS Proxy        |<-------------------------+
| (engine MYSQL,   |
|  iam_auth=DISABLED)
+------------------+
        | 3306
        v
+------------------+
| Aurora MySQL 8.0 |  master_username / master_password
+------------------+

                         After (RDS IAM auth)
+------------------+   1. entrypoint mints 15-min token via task-role identity
| Keycloak ECS     |      (aws rds generate-db-auth-token --username keycloak_iam)
| task (container) |   2. export KC_DB_PASSWORD=<token>; exec kc.sh start --optimized
|  task role:      |
|  rds-db:connect  |---- TLS 3306, user=keycloak_iam, password=<token> -------+
+------------------+                                                          v
        | KC_DB_URL (SSM, sslMode=VERIFY_CA)                       +--------------------+
        | KC_DB_POOL_MAX_LIFETIME < 15m                            | Aurora MySQL 8.0   |
        v                                                          | IAM auth ENABLED   |
   (RDS Proxy bypassed for Keycloak login - see decision)         | user keycloak_iam  |
                                                                   |  identified WITH   |
                                                                   |  AWSAuthentication-|
                                                                   |  Plugin            |
                                                                   +--------------------+
```

### Sequence Diagram (container start -> DB connection)

```
ECS Agent        Keycloak Container            STS/RDS            Aurora MySQL
   |   launch task     |                          |                   |
   |------------------>| entrypoint wrapper runs  |                   |
   |                   | read instance creds      |                   |
   |                   | (task role) from ECS     |                   |
   |                   | credential endpoint      |                   |
   |                   |------------------------->|                   |
   |                   | generate-db-auth-token   |                   |
   |                   |  (signed w/ SigV4,        |                   |
   |                   |   user=keycloak_iam)      |                   |
   |                   |<-------------------------|                   |
   |                   | export KC_DB_PASSWORD=tok|                   |
   |                   | exec kc.sh start --optimized                 |
   |                   | Agroal opens JDBC conn ------ TLS 3306 ------>|
   |                   |   user=keycloak_iam, password=token          |
   |                   |                          |  validate token   |
   |                   |                          |  via IAM (SigV4)   |
   |                   |<------------------------- connection OK ------|
   |                   | pool max-lifetime < 15m: connections recycle |
   |                   | (see token-refresh strategy below)           |
```

### Component Diagram

```
docker/keycloak/Dockerfile (custom image)
  +-- base: quay.io/keycloak/keycloak:25.0
  +-- installs: awscli v2 (or bundled SDK), RDS global CA bundle -> /opt/keycloak/conf/rds-ca.pem
  +-- adds:    /opt/keycloak/bin/docker-entrypoint-iam.sh   (token wrapper)
  +-- ENTRYPOINT: docker-entrypoint-iam.sh  ->  kc.sh start --optimized

terraform/aws-ecs/
  +-- keycloak-database.tf : iam_database_authentication_enabled=true; TLS; proxy decision
  +-- keycloak-ecs.tf      : task-role rds-db:connect; secrets block drops KC_DB_PASSWORD
  +-- locals.tf            : KC_DB_URL sslMode, pool env, RDS_* bootstrap env
  +-- variables.tf         : retire keycloak_database_password; add keycloak_db_iam_user
  +-- secret-rotation*.tf  : remove/repoint app-password rotation
```

### Decision: Bypass the RDS Proxy for the Keycloak Login Path

The existing RDS Proxy authenticates **to** the database with the Secrets Manager
secret and presents itself to clients with `MYSQL_CACHING_SHA2_PASSWORD`. Threading
end-to-end IAM auth through a proxy requires the proxy itself to support client IAM
auth and to hold its own DB credentials, which reintroduces a stored secret and
adds moving parts. Because the goal is to eliminate the stored application password,
the cleanest path is:

- Point Keycloak's `KC_DB_URL` at the **cluster writer endpoint** directly
  (`aws_rds_cluster.keycloak.endpoint`), not the proxy endpoint, for the IAM-auth
  login.
- Keep the proxy resource only if other consumers need it; otherwise mark it for
  removal in a follow-up. (Today the only consumer wired to the proxy is Keycloak.)

This trade-off (losing the proxy's connection multiplexing) is acceptable because
Keycloak runs a small fixed pool and Aurora Serverless v2 scales connections; it is
called out explicitly in Alternatives Considered and Open Questions.

## Data Models

This is an infrastructure change; there are no application Pydantic/dataclass
models. The relevant "data models" are the database user, the IAM policy document,
and the ECS container `secrets`/`environment` arrays.

### New Database User (Aurora MySQL)

Created out-of-band (post-deploy SQL, run by `scripts/post-deployment-setup.sh` or a
new helper) against the cluster as the master user:

```sql
-- IAM-authenticated user for Keycloak. No password is stored; auth is via token.
CREATE USER 'keycloak_iam'@'%' IDENTIFIED WITH AWSAuthenticationPlugin AS 'RDS';
GRANT ALL PRIVILEGES ON keycloak.* TO 'keycloak_iam'@'%';
FLUSH PRIVILEGES;
```

Notes:
- `IDENTIFIED WITH AWSAuthenticationPlugin AS 'RDS'` is the MySQL form of IAM auth
  (the Aurora PostgreSQL equivalent would be `GRANT rds_iam TO keycloak`). This is
  the key engine-specific difference from the #1303 description.
- The user must be granted only `keycloak.*`, following least privilege; the master
  user remains for migrations/break-glass.

### IAM Policy Document (task role, inline `jsonencode`)

```hcl
{
  Version = "2012-10-17"
  Statement = [
    {
      Sid      = "KeycloakRdsIamConnect"
      Effect   = "Allow"
      Action   = ["rds-db:connect"]
      Resource = "arn:${data.aws_partition.current.partition}:rds-db:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:dbuser:${aws_rds_cluster.keycloak.resource_id}/${var.keycloak_db_iam_user}"
    }
  ]
}
```

`aws_rds_cluster.keycloak.resource_id` is the cluster DbiResourceId
(`cluster-XXXXXXXX`); for IAM auth against an Aurora cluster the cluster resource id
is the correct ARN component.

### ECS Container Secrets Array (after)

```hcl
# locals: local.keycloak_container_secrets  (KC_DB_PASSWORD and KC_DB_USERNAME removed)
[
  { name = "KEYCLOAK_ADMIN",          valueFrom = aws_ssm_parameter.keycloak_admin.arn },
  { name = "KEYCLOAK_ADMIN_PASSWORD", valueFrom = aws_ssm_parameter.keycloak_admin_password.arn },
  { name = "KC_DB_URL",               valueFrom = aws_ssm_parameter.keycloak_database_url.arn }
  # KC_DB_USERNAME is now a plain env (the IAM user name, not a secret)
  # KC_DB_PASSWORD is generated at runtime by the entrypoint, never injected here
]
```

## API / CLI Design

No HTTP API or application CLI changes. The "interface" added is the IAM token
generation command run inside the container entrypoint.

**Token generation (inside container, MySQL/Aurora):**
```bash
aws rds generate-db-auth-token \
  --hostname "$RDS_DB_HOST" \
  --port 3306 \
  --region "$AWS_REGION" \
  --username "$KC_DB_USERNAME"
```

**Output:** an opaque SigV4-signed token string (valid 15 minutes) written to
`KC_DB_PASSWORD` for the Keycloak process. Token is never logged.

**Error cases:**
- Missing `rds-db:connect` permission -> token still generated locally but Aurora
  returns `Access denied for user 'keycloak_iam'` at connect time.
- Clock skew in the container -> `SignatureDoesNotMatch`; mitigated by relying on
  the ECS host clock (NTP-synced).
- `AWS_REGION` unset -> token signed for wrong region -> auth failure.

## Configuration Parameters

### New / Changed Environment Variables (Keycloak container)

| Variable Name | Type | Default | Required | Description |
|---------------|------|---------|----------|-------------|
| `KC_DB_USERNAME` | string | `keycloak_iam` | Yes | IAM database user; now a plain env, not a secret |
| `KC_DB_PASSWORD` | string | (runtime token) | Yes | Set by entrypoint to the generated IAM token; never stored |
| `RDS_DB_HOST` | string | cluster endpoint | Yes | Hostname the entrypoint signs the token for; must match the JDBC host |
| `RDS_DB_PORT` | int | `3306` | No | Port for token signing |
| `AWS_REGION` | string | `var.aws_region` | Yes | Region for SigV4 token signing (already present in env) |
| `KC_DB_URL` | string | `jdbc:mysql://<endpoint>:3306/keycloak?sslMode=VERIFY_CA&trustCertificateKeyStoreUrl=...` | Yes | Now includes TLS params |
| `KC_DB_POOL_MAX_LIFETIME` | int (ms) | `600000` (10 min) | Yes | Below 15-min token TTL so no connection outlives its token (see note) |
| `KC_DB_POOL_INITIAL_SIZE` | int | `5` | No | Initial Agroal pool size |
| `KC_DB_POOL_MIN_SIZE` | int | `5` | No | Keep min=max to avoid opening new connections with a stale token |
| `KC_DB_POOL_MAX_SIZE` | int | `5` | No | Fixed pool; growth would open connections with an expired token |

> **Pool note for the implementer:** Keycloak/Quarkus does not expose every Agroal
> knob as a first-class `KC_*` option in 25.0. Confirm which of
> `KC_DB_POOL_*` map to real options for the bundled driver; settings not exposed as
> `KC_*` must be supplied via a `quarkus.datasource.jdbc.*` property in
> `conf/keycloak.conf` baked into the custom image. See Open Questions.

### Retired Variables / Resources

| Item | File | Action |
|------|------|--------|
| `var.keycloak_database_password` | `variables.tf:97-101` | Remove (or keep solely for master user creation, sensitive, documented) |
| `KC_DB_PASSWORD` secret entry | `keycloak-ecs.tf:101-104` | Remove from `local.keycloak_container_secrets` |
| `KC_DB_USERNAME` secret entry | `keycloak-ecs.tf:97-100` | Move to plain env (`local.keycloak_container_env`) as the IAM user |
| `aws_secretsmanager_secret_rotation.keycloak_db_secret` | `secret-rotation-config.tf:35-47` | Remove (app login no longer uses the secret) |
| checkov skip `CKV_AWS_162` | `keycloak-database.tf:43` | Remove once IAM auth is enabled |

### New Variables

```hcl
# variables.tf
variable "keycloak_db_iam_user" {
  description = "IAM-authenticated MySQL user Keycloak uses to connect to Aurora."
  type        = string
  default     = "keycloak_iam"
}
```

### Deployment Surface Checklist

- [ ] `terraform/aws-ecs/keycloak-database.tf` (IAM auth flag, TLS, proxy decision, DB URL)
- [ ] `terraform/aws-ecs/keycloak-ecs.tf` (task-role policy, secrets/env arrays)
- [ ] `terraform/aws-ecs/locals.tf` (env: TLS, pool, RDS host)
- [ ] `terraform/aws-ecs/variables.tf` (retire password var, add IAM-user var)
- [ ] `terraform/aws-ecs/secret-rotation-config.tf` (remove rotation)
- [ ] `docker/keycloak/Dockerfile` (AWS CLI, CA bundle, entrypoint)
- [ ] `terraform/aws-ecs/scripts/post-deployment-setup.sh` (create IAM DB user SQL)
- [ ] `terraform/aws-ecs/README.md` and `OPERATIONS.md` (operational model)
- [ ] Local `docker-compose*.yml`: **no change** (out of scope, keep password auth)

## New Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| AWS CLI v2 | latest | Mint `generate-db-auth-token` in the container entrypoint (or use a tiny SDK script) |
| Amazon RDS global CA bundle | current | TLS trust store for `sslMode=VERIFY_CA` (`rds-combined-ca-bundle.pem`) |

No new Terraform providers are required. No new Python/runtime libraries are added
to the application. If AWS CLI v2 is judged too heavy for the image, an alternative
is a ~20-line `boto3`/SDK script, but the CLI is simplest and is the recommended
default.

## Implementation Details

### Step-by-Step Plan (for a future implementer)

#### Step 1: Enable IAM auth on the Aurora cluster
**File:** `terraform/aws-ecs/keycloak-database.tf`
**Lines:** ~43 (remove skip), ~48-81 (add flag)

```hcl
# Remove this line (no longer accurate):
#   #checkov:skip=CKV_AWS_162:IAM database authentication not used ...

resource "aws_rds_cluster" "keycloak" {
  cluster_identifier                  = "keycloak"
  engine                              = "aurora-mysql"
  engine_version                      = "8.0.mysql_aurora.3.10.3"
  database_name                       = "keycloak"
  master_username                     = var.keycloak_database_username
  master_password                     = var.keycloak_database_password   # still needed to create the IAM user
  iam_database_authentication_enabled = true                              # <-- NEW
  ...
}
```

> Enabling IAM auth on Aurora is an online change (no downtime), but Terraform may
> show it as an in-place modify. Verify in `terraform plan`.

#### Step 2: Require TLS and fix the JDBC URL
**File:** `terraform/aws-ecs/keycloak-database.tf`
**Lines:** ~277-283 (the `keycloak_database_url` SSM param), ~21 (`require_tls`)

```hcl
resource "aws_ssm_parameter" "keycloak_database_url" {
  name   = "/keycloak/database/url"
  type   = "SecureString"
  key_id = aws_kms_key.rds.id
  # Connect directly to the cluster writer endpoint, TLS-verified against the RDS CA.
  value  = "jdbc:mysql://${aws_rds_cluster.keycloak.endpoint}:3306/keycloak?sslMode=VERIFY_CA&trustCertificateKeyStoreUrl=file:/opt/keycloak/conf/rds-truststore.p12&trustCertificateKeyStorePassword=changeit"
  tags   = local.common_tags
}
```

If the proxy is retained for other consumers, set `require_tls = true` on
`aws_db_proxy.keycloak` (`keycloak-database.tf:21`). For the Keycloak login path
this design connects directly to the cluster endpoint (see Architecture decision).

#### Step 3: Grant `rds-db:connect` to the task role
**File:** `terraform/aws-ecs/keycloak-ecs.tf`
**Lines:** new `aws_iam_role_policy` after the existing task-role policy (~274)

```hcl
resource "aws_iam_role_policy" "keycloak_task_rds_iam_policy" {
  name = "keycloak-task-rds-iam-policy"
  role = aws_iam_role.keycloak_task_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "KeycloakRdsIamConnect"
        Effect   = "Allow"
        Action   = ["rds-db:connect"]
        Resource = "arn:${data.aws_partition.current.partition}:rds-db:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:dbuser:${aws_rds_cluster.keycloak.resource_id}/${var.keycloak_db_iam_user}"
      }
    ]
  })
}
```

> `data.aws_partition.current` and `data.aws_caller_identity.current` already exist
> in the module (`ecs.tf:1-2`, `keycloak-ecr.tf` bottom). `data.aws_region.current`
> is used throughout `keycloak-database.tf`. Reuse them; do not redeclare.

#### Step 4: Remove the password secret, move username to plain env
**File:** `terraform/aws-ecs/keycloak-ecs.tf` (secrets local, ~77-105) and
`terraform/aws-ecs/locals.tf` (env local, ~15-75)

```hcl
# keycloak-ecs.tf - local.keycloak_container_secrets (AFTER)
keycloak_container_secrets = [
  { name = "KEYCLOAK_ADMIN",          valueFrom = aws_ssm_parameter.keycloak_admin.arn },
  { name = "KEYCLOAK_ADMIN_PASSWORD", valueFrom = aws_ssm_parameter.keycloak_admin_password.arn },
  { name = "KC_DB_URL",               valueFrom = aws_ssm_parameter.keycloak_database_url.arn }
  # KC_DB_USERNAME and KC_DB_PASSWORD removed
]
```

```hcl
# locals.tf - local.keycloak_container_env (ADD)
{ name = "KC_DB_USERNAME",          value = var.keycloak_db_iam_user },
{ name = "RDS_DB_HOST",             value = aws_rds_cluster.keycloak.endpoint },
{ name = "RDS_DB_PORT",             value = "3306" },
{ name = "KC_DB_POOL_MAX_LIFETIME", value = "600000" },
{ name = "KC_DB_POOL_INITIAL_SIZE", value = "5" },
{ name = "KC_DB_POOL_MIN_SIZE",     value = "5" },
{ name = "KC_DB_POOL_MAX_SIZE",     value = "5" }
```

Also drop the `secretsmanager:GetSecretValue` statement for
`keycloak_db_secret` from the **exec** role policy
(`keycloak-ecs.tf:188-200`) once the secret is no longer read for login.

#### Step 5: Custom image - AWS CLI, CA bundle, entrypoint wrapper
**File:** `docker/keycloak/Dockerfile`

```dockerfile
FROM quay.io/keycloak/keycloak:25.0 as builder
ENV KC_HEALTH_ENABLED=true
ENV KC_METRICS_ENABLED=true
ENV KC_FEATURES=token-exchange
ENV KC_DB=mysql
WORKDIR /opt/keycloak
RUN keytool -genkeypair -storepass password -storetype PKCS12 -keyalg RSA -keysize 2048 \
    -dname "CN=server" -alias server -ext "SAN:c=DNS:localhost,IP:127.0.0.1" \
    -keystore conf/server.keystore
RUN /opt/keycloak/bin/kc.sh build

FROM quay.io/keycloak/keycloak:25.0
USER root
# AWS CLI v2 for generate-db-auth-token
RUN curl -sSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscli.zip \
 && (cd /tmp && unzip -q awscli.zip && ./aws/install) && rm -rf /tmp/aws*
# RDS global CA bundle -> PKCS12 truststore for the MySQL JDBC driver
RUN curl -sSL "https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem" \
      -o /opt/keycloak/conf/rds-ca.pem \
 && keytool -importcert -noprompt -alias rds -file /opt/keycloak/conf/rds-ca.pem \
      -keystore /opt/keycloak/conf/rds-truststore.p12 -storetype PKCS12 -storepass changeit
COPY --from=builder /opt/keycloak/ /opt/keycloak/
COPY docker/keycloak/docker-entrypoint-iam.sh /opt/keycloak/bin/docker-entrypoint-iam.sh
RUN chmod +x /opt/keycloak/bin/docker-entrypoint-iam.sh
WORKDIR /opt/keycloak
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8080/health/ready || exit 1
USER keycloak
ENTRYPOINT ["/opt/keycloak/bin/docker-entrypoint-iam.sh"]
```

**New file:** `docker/keycloak/docker-entrypoint-iam.sh`

```bash
#!/usr/bin/env bash
# Mint a short-lived RDS IAM auth token and hand off to Keycloak.
# The token replaces the static DB password; it is never persisted or logged.
set -euo pipefail

: "${RDS_DB_HOST:?RDS_DB_HOST is required}"
: "${KC_DB_USERNAME:?KC_DB_USERNAME is required}"
: "${AWS_REGION:?AWS_REGION is required}"
RDS_DB_PORT="${RDS_DB_PORT:-3306}"

# generate-db-auth-token uses the ECS task role via the container credential endpoint.
token="$(aws rds generate-db-auth-token \
  --hostname "$RDS_DB_HOST" \
  --port "$RDS_DB_PORT" \
  --region "$AWS_REGION" \
  --username "$KC_DB_USERNAME")"

export KC_DB_PASSWORD="$token"

# Hand off to the normal optimized start. exec so Keycloak is PID 1's child and
# signals propagate correctly.
exec /opt/keycloak/bin/kc.sh start --optimized "$@"
```

> **Token-refresh caveat (read carefully).** The wrapper mints the token *once* at
> start. A token authenticates a connection only at *connect* time; established
> connections keep working past 15 minutes. The risk is **new** connections opened
> later (pool growth or max-lifetime recycle) reusing the now-stale
> `KC_DB_PASSWORD`. Two mitigations, in order of preference:
>
> 1. **Fixed pool, no recycle (simplest, recommended for v1):** set
>    `min=max=initial` and `KC_DB_POOL_MAX_LIFETIME` to `0`/very large so the
>    initial connections (authenticated at boot) are never recycled. Aurora idle
>    timeouts then become the constraint; pair with a validation query so dead
>    connections are detected. This keeps the simple "token once at boot" wrapper.
> 2. **Sidecar/refresher (robust):** a sidecar regenerates the token before expiry
>    and a JDBC credentials provider (custom Quarkus `AgroalDataSource` credential
>    provider) supplies a fresh token on each new physical connection. This is the
>    production-grade answer but adds a custom Keycloak provider JAR. Track as a
>    follow-up; see Alternatives Considered and Open Questions.
>
> v1 ships mitigation (1). The implementer MUST verify with a long-running test
> (Section 5 of testing.md) that the pool never opens a connection with an expired
> token.

#### Step 6: Create the IAM DB user post-deploy
**File:** `terraform/aws-ecs/scripts/post-deployment-setup.sh` (extend) or new
`scripts/create-keycloak-iam-user.sh`

Run as the master user (one-time, idempotent):

```sql
CREATE USER IF NOT EXISTS 'keycloak_iam'@'%' IDENTIFIED WITH AWSAuthenticationPlugin AS 'RDS';
GRANT ALL PRIVILEGES ON keycloak.* TO 'keycloak_iam'@'%';
FLUSH PRIVILEGES;
```

> This requires connectivity to the cluster (the existing init scripts already
> reach the DB from within the VPC, e.g. via ECS Exec or the init task). Reuse that
> path; do not open new public access.

#### Step 7: Remove app-password rotation
**File:** `terraform/aws-ecs/secret-rotation-config.tf` (~35-47)

Remove `aws_secretsmanager_secret_rotation.keycloak_db_secret` and any rotation
wiring specific to the Keycloak DB login. If the secret is still needed for the
master user, keep the secret but drop the *rotation* (or repoint rotation to manage
only the master credential, documented). The rotation Lambda
(`secret-rotation.tf`) may remain for other secrets.

#### Step 8: Guard the public-image fallback
**File:** `terraform/aws-ecs/variables.tf` / `keycloak-ecs.tf`

Add a precondition or documentation that IAM auth requires the custom ECR image
(the entrypoint wrapper). The public `quay.io/keycloak/keycloak:25.0` default cannot
mint tokens. Either default `keycloak_image_uri` to the ECR image or add a
`lifecycle precondition` that fails the plan if the default public image is used
together with IAM auth.

### Error Handling
- Entrypoint uses `set -euo pipefail`; missing required env aborts the container
  with a clear message before Keycloak starts.
- If `generate-db-auth-token` fails (no network/credentials), the container exits
  non-zero and ECS restarts it; CloudWatch logs capture the failure.
- Token contents are never echoed; only success/failure of generation is logged.

### Logging
- Log "Generated RDS IAM auth token (expires in 15m) for user=$KC_DB_USERNAME
  host=$RDS_DB_HOST" at INFO (no token value).
- Keep Keycloak's own DB connection logs at INFO; a connection failure will surface
  as `Access denied` and is the primary signal for misconfigured IAM/ARN/username.

## Observability

### Tracing / Metrics / Logging Points
- **Entrypoint:** one INFO log per token generation (success/failure), no secret.
- **CloudWatch Logs:** Keycloak Agroal pool warnings about connection acquisition
  timeouts are the leading indicator of a stale-token / pool-recycle problem.
- **RDS:** enable `DatabaseConnections` and, if available, IAM-auth failure metrics;
  an authentication-failure spike means the IAM user, ARN, or token signing is
  misconfigured.
- **CloudTrail:** `rds-db:connect` is not logged per-connection, but token signing
  uses the task role; verify the role's `sts`/credential usage if debugging.
- Add a CloudWatch alarm on Keycloak ECS task restart count (an auth failure loop
  manifests as crash-looping tasks).

## Scaling Considerations
- **Current load:** a single small Keycloak service; a fixed pool of ~5 connections
  is ample. Aurora Serverless v2 (0.5-2 ACU) absorbs the load.
- **Horizontal scaling:** if Keycloak scales to N tasks, each task mints its own
  token from the shared task role at start - no contention, no shared secret. IAM
  token generation is a local SigV4 signing operation (no API rate concern).
- **Bottlenecks:** the 15-minute token TTL vs pool lifetime is the only real
  constraint; the fixed-pool mitigation removes it for v1.
- **Caching:** none required; tokens are cheap to mint. Do not cache tokens beyond a
  single connection's lifetime.

## File Changes

### New Files

| File Path | Description |
|-----------|-------------|
| `docker/keycloak/docker-entrypoint-iam.sh` | Mints IAM token, exports `KC_DB_PASSWORD`, execs `kc.sh start --optimized` |
| `terraform/aws-ecs/scripts/create-keycloak-iam-user.sh` (or additions to `post-deployment-setup.sh`) | One-time idempotent SQL to create `keycloak_iam` with `AWSAuthenticationPlugin` |

### Modified Files

| File Path | Lines | Change Description |
|-----------|-------|--------------------|
| `terraform/aws-ecs/keycloak-database.tf` | ~43, 48-81, 277-283, (6-28 if proxy) | Enable IAM auth; remove checkov skip; TLS JDBC URL/direct endpoint; proxy decision |
| `terraform/aws-ecs/keycloak-ecs.tf` | 77-105, ~274 (new policy), 188-200 | Drop `KC_DB_PASSWORD`/`KC_DB_USERNAME` secrets; add task-role `rds-db:connect`; trim exec-role secret read |
| `terraform/aws-ecs/locals.tf` | 15-75 | Add IAM user, RDS host, TLS, pool env |
| `terraform/aws-ecs/variables.tf` | 90-101, new | Retire/repurpose `keycloak_database_password`; add `keycloak_db_iam_user` |
| `terraform/aws-ecs/secret-rotation-config.tf` | 35-47 | Remove app-password rotation |
| `docker/keycloak/Dockerfile` | full | AWS CLI v2, RDS CA truststore, entrypoint wrapper |
| `terraform/aws-ecs/scripts/post-deployment-setup.sh` | append | Invoke IAM-user creation |
| `terraform/aws-ecs/README.md`, `OPERATIONS.md` | docs | Document IAM auth model, token flow, troubleshooting |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| New code (entrypoint + SQL script) | ~70 |
| New tests (testing.md scenarios, harness scripts) | ~120 |
| Modified code (Terraform + Dockerfile) | ~120 |
| Docs | ~80 |
| **Total** | **~390** |

## Testing Strategy
See `./testing.md`. Highlights: `terraform validate`/`plan` assertions that no
static password appears in the rendered task definition; a grep gate confirming
`KC_DB_PASSWORD` is absent from the `secrets` block; a long-running (>15 min)
connection test proving the pool never opens a connection with an expired token; an
`Access denied` negative test when the `rds-db:connect` ARN/username is wrong; and a
backwards-compat check that the local docker-compose PostgreSQL stack is untouched.

## Alternatives Considered

### Alternative 1: Keep Secrets Manager + RDS Proxy, enable proxy IAM auth
**Description:** Leave the password in Secrets Manager for the proxy-to-DB hop, but
enable client-to-proxy IAM auth (`iam_auth = "REQUIRED"`).
**Pros:** Keeps connection multiplexing; smaller container change.
**Cons:** Does not eliminate the stored password (proxy still uses the secret to
reach the DB); two auth axes to reason about; reintroduces the credential-drift
surface that caused #1026.
**Why rejected:** The goal is to remove the standing password; this keeps it.

### Alternative 2: Custom Quarkus JDBC credentials provider (token on each connect)
**Description:** Ship a Keycloak provider JAR implementing an Agroal credential
provider that mints a fresh token for every physical connection.
**Pros:** Fully robust against token expiry regardless of pool behavior; no fixed-pool
constraint.
**Cons:** Requires Java provider development, a custom build, and ongoing maintenance
against Keycloak/Quarkus internals.
**Why rejected for v1 (kept as follow-up):** Higher complexity; the fixed-pool
entrypoint approach meets the requirement with far less code and risk. Recommended
as the production hardening follow-up.

### Alternative 3: Switch production to Aurora PostgreSQL to match #1303
**Description:** Replace Aurora MySQL with Aurora PostgreSQL so the wording matches.
**Pros:** Aligns with the literal issue text; `GRANT rds_iam` is arguably simpler.
**Cons:** A disruptive engine migration far beyond the scope of "replace the
password"; data migration, driver changes, realm-compat testing.
**Why rejected:** Out of scope and disproportionate; IAM auth works on Aurora MySQL.

### Comparison Matrix

| Criteria | Chosen (entrypoint token, fixed pool) | Alt 1 (proxy IAM) | Alt 2 (JDBC provider) | Alt 3 (engine swap) |
|----------|---------------------------------------|-------------------|------------------------|---------------------|
| Removes stored password | Yes | No | Yes | Yes |
| Complexity | Low-Med | Med | High | Very High |
| Token-expiry robustness | Med (needs fixed pool) | High | High | Med |
| Maintenance burden | Low | Med | High | High |
| Blast radius | Contained | Contained | Contained | Whole DB |

## Rollout Plan
- **Phase 1 - Implementation (out of scope for this skill):** apply Terraform with
  IAM auth enabled *and* the secret still present (additive), build the custom
  image, create the `keycloak_iam` user.
- **Phase 2 - Cutover:** flip the task to the IAM user + token entrypoint; keep the
  master password available for rollback. Verify connection and a >15-min soak.
- **Phase 3 - Cleanup:** remove the `KC_DB_PASSWORD` secret entry, retire rotation,
  drop the checkov skip, tighten the exec-role policy.
- **Rollback:** revert the task definition to the prior secret-based `KC_DB_PASSWORD`
  and image; the master user/password remain intact throughout.

## Open Questions
- Which `KC_DB_POOL_*` settings are first-class in Keycloak 25.0 vs requiring a
  baked `keycloak.conf` `quarkus.datasource.jdbc.*` property? (Affects Step 4/5.)
- Is the RDS Proxy used by anything other than Keycloak? If not, should it be
  removed entirely rather than left orphaned? (Affects Architecture decision.)
- Confirm the MySQL JDBC driver flavor Keycloak 25.0 bundles (MySQL Connector/J vs
  MariaDB driver) and its exact TLS property names (`sslMode` vs
  `useSSL`/`requireSSL`), since the JDBC URL depends on it.
- Should v1 go straight to the JDBC-credentials-provider approach (Alt 2) if the
  fixed-pool soak test reveals Aurora idle-timeout churn?
- Does `master_password` still need to exist in Terraform/Secrets Manager after
  cutover, or can master access be limited to break-glass via a separately managed
  credential?

## References
- AWS: IAM database authentication for Aurora MySQL (`generate-db-auth-token`,
  `AWSAuthenticationPlugin`, mandatory TLS).
- AWS: RDS global CA bundle (`truststore.pki.rds.amazonaws.com/global/global-bundle.pem`).
- Keycloak 25.0: database configuration (`KC_DB`, `KC_DB_URL`, `KC_DB_USERNAME`,
  `KC_DB_PASSWORD`), Agroal connection pool.
- Repo: `keycloak-database.tf:285-297` (issue #1026 credential-drift rationale).
