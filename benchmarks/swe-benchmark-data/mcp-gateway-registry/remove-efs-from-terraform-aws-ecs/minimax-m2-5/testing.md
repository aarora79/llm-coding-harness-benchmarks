# Testing Plan: Remove EFS from terraform-aws-ecs

*Created: 2026-06-12*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview

### Scope of Testing
This testing plan verifies that EFS resources are completely removed from the terraform-aws-ecs module and that the resulting infrastructure configuration is valid.

### Prerequisites
- Terraform 1.0+
- AWS credentials configured
- Access to terraform/aws-ecs directory

### Shared Variables
```bash
export TERRAFORM_DIR="/path/to/repo/terraform/aws-ecs"
export AWS_REGION="us-east-1"
```

## 1. Verify EFS References Removed

### 1.1 No EFS Module in Storage
```bash
cd "$TERRAFORM_DIR/modules/mcp-gateway"
test -f storage.tf && grep -q "module.*efs" storage.tf && echo "FAIL: EFS module exists" || echo "PASS: No EFS module"
```
**Expected:** "PASS: No EFS module"

### 1.2 No EFS Variables
```bash
cd "$TERRAFORM_DIR/modules/mcp-gateway"
grep -q "efs_throughput_mode\|efs_provisioned_throughput" variables.tf && echo "FAIL: EFS variables exist" || echo "PASS: No EFS variables"
```
**Expected:** "PASS: No EFS variables"

### 1.3 No EFS Outputs
```bash
# Module outputs
cd "$TERRAFORM_DIR/modules/mcp-gateway"
grep -q "output.*efs" outputs.tf && echo "FAIL: Module EFS outputs exist" || echo "PASS: No module EFS outputs"

# Root outputs
cd "$TERRAFORM_DIR"
grep -q "output.*efs" outputs.tf && echo "FAIL: Root EFS outputs exist" || echo "PASS: No root EFS outputs"
```
**Expected:** "PASS" for both

### 1.4 No EFS Volume Configs
```bash
cd "$TERRAFORM_DIR/modules/mcp-gateway"
grep -q "efs_volume_configuration" ecs-services.tf && echo "FAIL: EFS volume configs exist" || echo "PASS: No EFS volume configs"
```
**Expected:** "PASS: No EFS volume configs"

### 1.5 No EFS Security Group
```bash
cd "$TERRAFORM_DIR/modules/mcp-gateway"
grep -q "efs.*security" storage.tf && echo "FAIL: EFS security group exists" || echo "PASS: No EFS security group"
```
**Expected:** "PASS: No EFS security group"

---

## 2. Terraform Validation Tests

### 2.1 Terraform Init
```bash
cd "$TERRAFORM_DIR"
terraform init -backend=false 2>&1 | tail -5
```
**Expected:** "Terraform has been successfully initialized"

### 2.2 Terraform Validate
```bash
cd "$TERRAFORM_DIR"
terraform validate
```
**Expected:**
```
Success! The configuration is valid.
```

### 2.3 Terraform Format Check
```bash
cd "$TERRAFORM_DIR"
terraform fmt -check -recursive
```
**Expected:** No output (all files formatted correctly)

### 2.4 Terraform Plan - No EFS Resources
```bash
cd "$TERRAFORM_DIR"
terraform plan 2>&1 | grep -i "efs\|elastic file system"
```
**Expected:** No output (no EFS resources in plan)

### 2.5 Terraform Plan Summary
```bash
cd "$TERRAFORM_DIR"
terraform plan 2>&1 | grep -E "Plan:|^[[:space:]]*~|Resources:"
```
**Expected:**
- Plan shows only deletions (no additions)
- All EFS-related resources should be destroyed

---

## 3. File Structure Tests

### 3.1 Storage File Should Not Exist or Be Empty
```bash
cd "$TERRAFORM_DIR/modules/mcp-gateway"
if [ -f storage.tf ]; then
    lines=$(wc -l < storage.tf)
    if [ "$lines" -lt 10 ]; then
        echo "PASS: storage.tf is minimal/empty"
    else
        echo "WARN: storage.tf still has content ($lines lines)"
    fi
else
    echo "PASS: storage.tf deleted"
fi
```
**Expected:** "PASS"

