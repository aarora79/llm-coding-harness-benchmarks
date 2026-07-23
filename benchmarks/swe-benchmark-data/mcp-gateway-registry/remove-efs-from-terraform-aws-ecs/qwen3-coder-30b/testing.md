# Testing Plan: Remove EFS from terraform/aws-ecs/

*Created: 2026-07-22*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview
### Scope of Testing
This testing plan ensures that EFS resources have been completely removed from the terraform/aws-ecs/ deployment and that no ECS services depend on EFS volumes. The tests verify both the infrastructure changes and the configuration updates.

### Prerequisites
- [ ] Terraform AWS provider configured
- [ ] Access to the terraform/aws-ecs/ directory
- [ ] Appropriate AWS credentials with necessary permissions

### Shared Variables
```bash
export TF_VAR_region="us-west-2"
export TF_VAR_environment="production"
```

## 1. Functional Tests
### 1.1 Terraform Validation Tests
**Test 1: Validate Terraform Configuration**
```bash
cd terraform/aws-ecs/
terraform init
terraform validate
```
**Expected Status:** Exit code 0
**Expected Response:** Success message indicating valid configuration
**Assertions:**
- No errors in terraform validation
- All variables properly defined
- No EFS-related resources found in validation

**Negative Case:** 
If terraform validate fails, it should indicate missing variables or invalid syntax.

### 1.2 Terraform Plan Tests
**Test 2: Run Terraform Plan**
```bash
cd terraform/aws-ecs/
terraform plan
```
**Expected Status:** Exit code 0
**Expected Response:** Plan output showing no EFS resource changes
**Assertions:**
- No EFS resources to create, update, or destroy
- All ECS resources are properly defined
- No EFS-related variables are referenced

**Negative Case:**
If terraform plan shows EFS resources to be created/destroyed, the removal is incomplete.

## 2. Backwards Compatibility Tests
**Test 3: Verify Variable Compatibility**
```bash
cd terraform/aws-ecs/
# Check that EFS variables are no longer defined
grep -r "efs_" variables.tf
grep -r "efs_" terraform.tfvars.example
```
**Expected Status:** Exit code 1 (no matches found)
**Expected Response:** No output (as no EFS variables should exist)
**Assertions:**
- EFS variables are removed from variables.tf
- EFS variables are removed from terraform.tfvars.example
- No EFS-related configurations remain

**Negative Case:**
If EFS variables are still found, backward compatibility is broken.

## 3. UX Tests
**Test 4: Check Configuration File Content**
```bash
cd terraform/aws-ecs/
# Verify EFS-related content is removed from main configuration
grep -r "aws_efs" *.tf
grep -r "volume.*efs" *.tf
```
**Expected Status:** Exit code 1 (no matches found)
**Expected Response:** No output (as EFS resources should be removed)
**Assertions:**
- No EFS resource definitions exist in main.tf
- No EFS volume mounts exist in task definitions
- All EFS-related references are eliminated

**Negative Case:**
If EFS references are still found, the removal is incomplete.

## 4. Deployment Surface Tests
### 4.1 Terraform / ECS Wiring
**Test 5: Validate Module Parameters**
```bash
cd terraform/aws-ecs/
# Check that EFS parameters are no longer passed to modules
grep -r "efs_enabled\|efs_file_system_id" *.tf
```
**Expected Status:** Exit code 1 (no matches found)
**Expected Response:** No output
**Assertions:**
- EFS parameters are removed from module calls
- No EFS-related module parameters exist

**Negative Case:**
If EFS module parameters are still found, the module wiring is incomplete.

### 4.2 Deploy and Verify
**Test 6: Full Deployment Verification**
```bash
cd terraform/aws-ecs/
# Run a complete validation sequence
terraform init -reconfigure
terraform validate
terraform plan -out=tfplan
```
**Expected Status:** Exit code 0
**Expected Response:** Success messages for all steps
**Assertions:**
- All terraform operations complete successfully
- No EFS-related resources are shown in the plan
- Deployment configuration is clean and valid

**Negative Case:**
If any step fails, the deployment surface is not properly cleaned.

## 5. End-to-End API Tests
**Test 7: Service Dependency Verification**
```bash
cd terraform/aws-ecs/
# Check that ECS task definitions don't reference EFS volumes
find . -name "*.tf" -exec grep -l "volume" {} \; | xargs grep -n "efs"
```
**Expected Status:** Exit code 1 (no matches found)
**Expected Response:** No output
**Assertions:**
- No ECS task definitions reference EFS volumes
- All volume configurations are properly updated
- No EFS-specific volume configurations remain

**Negative Case:**
If EFS volume references are found, services may still depend on EFS.

## 6. Test Execution Checklist
- [ ] Section 1 (Functional) passes
- [ ] Section 2 (Backwards Compat) verified or marked Not Applicable
- [ ] Section 3 (UX) verified or marked Not Applicable
- [ ] Section 4 (Deployment) verified or marked Not Applicable
- [ ] Section 5 (E2E) verified or marked Not Applicable
- [ ] Unit tests added under `tests/unit/`
- [ ] Integration tests added under `tests/integration/`
- [ ] `uv run pytest tests/` passes with no regressions