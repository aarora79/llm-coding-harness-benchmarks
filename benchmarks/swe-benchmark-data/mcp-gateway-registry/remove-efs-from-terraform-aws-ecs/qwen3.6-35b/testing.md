# Testing Plan: Remove EFS from terraform/aws-ecs

*Created: 2026-07-06*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview

### Scope of Testing
Verify that all EFS resources, variables, outputs, and references are removed from the `terraform/aws-ecs/` directory without breaking `terraform validate` or `terraform plan`. This covers 3 `.tf` resource files, 2 `.tf` variable/output files, 2 root output/README files, and 2 shell scripts.

### Prerequisites
- [ ] Repository cloned at tag `1.24.4`
- [ ] `terraform` CLI installed (any version >= 1.0)
- [ ] Working directory is the repo root

### Shared Variables
```bash
export TF_DIR="terraform/aws-ecs"
export REPO_ROOT="$(git rev-parse --show-toplevel)"
```

## 1. Functional Tests

### 1.1 Terraform Validate

**Command:**
```bash
cd "$REPO_ROOT/$TF_DIR"
terraform validate
```

**Expected Result:**
- Exit code 0
- Output: "Success! The configuration is valid."
- No errors or warnings about missing references

**Assertion:**
The exit code must be 0. If it is non-zero, there are stale references to `module.efs` or the removed variables in some other `.tf` file.

### 1.2 Terraform Plan - No EFS Resources

**Command:**
```bash
cd "$REPO_ROOT/$TF_DIR"
terraform plan -input=false -detailed-exitcode \
  -var "name=test-efs-removal" \
  -var "aws_region=us-east-1" \
  -var "keycloak_admin_password=test123" \
  -var "keycloak_database_password=test123" \
  -var "documentdb_admin_password=test123" \
  -var "storage_backend=file"
```

**Expected Result:**
- Exit code 0 (no changes to existing state) or 2 (changes to destroy EFS)
- The plan output must NOT contain any of:
  - `aws_efs_file_system`
  - `aws_efs_mount_target`
  - `aws_efs_access_point`
  - `aws_vpc_security_group_rule` with "efs" in the description
  - `aws_vpc_security_group_egress_rule` with "efs" in the name
  - `module.efs`

**Negative Case:**
If the plan shows `aws_efs_file_system` resources, some EFS reference was missed in the `.tf` files.

### 1.3 Grep Verification - No EFS References in .tf Files

**Command:**
```bash
grep -rn "aws_efs\|efs_volume_configuration\|module\.efs\|var\.efs_\|mount.*efs\|efs_id\|efs_arn\|efs_access" \
  --include="*.tf" "$REPO_ROOT/$TF_DIR/" || echo "PASS: No EFS references found"
```

**Expected Result:**
- Zero matches from `grep`
- Exit code 1 from grep (no matches) is acceptable

**Negative Case:**
If grep finds any match, that file still references EFS and was missed in the removal.

### 1.4 Grep Verification - No EFS References in README

**Command:**
```bash
grep -n "elasticfilesystem" "$REPO_ROOT/$TF_DIR/README.md" || echo "PASS: No elasticfilesystem references"
```

**Expected Result:**
- Zero matches

### 1.5 Verify Removed Variables No Longer Exist

**Command:**
```bash
grep -n "efs_throughput_mode\|efs_provisioned_throughput" \
  "$REPO_ROOT/$TF_DIR/modules/mcp-gateway/variables.tf" || echo "PASS: EFS variables removed"
```

**Expected Result:**
- Zero matches in the module variables file

### 1.6 Verify Removed Outputs No Longer Exist

**Command:**
```bash
# Module outputs
grep -n 'output "efs_' \
  "$REPO_ROOT/$TF_DIR/modules/mcp-gateway/outputs.tf" || echo "PASS: Module EFS outputs removed"

# Root outputs
grep -n 'output "mcp_gateway_efs_' \
  "$REPO_ROOT/$TF_DIR/outputs.tf" || echo "PASS: Root EFS outputs removed"
```

**Expected Result:**
- Zero matches in both files

