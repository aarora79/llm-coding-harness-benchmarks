# GitHub Issue: Replace Keycloak Database Password with RDS IAM Authentication

## Labels
- enhancement
- security
- infrastructure
- keycloak

## Description

### Problem Statement
The MCP Gateway Registry currently uses static database credentials for Keycloak's Aurora MySQL cluster. This approach has security implications as passwords are stored in plaintext and must be rotated manually. We need to transition to RDS IAM database authentication which provides better security through short-lived tokens and eliminates the need to manage static passwords.

### Proposed Solution
Replace the Keycloak database password with RDS IAM authentication by:
1. Enabling IAM database authentication on the Aurora MySQL cluster
2. Removing static DB credentials from Terraform variables and ECS configuration
3. Updating the Keycloak ECS task to generate short-lived IAM auth tokens via `rds:GenerateDBAuthToken`
4. Updating IAM roles/policies to support the new authentication method
5. Maintaining backwards compatibility with password auth as a feature-flagged fallback

### User Stories
- As an operator deploying on AWS ECS + RDS, I want to use RDS IAM authentication for Keycloak so that I don't need to manage static database passwords
- As a security engineer, I want to eliminate static database credentials so that the system is more secure and follows security best practices
- As a system administrator, I want to maintain backwards compatibility so that existing deployments can continue to work during the transition

### Acceptance Criteria
- [ ] IAM database authentication is enabled on the Aurora MySQL cluster
- [ ] Static database credentials are removed from Terraform variables and ECS configuration
- [ ] Keycloak ECS task can generate short-lived IAM auth tokens via `rds:GenerateDBAuthToken`
- [ ] IAM roles/policies are updated to support the new authentication method
- [ ] Password authentication remains available as a feature-flagged fallback
- [ ] Backwards compatibility is maintained during the transition period

### Out of Scope
- Keycloak version upgrades
- Changes to Keycloak application configuration beyond database connection
- Changes to other services in the deployment
- Migration of existing databases or data

### Dependencies
- AWS RDS IAM authentication support for Aurora MySQL
- Keycloak version that supports IAM authentication

### Related Issues
- #1303