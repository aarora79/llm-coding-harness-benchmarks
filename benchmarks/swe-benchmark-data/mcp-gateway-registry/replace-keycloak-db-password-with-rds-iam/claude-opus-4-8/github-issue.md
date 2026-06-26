# GitHub Issue: Replace Keycloak Database Password with RDS IAM Authentication

## Title
Replace Keycloak static database password with RDS IAM authentication

## Labels
- enhancement
- security
- infra
- terraform

## Description

### Problem Statement

The Keycloak ECS deployment authenticates to its Aurora database using a static
username/password pair. The credentials live in an AWS Secrets Manager secret
(`keycloak/database`), are injected into the Keycloak container as the
`KC_DB_USERNAME` and `KC_DB_PASSWORD` environment variables, and are rotated
every 30 days by a Lambda function.

A long-lived static password, even one stored in Secrets Manager and rotated
monthly, has several drawbacks:

1. The password is materialized in the container environment for the lifetime of
   the task, where it can be read by anything that can inspect the process
   environment or an ECS Exec session.
2. Rotation is coarse (every 30 days) and has historically caused outages when
   the credential copy in one store drifted from the database (see the
   `keycloak-database.tf` comment referencing issue #1026, where stale SSM copies
   forced Keycloak to crash on every restart).
3. The secret is a standing target: anyone who exfiltrates it has database access
   until the next rotation.

RDS IAM database authentication removes the standing password. The database user
is marked as IAM-authenticated, and clients present a short-lived (15 minute)
signed token generated from their IAM identity instead of a password. Access is
then governed by the `rds-db:connect` IAM permission attached to the Keycloak ECS
task role, and can be revoked instantly by changing the IAM policy.

### Important Scope Clarification (Engine Discrepancy)

The originating request (issue #1303) describes the database as **PostgreSQL** and
asks to "configure RDS IAM auth on the PostgreSQL instance." The **production**
infrastructure under `terraform/aws-ecs/` does not use PostgreSQL. It provisions:

- An **Aurora MySQL 8.0** cluster (`aws_rds_cluster.keycloak`, engine
  `aurora-mysql`, version `8.0.mysql_aurora.3.10.3`).
- An **RDS Proxy** (`aws_db_proxy.keycloak`, `engine_family = "MYSQL"`) in front of
  the cluster, with `iam_auth = "DISABLED"` and `auth_scheme = "SECRETS"`.

PostgreSQL (`jdbc:postgresql://keycloak-db:5432/keycloak`) only appears in the
local `docker-compose*.yml` developer stack, which uses a plain `postgres`
container and is out of scope for IAM auth (IAM auth is an RDS feature; there is no
RDS in local Docker).

This issue therefore targets the **Aurora MySQL** production stack. RDS IAM
authentication works identically for Aurora MySQL and Aurora PostgreSQL; only the
JDBC driver, port (3306 vs 5432), and token plumbing differ. The design will call
out every place this engine difference matters so the implementer is not surprised.

### Proposed Solution

1. **Enable IAM database authentication** on the Aurora MySQL cluster
   (`iam_database_authentication_enabled = true`) and remove the
   checkov skip that documents the prior decision not to use it.
2. **Create an IAM-authenticated database user** (`keycloak_iam`) in the Aurora
   cluster mapped to the AWS authentication plugin, distinct from the master user.
3. **Grant `rds-db:connect`** to the Keycloak ECS **task role** scoped to the
   `dbuser` ARN built from the cluster `resource_id` and the IAM database user.
4. **Generate a short-lived IAM auth token at container start** via a small
   entrypoint wrapper that calls the AWS SDK / CLI to mint the token, exports it as
   `KC_DB_PASSWORD`, and then execs the normal Keycloak start command. The token is
   refreshed by recycling JDBC connections faster than the 15-minute token TTL.
5. **Remove the static DB password** from Terraform inputs, the Secrets Manager
   secret used for application auth, the ECS task `secrets` block, and the rotation
   Lambda wiring (subject to the migration sequencing in the LLD).
6. **Update IAM roles/policies, security groups, and the RDS Proxy auth path**
   accordingly, and require TLS for the database connection (IAM auth mandates TLS).

### User Stories

- As a security engineer, I want Keycloak to authenticate to its database with a
  short-lived IAM token instead of a static password, so that a leaked credential
  is useless within 15 minutes and access can be revoked via IAM.
- As an SRE, I want database access governed by IAM policy rather than a rotating
  secret, so that I no longer risk credential-drift outages during rotation.
- As a platform operator, I want no plaintext database password present in the
  container environment, Terraform state inputs, or long-lived secrets.

### Acceptance Criteria

- [ ] `iam_database_authentication_enabled = true` is set on the Aurora cluster and
      the corresponding checkov skip comment is removed.
- [ ] An IAM-authenticated database user exists in the Keycloak database and is
      used by the Keycloak ECS task.
- [ ] The Keycloak ECS **task role** has an `rds-db:connect` permission scoped to
      the specific `dbuser` resource ARN (not `*`).
- [ ] The Keycloak container obtains its DB password as a freshly generated IAM
      auth token at startup; no static `KC_DB_PASSWORD` is read from Secrets Manager
      for application login.
- [ ] The static database password variable
      (`var.keycloak_database_password`) and the application-auth Secrets Manager
      secret/version (`keycloak/database`) are removed or reduced to only what the
      master user genuinely still needs, with the rationale documented.
- [ ] The DB connection uses TLS (`require_tls`/`sslmode`) as required by IAM auth.
- [ ] JDBC connection max-lifetime is configured below the 15-minute token TTL so
      connections never outlive their token.
- [ ] The RDS Proxy auth path is reconciled: either IAM auth is enabled on the
      proxy or the proxy is bypassed/removed for the IAM-auth connection, with the
      decision documented.
- [ ] `terraform validate` passes and a `terraform plan` shows the intended
      changes with no static password in the planned task definition.
- [ ] Documentation (`terraform/aws-ecs/README.md`, `OPERATIONS.md`) is updated to
      describe IAM auth, token generation, and the new operational model.

### Out of Scope

- The local `docker-compose*.yml` developer stack (plain PostgreSQL container, no
  RDS). It keeps password auth; no IAM auth applies.
- Migrating the database engine between MySQL and PostgreSQL.
- Changing Keycloak's admin credentials (`KEYCLOAK_ADMIN` / `KEYCLOAK_ADMIN_PASSWORD`)
  or the realm bootstrap flow.
- Application-level Keycloak feature changes (realms, clients, token exchange).
- IAM auth for any other datastore in the stack (e.g. DocumentDB).

### Dependencies

- AWS Aurora MySQL support for IAM database authentication (available on Aurora
  MySQL 8.0).
- The Keycloak container image must contain a tool capable of minting an RDS auth
  token (AWS CLI v2 or an SDK). This may require extending
  `docker/keycloak/Dockerfile`.
- The Amazon RDS root/intermediate CA bundle must be available to the JDBC driver
  for TLS verification.

### Related Issues

- #1303 (originating request; describes the DB as PostgreSQL - see scope note above)
- #1026 (prior credential-drift outage caused by stale SSM copies of the DB password)
