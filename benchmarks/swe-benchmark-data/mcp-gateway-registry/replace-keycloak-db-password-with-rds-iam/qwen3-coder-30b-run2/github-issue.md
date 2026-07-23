# GitHub Issue: Replace Keycloak Database Password with RDS IAM Authentication

## Labels
- enhancement
- security
- infrastructure
- aws

## Description

### Problem Statement
The Keycloak service currently uses static database credentials (username/password) to connect to an Aurora MySQL cluster. This approach has security implications as static passwords are harder to rotate and manage securely. We need to migrate to RDS IAM authentication which provides short-lived credentials and better security practices.

### Proposed Solution
Replace the Keycloak database password with RDS IAM authentication by:
1. Enabling IAM database authentication on the Aurora MySQL cluster
2. Removing static DB credentials from Terraform and ECS configurations
3. Updating the Keycloak ECS task to generate short-lived IAM auth tokens via `rds:GenerateDBAuthToken`
4. Updating IAM roles/policies to support the new authentication method
5. Maintaining backwards compatibility with password auth as a feature-flagged fallback

### User Stories
- As an operator deploying on AWS ECS + RDS, I want to use secure IAM authentication instead of static passwords so that my database connections are more secure
- As a security administrator, I want to reduce the attack surface by eliminating static database credentials so that database access is more secure
- As a system administrator, I want to maintain backwards compatibility with password authentication so that deployments can gradually transition

### Acceptance Criteria
- [ ] Keycloak can connect to Aurora MySQL using IAM authentication
- [ ] Static database password is removed from configuration
- [ ] IAM database authentication is enabled on the Aurora cluster
- [ ] Keycloak ECS task generates short-lived IAM auth tokens via `rds:GenerateDBAuthToken`
- [ ] IAM roles/policies are updated to support IAM authentication
- [ ] Password authentication remains available as a feature-flagged fallback
- [ ] No Keycloak version change is required
- [ ] No deadline for the migration

### Out of Scope
- Keycloak version upgrade
- Changes to other services beyond Keycloak
- Changes to Helm/EKS deployment patterns
- Migration of existing databases or data

### Dependencies
- AWS Aurora MySQL cluster with IAM database authentication support
- Proper IAM policies and roles for Keycloak ECS task
- Updated Keycloak ECS task configuration

### Related Issues
- #1303