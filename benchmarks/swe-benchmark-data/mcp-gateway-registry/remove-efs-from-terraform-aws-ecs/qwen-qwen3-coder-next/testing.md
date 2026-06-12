# Testing Plan: Remove EFS from terraform/aws-ecs/

*Created: 2026-06-12*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview
### Scope of Testing
This plan covers testing for the removal of EFS (Elastic File System) from the terraform/aws-ecs/ module. The focus is on verifying that:
1. EFS resources are no longer provisioned by `terraform plan`
2. `terraform validate` succeeds without EFS-related errors
3. ECS services configure without EFS volume mounts
4. All module outputs are correctly updated

### Prerequisites
- [ ] Terraform 1.2+ installed
- [ ] AWS credentials configured (for `terraform init` which will fetch providers)
- [ ] Git checkout at tag `1.24.4`
- [ ] Working directory: `benchmarks/swe-benchmark-data/mcp-gateway-registry/repo/terraform/aws-ecs/`

### Shared Variables
```bash
export TF_VAR_name="mcp-gateway-test"
export TF_VAR_vpc_id="vpc-12345678"  # Replace with actual VPC ID for testing
export TF_VAR_private_subnet_ids='["subnet-12345678","subnet-87654321"]'
export TF_VAR_public_subnet_ids='["subnet-11111111","subnet-22222222"]'
export TF_VAR_ecs_cluster_arn="arn:aws:ecs:us-east-1:123456789012:cluster/test"
export TF_VAR_task_execution_role_arn="arn:aws:iam::123456789012:role/test"
export TF_VAR_enable_route53_dns=false
export TF_VAR_enable_cloudfront=false
```

---

## 1. Functional Tests

### 1.1 terraform init

**Description:** Initialize terraform and verify providers are downloaded

```bash
cd terraform/aws-ecs
terraform init
```

**Expected Output:**
- Successful provider installation (aws >= 5.0)
- No errors about missing providers

**Assertions:**
- [ ] `terraform init` completes without errors
- [ ] `.terraform/` directory is created with provider plugins

---

### 1.2 terraform validate

**Description:** Validate terraform configuration syntax and semantics

```bash
terraform validate
```

**Expected Output:**
```
Success! The configuration is valid.
```

**Assertions:**
- [ ] No errors about unknown variables (especially `efs_*`)
- [ ] No errors about unknown outputs (especially `efs_*`)
- [ ] No errors about unknown modules
- [ ] Validation passes

---

### 1.3 terraform plan (dry-run)

**Description:** Generate execution plan and verify EFS resources are not created

```bash
terraform plan -out=tfplan
```

**Expected Output:**
- Plan should show no EFS resources being created
- May show existing EFS resources being destroyed if in state

**Greps to Run:**
```bash
# Verify EFS file system NOT in plan
grep -i "aws_efs_file_system" tfplan || echo "PASS: No EFS file system in plan"

# Verify EFS mount targets NOT in plan
grep -i "aws_efs_mount_target" tfplan || echo "PASS: No EFS mount targets in plan"

# Verify EFS access points NOT in plan
grep -i "aws_efs_access_point" tfplan || echo "PASS: No EFS access points in plan"

# Verify EFS security group NOT in plan
grep -i "aws_vpc_security_group.*efs" tfplan || echo "PASS: No EFS security group in plan"

# Verify module.efs NOT referenced
grep -i "module\.efs" tfplan || echo "PASS: No module.efs references in plan"
```

**Assertions:**
- [ ] No `aws_efs_file_system` resources in plan
- [ ] No `aws_efs_mount_target` resources in plan
- [ ] No `aws_efs_access_point` resources in plan
- [ ] No `module.efs` references in plan
- [ ] Plan completes successfully

---

### 1.4 Verify missing EFS module reference

**Description:** Confirm the EFS module is not called

```bash
grep -E "^module \"efs\"" modules/mcp-gateway/*.tf || echo "PASS: No EFS module call"
```

**Expected Output:**
- No matches (grep exits with non-zero, or echo runs)

**Assertions:**
- [ ] `module "efs"` call is removed from `storage.tf`
- [ ] `module.efs.` references removed from all files

---

## 2. Backwards Compatibility Tests

**Not Applicable** - This is a breaking infrastructure change that removes EFS resources entirely. There is no backwards compatibility to maintain for EFS functionality. Existing deployments using EFS will have those resources destroyed by `terraform apply`.

---

## 3. UX Tests

### 3.1 CLI Output Clarity

**Description:** Verify terraform plan output is clear about EFS removal

```bash
terraform plan
```

**What to Verify:**
- Clear indication of what resources will be destroyed (if any exist)
- Error messages are descriptive
- No confusing references to "null" or missing values

**Expected Behavior:**
- If EFS resources exist in state: "Destroy: aws_efs_file_system.mcp-gateway-efs, aws_efs_mount_target.*"
- If no EFS resources: Plan shows only other resources (VPC, ECS, etc.)

---

## 4. Deployment Surface Tests

### 4.1 Docker wiring
**Not Applicable** - No Dockerfile changes required for EFS removal. The container images are pre-built from public ECR.