### 1.7 Verify storage.tf is Deleted

**Command:**
```bash
test -f "$REPO_ROOT/$TF_DIR/modules/mcp-gateway/storage.tf" && echo "FAIL: storage.tf still exists" || echo "PASS: storage.tf removed"
```

**Expected Result:**
- File does not exist

## 2. Backwards Compatibility Tests

### 2.1 Existing Deployments Without EFS Variables

**Scenario:** A deployment that does not set `efs_throughput_mode` or `efs_provisioned_throughput` (most deployments, since these had defaults).

**Command:**
```bash
cd "$REPO_ROOT/$TF_DIR"
terraform plan -input=false -detailed-exitcode \
  -var "storage_backend=file" \
  -var "keycloak_admin_password=test123" \
  -var "keycloak_database_password=test123" \
  -var "documentdb_admin_password=test123"
```

**Expected Result:**
- Exit code 0 (no planned changes) or 2 (EFS resources to destroy)
- No error about missing variable values

**Justification:** The variables had defaults (`bursting`, `100`) and most deployments did not explicitly set them. Removing them should not cause validation errors for deployments that didn't override them.

### 2.2 Terraform State Compatibility

**Scenario:** A deployment with existing EFS resources in Terraform state.

**Command:**
```bash
cd "$REPO_ROOT/$TF_DIR"
terraform plan -input=false
```

**Expected Result:**
- Plan shows the EFS resources being **destroyed**:
  - `~ aws_efs_file_system.mcp_gateway_efs` -> `destroy`
  - `~ aws_efs_mount_target.*` -> `destroy`
  - `~ aws_efs_access_point.*` -> `destroy`
  - `~ aws_vpc_security_group.*` (EFS SG) -> `destroy`
  - `~ aws_vpc_security_group_egress_rule.efs_all_outbound` -> `destroy`
- No other unexpected resources are modified or added

**Justification:** Terraform reconciles state by matching resource addresses. Removing the `.tf` definitions means Terraform will plan to destroy the orphaned resources.

## 3. UX Tests

### 3.1 terraform plan Output Clarity

**Scenario:** An operator reviews `terraform plan` output and confirms no EFS resources are present.

**Command:**
```bash
cd "$REPO_ROOT/$TF_DIR"
terraform plan -input=false -var "storage_backend=file" -var "keycloak_admin_password=test123" -var "keycloak_database_password=test123" -var "documentdb_admin_password=test123" 2>&1 | grep -i "efs" && echo "FAIL: EFS still visible" || echo "PASS: No EFS in plan"
```

**Expected Result:**
- The word "EFS" or "efs" does not appear in the plan output (except possibly in the resource destruction section for resources being removed)

**Post-deployment note:** After `terraform apply`, the plan should show zero EFS references in both "Resources to add", "Resources to change", and "Resources to destroy" sections.

### 3.2 README Documentation Accuracy

**Scenario:** A new platform engineer reads the IAM permissions section in README.md and does not see `elasticfilesystem:*`.

**Command:**
```bash
grep -c "elasticfilesystem" "$REPO_ROOT/$TF_DIR/README.md"
```

**Expected Result:**
- Exit code 1 (zero matches from grep, meaning the permission was removed)

## 4. Deployment Surface Tests

### 4.1 Terraform Wiring

**Check:** All `.tf` files in `terraform/aws-ecs/` and its subdirectories parse without errors.

**Command:**
```bash
cd "$REPO_ROOT/$TF_DIR"
# Verify no stray references
for f in $(find . -name "*.tf"); do
  terraform fmt -check "$f" || echo "FORMAT ISSUE: $f"
done
```

**Expected Result:**
- All files pass `terraform fmt -check` (or are auto-fixed by `terraform fmt`)
- No syntax errors

### 4.2 Helm / EKS Wiring

**Verdict:** **Not Applicable** - This change is scoped to `terraform/aws-ecs/` only. Helm charts under `charts/` are out of scope per the issue definition. A follow-up issue should be filed for Helm chart cleanup.

### 4.3 Docker Compose Wiring

