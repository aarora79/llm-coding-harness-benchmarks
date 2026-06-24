# GitHub Issue: Remove EFS from Terraform AWS ECS Deployment

## Title
Remove EFS Storage from MCP Gateway Registry AWS ECS Deployment

## Labels
- enhancement
- infrastructure
- terraform
- breaking-change
- performance

## Description

### Problem Statement
The current MCP Gateway Registry deployment uses Amazon EFS (Elastic File System) for persistent storage across multiple ECS services (auth-server, mcpgw). While EFS provides shared file system access, it introduces several issues:

1. **Performance Overhead**: EFS adds latency compared to local ephemeral storage, especially for high-frequency write operations
2. **Cost**: EFS storage and operations add ongoing costs
3. **Complexity**: EFS setup requires additional networking configuration, security groups, and access points
4. **Maintenance**: EFS volumes require management and monitoring
5. **Failure Risk**: Network dependencies create additional points of failure

The registry service has already been migrated to use DocumentDB for persistence and CloudWatch for logs (evident by commented code in ecs-services.tf line 1367: "EFS volumes removed"). This work was partially completed - we need to consolidate this approach across all services.

### Proposed Solution
Remove all EFS dependencies from the Terraform AWS ECS deployment:

1. **Complete EFS Removal**: Remove EFS file system, access points, and security groups from the Terraform configuration
2. **Service Migration**: Migrate remaining services (auth-server, mcpgw) to use appropriate storage alternatives:
   - **Auth Server**: Store configuration in Secrets Manager / SSM Parameter Store instead of EFS-mounted files
   - **MC Gateway**: Use ephemeral storage or S3 for temporary files
3. **Update Deployment Workflows**: Modify post-deployment scripts that rely on EFS initialization
4. **Clean Up References**: Remove EFS outputs, variables, and documentation

### User Stories
- As a DevOps engineer, I want to reduce deployment complexity so that I can manage infrastructure more efficiently
- As a cost-conscious organization, I want to eliminate EFS-related expenses so that I can reduce my AWS bill
- As a security engineer, I want to minimize network dependencies so that I can reduce the attack surface
- As an application developer, I want simpler storage patterns so that I can focus on application logic

### Acceptance Criteria
- [ ] All EFS-related Terraform resources removed from `terraform/aws-ecs/modules/mcp-gateway/storage.tf`
- [ ] All EFS volume mounts removed from ECS service definitions in `terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf`
- [ ] EFS-related Terraform variables removed from `terraform/aws-ecs/modules/mcp-gateway/variables.tf`
- [ ] EFS outputs removed from `terraform/aws-ecs/modules/mcp-gateway/outputs.tf`
- [ ] Post-deployment scripts updated to remove EFS initialization steps
- [ ] Terraform successfully applies without EFS-related errors
- [ ] All ECS services start successfully without EFS dependencies
- [ ] Registry, Auth Server, and MC Gateway functionality verified
- [ ] Updated README documentation reflects storage changes
- [ ] Migration guide provided for existing users

### Out of Scope
- Changing storage backend for production instances without proper planning
- Migrating existing EFS data automatically (manual migration required)
- Changing DocumentDB usage (already in place for registry persistence)
- Modifying other AWS services beyond EFS removal
- Updating pre-existing Helm chart EFS support (focus only on Terraform ECS deployment)

### Dependencies
- Terraform 1.x+ with AWS provider
- AWS CLI configured with appropriate permissions
- Existing DocumentDB/S3 infrastructure for data migration
- Migrated configuration storage patterns already implemented in services

### Related Issues
- Issue #305: Registry EFS to DocumentDB migration (partially completed)
- Issue #217: Auth-server configuration management improvements
- Issue #158: Infrastructure cost optimization planning