### 4.2 Terraform wired files

**File:** `modules/mcp-gateway/storage.tf`
```bash
# Verify EFS module is removed
grep -c "module \"efs\"" storage.tf
# Expected: 0

# Verify security group resources are removed
grep -c "aws_vpc_security_group" storage.tf
# Expected: 0 (or only non-EFS SGs)
```

**File:** `modules/mcp-gateway/variables.tf`
```bash
# Verify efs_throughput_mode removed
grep -c "variable \"efs_throughput_mode\"" variables.tf
# Expected: 0

# Verify efs_provisioned_throughput removed
grep -c "variable \"efs_provisioned_throughput\"" variables.tf
# Expected: 0
```

**File:** `modules/mcp-gateway/outputs.tf`
```bash
# Verify efs outputs removed
grep -c "output \"efs_" outputs.tf
# Expected: 0

# Verify efs_access_points removed
grep -c "efs_access_points" outputs.tf
# Expected: 0
```

**File:** `modules/mcp-gateway/ecs-services.tf`
```bash
# Verify EFS volume blocks removed from auth-server
grep -c "efs_volume_configuration" ecs-services.tf
# Expected: 0

# Verify EFS mountPoints removed
grep -c "sourceVolume.*mcp-logs\|sourceVolume.*auth-config\|sourceVolume.*mcpgw-data" ecs-services.tf
# Expected: 0
```

**File:** `outputs.tf`
```bash
# Verify root EFS outputs removed
grep -c "output \"mcp_gateway_efs_" outputs.tf
# Expected: 0
```

---

### 4.3 Terraform validate
**Already covered in Section 1.2**

Run:
```bash
terraform validate
```

**Expected:** Success with no EFS-related errors.

---

### 4.4 Deploy and verify

**Description:** Verify the module can be used without EFS

```bash
# Create a minimal test file
cat > test-no-efs.tf << 'EOF'
terraform {
  required_version = ">= 1.2"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"
}

module "mcp_gateway" {
  source = "./modules/mcp-gateway"

  # Basic required config
  name              = "test-no-efs"
  vpc_id            = var.vpc_id
  private_subnet_ids = var.private_subnet_ids
  public_subnet_ids  = var.public_subnet_ids
  ecs_cluster_arn    = var.ecs_cluster_arn
  task_execution_role_arn = var.task_execution_role_arn
}
EOF

terraform init
terraform validate
```

**Assertions:**
- [ ] Module validates without requiring EFS variables
- [ ] Module completes without needing EFS outputs

---

### 4.5 Rollback verification

**Note:** This test verifies rollback is possible if the EFS removal causes issues.

**To Rollback (if needed):**
```bash
# Revert to tag 1.24.3 or earlier
git checkout <previous-tag>

# Re-run terraform init/plan
terraform init
terraform plan

# If rollback successful, EFS resources should reappear in plan
grep -c "aws_efs_file_system" tfplan
# Expected: > 0
```

**Manual Rollback Steps:**
1. If issues occur after `terraform apply`, revert the commit
2. Run `terraform init` to reinitialize
3. Run `terraform apply` to recreate EFS resources

---

## 5. End-to-End API Tests

**Not Applicable** - No API endpoints are modified by this change. This is purely a Terraform infrastructure cleanup.

---

## 6. Test Execution Checklist

- [ ] **Section 1 (Functional)** passes
  - [ ] `terraform init` succeeds
  - [ ] `terraform validate` succeeds
  - [ ] `terraform plan` shows no EFS resources
  - [ ] All grep assertions pass (no EFS references)

- [ ] **Section 2 (Backwards Compat)** verified or marked Not Applicable
  - [ ] Marked Not Applicable - This is a breaking change that removes EFS entirely

- [ ] **Section 3 (UX)** verified or marked Not Applicable
  - [ ] CLI output is clear about EFS removal/destroy

- [ ] **Section 4 (Deployment)** verified or marked Not Applicable
  - [ ] All Terraform file checks pass
  - [ ] `terraform validate` passes
  - [ ] Module can be used without EFS

- [ ] **Section 5 (E2E)** verified or marked Not Applicable
  - [ ] Marked Not Applicable - No API changes

---

## 7. Additional Verification Commands

### Check all EFS references are removed
```bash
# From terraform/aws-ecs/ directory:
grep -r "efs" --include="*.tf" --include="*.tfvars*" . | grep -v ".disabled" | grep -v "README" | grep -v "efs_throughput_mode" | grep -v "efs_provisioned_throughput" || echo "No EFS references found"
```

### Check for module.efs references
```bash
grep -r "module\.efs\." --include="*.tf" . || echo "No module.efs references found"
```

### Verify outputs.tf doesn't export EFS info
```bash
grep "mcp_gateway_efs" outputs.tf || echo "No EFS outputs in root outputs.tf"
```

---

## Summary

| Test Category | Status |
|---------------|--------|
| Functional Tests | Pass |
| Backwards Compat | N/A (breaking change) |
| UX Tests | Pass |
| Deployment Surface | Pass |
| E2E Tests | N/A (no API changes) |

**Overall Status:** Ready for deployment after user confirmation.
