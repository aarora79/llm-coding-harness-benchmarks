# GitHub Issue: Remove EFS from terraform/aws-ecs/

## Labels
- enhancement
- infrastructure
- terraform

## Description

### Problem Statement
EFS is no longer needed in this deployment as the application uses S3/DocumentDB for all persistent storage. EFS adds unnecessary cost and complexity to the infrastructure.

### Proposed Solution
Remove all EFS resources from the terraform/aws-ecs/ directory including:
- EFS file system
- EFS mount targets
- EFS security groups
- Any task-definition volume mounts that reference EFS
- Update variables.tf, terraform.tfvars.example, and module wiring

### User Stories
- As an operator deploying via Terraform, I want to remove obsolete EFS resources so that I reduce infrastructure costs and complexity
- As a DevOps engineer, I want to ensure no service depends on the EFS mount so that I can safely remove it

### Acceptance Criteria
- [ ] All EFS resources are removed from terraform/aws-ecs/
- [ ] No ECS task definitions reference EFS volumes
- [ ] variables.tf is updated to remove EFS-related variables
- [ ] terraform.tfvars.example is updated to remove EFS-related variables
- [ ] terraform validate and terraform plan still succeed
- [ ] No service depends on EFS mount

### Out of Scope
- Changes to application code or data persistence mechanisms
- Changes to other infrastructure components besides EFS in terraform/aws-ecs/

### Dependencies
- None

### Related Issues
- #1286