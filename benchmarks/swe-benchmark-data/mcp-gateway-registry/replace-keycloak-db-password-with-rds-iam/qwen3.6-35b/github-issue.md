# GitHub Issue: Replace Keycloak Database Password with RDS IAM Authentication

## Title
Replace Keycloak Aurora MySQL static password with RDS IAM database authentication

## Labels
enhancement, security, terraform, infrastructure, aws

## Description

### Problem Statement

The Keycloak service deployed on AWS ECS connects to its Aurora MySQL database using a static password stored in AWS Secrets Manager at `keycloak/database`. The ECS task reads `KC_DB_USERNAME` and `KC_DB_PASSWORD` from this secret and injects them as environment variables into the Keycloak container.

This approach has several security and operational drawbacks:

1. **Long-lived credentials**: The database password persists in Secrets Manager, ECS task definition secrets, and Keycloak's JDBC connection pool throughout the deployment lifecycle. Any compromise of Secrets Manager, the ECS task metadata service, or a container escape grants persistent database access.

2. **Secret rotation coupling**: Although a rotation Lambda updates Secrets Manager on a schedule, Keycloak only picks up the new password after a container restart. Between rotation and restart, the old password is stale, risking connection failures. Issue #1026 demonstrated this class of problem when SSM parameters drifted from Secrets Manager values.

3. **No per-request authentication**: Static passwords do not provide per-request identity or auditability of database connections.

4. **Compliance gaps**: The existing `#checkov:skip=CKV_AWS_162` comment in `keycloak-database.tf` explicitly acknowledges that IAM database authentication is not in use, which many security frameworks flag as a finding.

5. **Unnecessary RDS Proxy complexity**: The RDS Proxy is configured with `auth_scheme = "SECRETS"` solely to fetch static credentials. With IAM auth, the proxy's internal secret dependency can be simplified.

### Proposed Solution

Replace the static password-based JDBC authentication with Amazon RDS IAM authentication for the Keycloak ECS deployment on AWS.

The solution will:

1. Enable IAM database authentication on the Aurora MySQL cluster (`keycloak`) at the MySQL user level.
2. Update the RDS Proxy (`aws_db_proxy.keycloak`) to support IAM authentication (`iam_auth = "ENABLED"`).
3. Attach a new IAM policy (`keycloak-rds-db-auth-policy`) to the Keycloak ECS task role granting `rds-db:connect` scoped to the Keycloak database user ARN.
4. Modify the Keycloak ECS task to generate short-lived (15-minute) IAM auth tokens at startup via a wrapper script using the AWS CLI.
5. Add a feature flag `KEYCLOAK_DB_IAM_AUTH_ENABLED` with default `false` so that existing deployments continue using password auth as a fallback until the flag is explicitly set to `true`.
6. When IAM auth is active, remove `KC_DB_PASSWORD` from the ECS container secrets block (no longer sourced from Secrets Manager).
7. Remove the secret rotation Lambda and Secrets Manager secret when IAM auth is permanently enabled.

### User Stories

- As a security engineer, I want the Keycloak database to use IAM authentication so that credentials do not persist in Secrets Manager or environment variables.
- As an SRE, I want database authentication to be automatic via IAM tokens so that I no longer need to coordinate secret rotations with Keycloak restarts.
- As a platform operator, I want a feature flag to control IAM auth rollout so that I can test and roll back without rebuilding the container image.
- As a developer, I want the docker-compose local development setup to continue using password authentication since IAM auth is AWS-specific.

### Acceptance Criteria

- [ ] IAM database authentication is enabled on the Keycloak Aurora MySQL cluster.
- [ ] The MySQL `keycloak` user is created with `AWSAuthenticationPlugin`.
- [ ] The RDS Proxy `aws_db_proxy.keycloak` is updated to `iam_auth = "ENABLED"`.
- [ ] A new IAM policy granting `rds-db:connect` is attached to the Keycloak ECS task role.
- [ ] The Keycloak ECS task generates an IAM auth token at container startup and uses it as `KC_DB_PASSWORD` when `KEYCLOAK_DB_IAM_AUTH_ENABLED=true`.
- [ ] When `KEYCLOAK_DB_IAM_AUTH_ENABLED=false` (default), the existing Secrets Manager `KC_DB_USERNAME`/`KC_DB_PASSWORD` flow works unchanged.
- [ ] The `aws_secretsmanager_secret.keycloak_db_secret` is removed from the ECS task secrets block when IAM auth is active.
- [ ] Terraform plan shows only expected changes with no unexpected resource replacements.
- [ ] The checkov skip `CKV_AWS_162` is removed from `keycloak-database.tf`.
- [ ] Keycloak starts, connects to the database, and serves OIDC requests successfully with IAM auth enabled.
- [ ] Keycloak starts, connects to the database, and serves OIDC requests successfully with IAM auth disabled (password auth fallback).
- [ ] No change to the Keycloak Docker image version (still `quay.io/keycloak/keycloak:25.0`).
- [ ] Docker Compose local development is unaffected.

### Out of Scope

- Helm/EKS deployment changes (scoped to ECS + Terraform only).
- Changing the Keycloak Docker image base or Keycloak version.
- Docker Compose local development changes (PostgreSQL-based dev stack remains unchanged).
- IAM authentication for the registry's DocumentDB connection (separate concern).
- Replacing or modifying the secret rotation Lambda (removed entirely when IAM auth is enabled).

### Dependencies

- Aurora MySQL Serverless v2 supports IAM database authentication (current engine version `8.0.mysql_aurora.3.10.3`).
- The ECS task must have a way to call `rds-db:connect` at startup (AWS CLI bundled or installed in a wrapper).
- The ECS task role must be able to use IMDS credentials to generate tokens.

### Related Issues

- #1026: Moved Keycloak DB credentials from SSM to Secrets Manager after rotation Lambda stopped updating SSM parameters.
- Related to the `CKV_AWS_162` checkov skip in `keycloak-database.tf`.