### 3.2 Check All EFS References Gone
```bash
cd "$TERRAFORM_DIR"
grep -r "efs" --include="*.tf" . 2>/dev/null | grep -v "^#" | grep -v "federation" | grep -v "reference"
```
**Expected:** No output or only false positives (federation, reference)

---

## 4. Integration Tests

### 4.1 Module Dependency Check
Verify no modules depend on EFS outputs:
```bash
cd "$TERRAFORM_DIR"
grep -r "module.efs\|var.efs" --include="*.tf" . 2>/dev/null
```
**Expected:** No output

### 4.2 Task Definition References
Verify ECS task definitions don't reference EFS:
```bash
cd "$TERRAFORM_DIR/modules/mcp-gateway"
grep -A5 -B5 "volume" ecs-services.tf | grep -i "efs"
```
**Expected:** No output

### 4.3 Documentation Updates
```bash
# Check OPERATIONS.md
cd "$TERRAFORM_DIR"
grep -i "efs" OPERATIONS.md | head -5

# Check README.md
cd "$TERRAFORM_DIR/../.."
grep -i "efs" README.md | head -5
```
**Expected:** No EFS references in either file

---

## 5. Deployment Surface Tests

### 5.1 Terraform Output Values
After applying, verify outputs don't contain EFS:
```bash
cd "$TERRAFORM_DIR"
terraform output 2>/dev/null | grep -i efs
```
**Expected:** No output (EFS outputs removed)

### 5.2 Variable Definitions
Ensure no EFS-related variables remain:
```bash
cd "$TERRAFORM_DIR/modules/mcp-gateway"
grep "^variable" variables.tf | grep -i efs
```
**Expected:** No output

---

## 6. Pre-Deployment Checklist

Before applying to any environment, complete this checklist:

- [ ] All Section 1 tests pass (EFS references removed)
- [ ] Section 2 tests pass (Terraform validates)
- [ ] Section 3 tests pass (file structure correct)
- [ ] Section 4 tests pass (no broken dependencies)
- [ ] Plan shows only deletions (no new EFS)
- [ ] Backup of current Terraform state exists
- [ ] Rollback plan documented
- [ ] Communication to stakeholders sent

---

## 7. Post-Deployment Verification

After applying Terraform:

```bash
# In AWS Console or CLI:
aws efs describe-file-systems --region $AWS_REGION 2>/dev/null | grep -i mcp-gateway
# Should show nothing (or at least no new EFS)

# Verify ECS services are running
aws ecs describe-services --cluster mcp-gateway --services auth registry --region $AWS_REGION | jq '.services[] .runningCount'
# Should show task counts > 0
```

---

## 8. Test Execution Summary

### Command to Run All Verification Tests
```bash
#!/bin/bash
set -e

TERRAFORM_DIR="$1"
if [ -z "$TERRAFORM_DIR" ]; then
    echo "Usage: $0 <path-to-terraform-dir>"
    exit 1
fi

cd "$TERRAFORM_DIR"

echo "=== Running EFS Removal Verification ==="

echo -n "1.1 No EFS module: "
if grep -q "module.*efs" modules/mcp-gateway/storage.tf 2>/dev/null; then
    echo "FAIL"
else
    echo "PASS"
fi

echo -n "1.2 No EFS variables: "
if grep -q "efs_throughput_mode" modules/mcp-gateway/variables.tf; then
    echo "FAIL"
else
    echo "PASS"
fi

echo -n "1.3 No EFS outputs: "
if grep -q "output.*efs" modules/mcp-gateway/outputs.tf; then
    echo "FAIL"
else
    echo "PASS"
fi

echo -n "1.4 No EFS volume configs: "
if grep -q "efs_volume_configuration" modules/mcp-gateway/ecs-services.tf; then
    echo "FAIL"
else
    echo "PASS"
fi

echo "=== All verification complete ==="
```

---

## Notes

- All tests are safe to run locally (read-only for validation)
- Actual `terraform apply` requires AWS credentials and manual approval
- Consider running in a non-production environment first
- Monitor application logs after deployment for any storage-related errors