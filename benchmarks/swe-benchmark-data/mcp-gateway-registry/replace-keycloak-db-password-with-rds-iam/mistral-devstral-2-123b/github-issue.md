# GitHub Issue: Replace Keycloak DB Static Password with RDS IAM Authentication

## Title
Replace Keycloak Database Static Password with RDS IAM Authentication

## Labels
- enhancement
- security
- infrastructure
- database

## Description

### Problem Statement
The current MCP Gateway Registry deployment uses static database credentials stored in Terraform variables (`keycloak_database_username` and `keycloak_database_password`) for Keycloak's Aurora MySQL database connection. These static credentials are stored in AWS Secrets Manager but still require manual rotation and are associated with the issue mentioned in the previous deployments.

### Proposed Solution
Implement RDS IAM Authentication to eliminate static database credentials entirely. This approach:
- Removes static username/password from Terraform configuration
- Replaces them with short-lived IAM authentication tokens
- Enhances security posture by eliminating long-lived credentials
- Provides automatic credential rotation tied to IAM roles
- Simplifies password management

### User Stories
- As a Security Engineer, I want database authentication to use IAM roles instead of static credentials so that we can reduce our attack surface and improve credential rotation
- As a DevOps Engineer, I want to eliminate manual database password rotation so that I can reduce operational overhead
- As a System Administrator, I want Keycloak to use temporary, scoped database credentials so that we can improve our compliance posture

### Acceptance Criteria
- [ ] RDS IAM Authentication is enabled on the Aurora MySQL cluster
- [ ] RDS Proxy is configured to support IAM authentication alongside existing password-based authentication (hybrid mode during transition)
- [ ] Keycloak ECS task IAM role has permissions to generate database authentication tokens
- [ ] Static database credentials (`keycloak_database_username` and `keycloak_database_password`) are removed from Terraform variables
- [ ] Keycloak ECS task configuration uses IAM authentication for database access
- [ ] Database connection testing verifies successful authentication without static credentials
- [ ] Backward compatibility is maintained during transition period
- [ ] Documentation is updated to show IAM authentication setup

### Out of Scope
- [ ] PostgreSQL support (this deployment uses Aurora MySQL)
- [ ] Rotating the initial static credentials used during the migration process
- [ ] Modifying the Keycloak internal authentication mechanisms
- [ ] Multiregion replication of IAM authentication setup
- [ ] External database client access patterns
- [ ] SMTS/Token Exchange service authentication changes

### Dependencies
- [ ] AWS Aurora MySQL Serverless v2 support for IAM authentication
- [ ] AWS RDS Proxy support for IAM authentication
- [ ] Keycloak 24+ compatibility with MySQL IAM authentication
- [ ] Existing MCP Gateway Registry infrastructure (VPC, SG, etc.)

### Related Issues
- Fixes #1303: Remove static database password from ECS configuration
- Related to #1026: Previous issue with password drift in Secrets Manager rotation
- Related to security hardening initiatives

## Security Impact

This change significantly improves the security posture by:
1. Eliminating long-lived static database credentials
2. Implementing automatic credential rotation via IAM
3. Providing fine-grained access control through IAM roles
4. Enabling temporary, scoped database access credentials
5. Reducing the blast radius of credential compromise