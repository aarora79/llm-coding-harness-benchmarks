# GitHub Issue: Replace Keycloak Database Password with RDS IAM Authentication

## Title
Replace static Keycloak database password with RDS IAM authentication (issue #1303)

## Labels
- security
- enhancement
- infrastructure
- aws

## Description

### Problem Statement
The current Keycloak deployment uses static database passwords stored in:
- Terraform state (`var.keycloak_database_password`)
- Secrets Manager (`keycloak/database` secret)
- ECS task definition secrets (`KC_DB_USERNAME`, `KC_DB_PASSWORD`)

Static passwords are a security audit liability because:
1. They are long-lived credentials stored at rest in multiple locations
2. They require manual rotation via Lambda (issue #1026)
3. Terraform state contains decrypted secrets
4. Passwords persist in ECS task definition history

### Proposed Solution
Replace static password authentication with AWS RDS IAM authentication:
1. Enable IAM authentication on the Aurora MySQL cluster
2. Configure Keycloak ECS task to generate short-lived IAM auth tokens at startup
3. Remove `keycloak_database_password` variable and related secrets
4. Update IAM role to allow `rds-db:connect` for the Keycloak task role

### User Stories
- As a DevOps engineer, I want Keycloak to use IAM authentication so I don't have to manage or rotate static database passwords.
- As a security auditor, I want to eliminate long-lived credentials from the infrastructure so we can demonstrate compliance with security policies.
- As a platform engineer, I want the Keycloak database connection to automatically authenticate using short-lived tokens so there's no risk of credential expiration.

### Acceptance Criteria
- [ ] AWS RDS IAM authentication is enabled on the Aurora MySQL cluster
- [ ] Keycloak ECS task generates short-lived IAM tokens for database authentication (not stored at rest)
- [ ] `keycloak_database_password` variable is removed from Terraform
- [ ] Secrets Manager secret for DB password is removed or contains only username
- [ ] ECS task execution role includes `rds-db:connect` permission
- [ ] Zero-downtime cutover: existing Keycloak sessions are preserved during migration
- [ ] Backward compatibility: existing Keycloak 25 deployment continues to work
- [ ] Configuration changes documented in terraform variables and helm charts

### Out of Scope
- Changing the database engine from Aurora MySQL to PostgreSQL in production
- Modifying any other service's database configuration
- Implementing database connection pooling optimizations
- Changes to the RDS instance size or scaling configuration

### Dependencies
- Requires AWS RDS IAM authentication support (available for Aurora MySQL/PostgreSQL)
- Requires ECS task role modification

### Related Issues
- #1303 (this issue)
- #1026 (previous DB credential rotation fix - informs the secrets Manager pattern)