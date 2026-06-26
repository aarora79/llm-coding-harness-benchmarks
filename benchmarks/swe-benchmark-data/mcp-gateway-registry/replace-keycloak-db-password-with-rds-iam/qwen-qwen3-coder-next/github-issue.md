# GitHub Issue: Replace Keycloak Database Password with RDS IAM Authentication

## Title
Replace Keycloak Database Password with RDS IAM Authentication

## Labels
- enhancement
- security
- infrastructure

## Description

### Problem Statement
Keycloak currently uses static database credentials stored in AWS Secrets Manager for connecting to its Aurora MySQL database. This approach has several security drawbacks:
1. Static passwords increase the attack surface if leaked
2. Password rotation requires manual intervention or separate automation
3. credentials are stored in Secrets Manager as plaintext (though encrypted at rest)

The goal is to migrate to RDS IAM database authentication, which provides short-lived, automatically-rotating credentials that eliminate the need to manage database passwords.

### Proposed Solution
1. Configure Aurora MySQL cluster to support IAM database authentication
2. Create IAM role for Keycloak ECS task that allows RDS IAM auth
3. Modify Keycloak ECS task to generate temporary IAM auth tokens using AWS SDK
4. Remove static database password from Secrets Manager
5. Update Terraform configuration to use IAM auth instead of password-based auth
6. Update docker-compose and Helm charts to support IAM authentication mode

### User Stories
- As a security engineer, I want Keycloak to use IAM database authentication so that static database passwords are not stored or transmitted
- As an SRE, I want database authentication to be automatically rotated so I don't need to manage password rotation manually
- As a DevOps engineer, I want the deployment configuration to support both IAM and password-based authentication for flexible deployment scenarios

### Acceptance Criteria
- [ ] Keycloak can connect to Aurora MySQL using IAM database authentication tokens
- [ ] Static `keycloak_database_password` variable is removed from Terraform
- [ ] Secrets Manager secret `keycloak/database` no longer contains a password field
- [ ] ECS task IAM role has `rds-db:connect` permission with proper conditions
- [ ] Keycloak container generates IAM auth tokens using AWS SDK at startup
- [ ] Existing password-based auth is disabled or deprecated
- [ ] Terraform plan shows no secret password references in cloud resources
- [ ] Docker Compose configuration supports IAM auth mode
- [ ] Helm chart supports IAM auth configuration

### Out of Scope
- Changing the database engine (stays with Aurora MySQL)
- Migrating to a different authentication provider for Keycloak itself
- Modifying Keycloak application code beyond startup configuration
- Updating the RDS proxy configuration (will continue to use password auth)

### Dependencies
- Reference: Issue #1303

### Related Issues
- Issue #1303 (referenced)
