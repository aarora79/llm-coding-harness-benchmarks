# GitHub Issue: Replace Keycloak database password with RDS IAM authentication

## Title
Replace Keycloak Aurora MySQL static password with RDS IAM database authentication (feature-flagged, password fallback retained)

## Labels
- enhancement
- security
- infra
- terraform

(Confirm against `gh label list` before filing upstream; apply only labels that already exist and suggest new ones in a comment rather than creating them during issue creation.)

## Description

### Problem Statement

The Keycloak service on AWS ECS authenticates to its Aurora MySQL Serverless v2 cluster with a static username and password. The password is supplied as a plaintext Terraform variable (`keycloak_database_password`), stored in AWS Secrets Manager (`keycloak/database`), rotated every 30 days by a dedicated Lambda, and injected into the Keycloak ECS task as the `KC_DB_PASSWORD` container secret.

A long-lived static database password is a standing credential that:

- Must be created, distributed, and rotated (the repo carries an entire rotation stack: `secret-rotation.tf`, `secret-rotation-config.tf`, `lambda/rotate-rds/index.py`).
- Lives in plaintext in `terraform.tfvars` on operator machines and in Terraform state.
- Has repeatedly caused outages when the stored copy drifts from the value in Aurora (see the extensive Issue #1026 comments in `keycloak-database.tf` and `keycloak-ecs.tf`, where Keycloak crash-looped with "Access denied for user keycloak" after each rotation).

RDS IAM database authentication removes the standing password entirely: the ECS task presents a short-lived (15-minute) IAM authentication token generated from its task role, so there is no password to store, rotate, or leak.

### Proposed Solution

Introduce RDS IAM database authentication for the Keycloak Aurora MySQL cluster, gated behind a new Terraform feature flag (`keycloak_db_iam_auth_enabled`, default `false`). When enabled:

1. Enable IAM database authentication on the Aurora MySQL cluster (`iam_database_authentication_enabled = true`).
2. Grant the Keycloak ECS task role the `rds-db:connect` action, scoped to the cluster resource id and the specific database user.
3. Configure the Keycloak container to connect using the AWS Advanced JDBC Wrapper's IAM authentication plugin, which generates and caches short-lived IAM auth tokens (equivalent to `rds:GenerateDBAuthToken`) on each new physical connection. This removes `KC_DB_PASSWORD` from the task definition.
4. Bootstrap an IAM-enabled MySQL database user (`IDENTIFIED WITH AWSAuthenticationPlugin`) that Keycloak logs in as.

When the flag is `false` (the default), behavior is byte-for-byte unchanged: password auth via Secrets Manager, the rotation stack, and `KC_DB_PASSWORD` all remain exactly as they are today. This preserves a fully supported fallback path with no Keycloak version change.

### User Stories

- As an operator deploying on AWS ECS + Aurora MySQL, I want Keycloak to authenticate to its database with a short-lived IAM token so that there is no static database password to store, rotate, or leak.
- As a security engineer, I want to eliminate the standing Keycloak DB credential and its plaintext presence in tfvars and Terraform state.
- As an operator on an existing deployment, I want to keep password authentication working unchanged until I explicitly opt in, so that upgrading carries no forced migration risk.
- As an SRE, I want to flip a single Terraform flag to switch a Keycloak environment between password auth and IAM auth (and back) so that I can roll forward or roll back safely.

### Acceptance Criteria

- [ ] A new Terraform variable `keycloak_db_iam_auth_enabled` (bool, default `false`) is added to `terraform/aws-ecs/variables.tf` and documented in `terraform.tfvars.example`.
- [ ] When `keycloak_db_iam_auth_enabled = true`, `aws_rds_cluster.keycloak` sets `iam_database_authentication_enabled = true`.
- [ ] When the flag is `true`, the Keycloak task role (`aws_iam_role.keycloak_task_role`) is granted `rds-db:connect` scoped to `arn:aws:rds-db:<region>:<account>:dbuser:<cluster-resource-id>/<db-user>`.
- [ ] When the flag is `true`, the Keycloak ECS task definition no longer includes the `KC_DB_PASSWORD` secret, and Keycloak connects using the AWS Advanced JDBC Wrapper IAM plugin over TLS.
- [ ] When the flag is `true`, IAM auth tokens are generated at runtime (short-lived, auto-refreshed per connection) rather than a static password being read from Secrets Manager.
- [ ] A documented, repeatable bootstrap step creates the IAM-enabled MySQL user with the `AWSAuthenticationPlugin`.
- [ ] When `keycloak_db_iam_auth_enabled = false` (default), the deployment is identical to today: `KC_DB_PASSWORD` from Secrets Manager, rotation Lambda active, `master_password` set. No regression.
- [ ] The `checkov:skip=CKV_AWS_162` comment on the cluster is removed or made conditional to reflect that IAM auth is now supported.
- [ ] Terraform README / OPERATIONS docs describe how to enable IAM auth, the bootstrap step, and how to roll back to password auth.
- [ ] TLS is required for IAM-authenticated connections, and the RDS CA bundle is trusted by the Keycloak container.

### Out of Scope

- Local development via docker-compose (which runs Keycloak on a PostgreSQL container, not Aurora MySQL). IAM auth applies only to the AWS ECS + Aurora path.
- Helm / EKS deployment surfaces (no Helm chart is used for Keycloak in this repo).
- Upgrading or changing the Keycloak version (remains `quay.io/keycloak/keycloak:25.0`).
- Migrating any other service (registry, auth-server, DocumentDB) to IAM auth.
- Removing the RDS password-rotation stack. It stays intact to support the fallback path; decommissioning it is a follow-up once IAM auth is the default.

### Dependencies

- The AWS Advanced JDBC Wrapper JAR (`aws-advanced-jdbc-wrapper`) must be bundled into the Keycloak container image (`docker/keycloak/Dockerfile`). This requires operators enabling IAM auth to deploy the custom-built image rather than the stock public image run non-optimized.
- Aurora MySQL 8.0 (already in use: `8.0.mysql_aurora.3.10.3`) supports IAM authentication.
- The ECS task must run in a subnet with network egress to the STS/RDS token-signing path (already satisfied: tasks run in private subnets with the necessary routing).

### Related Issues

- Reference issue: agentic-community/mcp-gateway-registry#1303
- Related context: Issue #1026 (DB password drift after rotation), Issue #1122 (Keycloak 25 hostname/proxy config).
