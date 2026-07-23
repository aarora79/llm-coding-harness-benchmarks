# GitHub Issue: Replace Keycloak Database Password with RDS IAM Authentication

## Title
Support RDS IAM authentication for Keycloak's Aurora MySQL database, with password auth as a feature-flagged fallback

## Labels
- enhancement
- security
- infra

## Description

### Problem Statement
Keycloak's Aurora MySQL cluster (`aws_rds_cluster.keycloak` in `terraform/aws-ecs/keycloak-database.tf`) currently authenticates with a static master username/password. The password is a Terraform-supplied secret (`var.keycloak_database_password`), stored in Secrets Manager (`keycloak/database`) and rotated every 30 days by a dedicated Lambda (`terraform/aws-ecs/lambda/rotate-rds/index.py`). The cluster resource carries an explicit `#checkov:skip=CKV_AWS_162` comment acknowledging that IAM database authentication is not used.

Long-lived static database credentials are a standing security liability even with rotation in place: the password is readable by anything with `secretsmanager:GetSecretValue` on the secret, it must be distributed to the ECS task via the `secrets` block, and a leaked value remains valid until the next rotation cycle (today, up to 30 days). AWS RDS IAM database authentication replaces the password with short-lived (15-minute) auth tokens signed by IAM/STS, removing the long-lived credential from the trust chain entirely.

### Proposed Solution
Enable IAM database authentication on the Keycloak Aurora MySQL cluster and switch the Keycloak ECS task to generate short-lived IAM auth tokens via `rds:GenerateDBAuthToken` instead of reading a static password from Secrets Manager. Specifically:

1. Set `iam_database_authentication_enabled = true` on `aws_rds_cluster.keycloak`.
2. Create a dedicated Aurora MySQL database user (via a one-time bootstrap step) with `AWSAuthenticationPlugin` (`AWS_AUTH_ONLY` or IAM plugin) so it can authenticate using generated auth tokens instead of a password.
3. Add IAM policy statements (`rds-db:connect`) to the Keycloak ECS task role, scoped to the specific `dbuser` resource ARN for the IAM-auth database user.
4. Add a Keycloak MySQL JDBC driver capable of generating and refreshing the IAM auth token as the JDBC password (the AWS JDBC Driver / IAM auth plugin for MySQL), since Keycloak's stock MySQL driver has no built-in support for IAM tokens, which expire every 15 minutes and must be regenerated per connection.
5. Introduce a feature flag (Terraform variable, e.g. `keycloak_database_use_iam_auth`, mirroring the existing `documentdb_use_iam` precedent) that defaults to `false` (password auth, current behavior) and, when `true`, switches the ECS task's DB credential wiring to IAM-token generation instead of the Secrets Manager password.
6. When the flag is `false`, all existing password-based resources (Secrets Manager secret, rotation Lambda, `KC_DB_USERNAME`/`KC_DB_PASSWORD` secrets in the task definition) continue to work exactly as they do today.

### User Stories
- As an operator running this stack on AWS ECS, I want to eliminate the long-lived Keycloak database password so that a leaked credential cannot be used to access the database beyond a 15-minute IAM token window.
- As an operator with an existing password-based deployment, I want to keep using password auth without any forced migration, so upgrading Terraform does not break my running Keycloak instance.
- As a security reviewer, I want IAM database access scoped to exactly the Keycloak task role via a `dbuser` resource ARN, so no other principal in the account can mint auth tokens for this database.

### Acceptance Criteria
- [ ] `aws_rds_cluster.keycloak` has `iam_database_authentication_enabled = true` (unconditionally; enabling the flag on the cluster does not disable password auth cluster-side).
- [ ] A new Terraform variable `keycloak_database_use_iam_auth` (bool, default `false`) controls the ECS task's DB credential wiring; existing deployments that do not set this variable see no behavior change.
- [ ] When `keycloak_database_use_iam_auth = true`, the ECS task role has an `rds-db:connect` policy statement scoped to the IAM-auth database user's `dbuser` ARN, and the Keycloak container generates a fresh auth token per connection rather than reading `KC_DB_PASSWORD` from Secrets Manager.
- [ ] When `keycloak_database_use_iam_auth = false` (default), the Secrets Manager secret, rotation Lambda, and `KC_DB_USERNAME`/`KC_DB_PASSWORD` task-definition secrets remain wired up exactly as before.
- [ ] The IAM-auth database user is created with an authentication plugin that supports IAM tokens, documented as a one-time manual or scripted bootstrap step (Aurora MySQL master password auth cannot itself be replaced by IAM auth for the `master_username` account without an explicit `CREATE USER ... IDENTIFIED WITH AWSAuthenticationPlugin` statement run against the database).
- [ ] Documentation (`terraform/aws-ecs/README.md`, `docs/unified-parameter-reference.md`) is updated to describe the new variable, the bootstrap step, and the tradeoffs of each mode.
- [ ] No change to the Keycloak version or image tag (`quay.io/keycloak/keycloak:25.0`).
- [ ] Terraform plan/apply succeeds with the flag left at its default (`false`) against an existing state, producing no destructive changes to the running cluster or task definition (other than the additive `iam_database_authentication_enabled` attribute, which is a non-disruptive in-place modification on Aurora).

### Out of Scope
- Migrating DocumentDB's existing `documentdb_use_iam` flag or behavior (already implemented and unaffected by this change).
- Removing or bypassing the existing Secrets Manager password rotation Lambda when the flag is `false`.
- Any Helm/EKS deployment path — this repository's Helm charts are not in scope; only the Terraform `aws-ecs` stack is addressed.
- Upgrading the Keycloak container image or Keycloak version.
- Automating the one-time SQL bootstrap step (`CREATE USER ... IDENTIFIED WITH AWSAuthenticationPlugin`) as part of `terraform apply`; it is documented as a manual/scripted step run once against the database, similar to how `init-keycloak.sh` performs one-time realm setup today.
- Local Docker Compose development (`docker-compose.yml` uses Postgres for Keycloak locally, not Aurora MySQL, and is unaffected).

### Dependencies
- Requires `aws` Terraform provider `>= 5.0` (already satisfied in `terraform/aws-ecs/main.tf`).
- Requires network egress from the Keycloak ECS task to AWS STS/RDS API endpoints (already available; the task runs in private subnets with a NAT gateway per the existing VPC module).

### Related Issues
- Reference: https://github.com/agentic-community/mcp-gateway-registry/issues/1303
- Related precedent: `documentdb_use_iam` (issue #955 and the DocumentDB IAM-auth implementation in `scripts/manage-documentdb.py`), which this design follows as the established pattern for optional IAM database authentication in this repository.
- Related: issue #1026 (KC_DB_USERNAME/KC_DB_PASSWORD sourced from Secrets Manager to avoid rotation drift) — the password-auth fallback path preserves that fix unchanged.