**Verdict:** **Not Applicable** - Docker Compose files under `docker/` are out of scope per the issue definition. A follow-up issue should be filed for Docker Compose cleanup.

### 4.4 Deploy and Verify (Dry Run)

**Scenario:** Full dry-run plan to verify no unexpected changes.

**Command:**
```bash
cd "$REPO_ROOT/$TF_DIR"
terraform plan -input=false -detailed-exitcode \
  -var "name=test-efs-dryrun" \
  -var "aws_region=us-east-1" \
  -var "vpc_cidr=10.0.0.0/16" \
  -var "keycloak_admin_password=test123" \
  -var "keycloak_database_password=test123" \
  -var "documentdb_admin_password=test123" \
  -var "storage_backend=file" \
  -var "enable_cloudfront=false" \
  -var "enable_route53_dns=false" \
  -var "enable_waf=false" \
  -var "enable_monitoring=false" \
  -var "enable_observability=false" \
  -var "enable_demo_servers=false" \
  2>&1 | tee /tmp/terraform-plan-output.txt

# Verify no EFS resources in plan
grep -i "efs" /tmp/terraform-plan-output.txt && echo "WARNING: EFS references found in plan" || echo "PASS: No EFS in plan output"
```

**Expected Result:**
- Exit code 0 (no changes) or 2 (changes to destroy EFS)
- No EFS resources in the plan output for resources being created or modified

### 4.5 Rollback Verification

**Scenario:** The change needs to be rolled back.

**Command:**
```bash
# Revert all changes via git
git checkout -- terraform/aws-ecs/
```

**Expected Result:**
- All EFS resources, variables, outputs, and references are restored
- `terraform validate` passes with EFS resources present

**Justification:** Since this is a pure code change with no external API calls, rolling back is a `git checkout` of the changed files.

## 5. End-to-End API Tests

**Verdict:** **Not Applicable** - This change does not modify any HTTP endpoints or CLI commands. The ECS task definitions are rendered by Terraform but the application code (registry, auth-server, mcpgw) is not modified. No API-level testing is needed.

## 6. Test Execution Checklist

- [ ] Section 1 (Functional) passes
  - [ ] 1.1 `terraform validate` returns exit code 0
  - [ ] 1.2 `terraform plan` shows no EFS resources (or only EFS destruction)
  - [ ] 1.3 Grep returns zero EFS references in .tf files
  - [ ] 1.4 Grep returns zero EFS references in README.md
  - [ ] 1.5 EFS variables removed from module variables
  - [ ] 1.6 EFS outputs removed from module and root outputs
  - [ ] 1.7 storage.tf is deleted
- [ ] Section 2 (Backwards Compat) verified
  - [ ] 2.1 Deployments without EFS variables still plan
  - [ ] 2.2 Existing state shows EFS destruction
- [ ] Section 3 (UX) verified
  - [ ] 3.1 terraform plan output is clean
  - [ ] 3.2 README IAM permissions no longer include elasticfilesystem
- [ ] Section 4 (Deployment) verified
  - [ ] 4.1 terraform fmt check passes
  - [ ] 4.2 Dry-run plan succeeds
  - [ ] 4.3 Rollback via git checkout works
- [ ] Section 5 (E2E) verified or marked Not Applicable
- [ ] `terraform validate` passes with no regressions

## Pre-Deployment Warning for Live Environments

Before running `terraform apply` on a live deployment, operators should:

1. **Verify the EFS file system is empty or that data loss is acceptable:**
   ```bash
   aws efs describe-file-systems --query "FileSystems[*].[Id,Name]"
   aws efs describe-access-points --file-system-id <EFS_ID>
   ```

2. **Run `terraform plan` first and review the destruction list:**
   ```bash
   terraform plan -detailed-exitcode 2>&1 | grep -i "destroy"
   ```

3. **Confirm scopes.yml availability:** The auth-server needs scopes.yml at a path other than `/efs/auth_config/`. Verify it is available in the Docker image or via an alternative mechanism.

4. **Accept that EFS destruction is irreversible:** AWS EFS has a 7-day pending-deletion state, but data is unrecoverable after deletion.