# Testing Plan: Remove EFS from terraform/aws-ecs/

*Created: 2026-07-22*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview

### Scope of Testing

This testing plan covers verification that EFS has been completely removed from the terraform/aws-ecs/ deployment without breaking `terraform validate`, `terraform plan`, or the ECS service configurations. It covers code-level greps, Terraform validation, script behavior, and deployment surface verification.

### Prerequisites

- [ ] Target repo checked out (at tag 1.24.4 or desired branch)
- [ ] All changes from the LLD implemented (files deleted, files modified)
- [ ] `terraform` CLI installed (version >= 1.2)
- [ ] No actual AWS credentials needed for `terraform validate`

### Shared Variables

```bash
export TF_DIR="terraform/aws-ecs"
```

## 1. Functional Tests

### 1.1 Terraform Validate

**Command:**
```bash
cd "$TF_DIR" && terraform init -backend=false && terraform validate
```

**Expected:**
- Exit code 0
- Output: `Success! The configuration is valid.`

**Assertions:**
- No errors about missing modules, missing variables, or undefined references
- No errors about `module.efs` (since storage.tf is deleted, any remaining reference is an error)
- No errors about removed variables `efs_throughput_mode` or `efs_provisioned_throughput`

### 1.2 Terraform Plan

**Command:**
```bash
cd "$TF_DIR" && terraform init -backend=false && terraform plan -input=false \
  -var='aws_region=us-east-1' \
  -var='name=test-efs-removal' \
  -var='storage_backend=file' \
  -var='keycloak_admin_password=test' \
  -var='documentdb_credentials_secret_arn=' \
  -var='ingress_cidr_blocks=["10.0.0.0/8"]' \
  -var='enable_monitoring=false' \
  -var='enable_autoscaling=false' 2>&1
```

**Expected:**
- Exit code 0
- Plan succeeds with zero errors
- No EFS-related resources in the plan

**Assertions:**
- Plan output contains no `aws_efs_file_system`
- Plan output contains no `aws_efs_mount_target`
- Plan output contains no `aws_efs_access_point`
- Plan output contains no `aws_vpc_security_group_rule` referencing port 2049 from the EFS module

### 1.3 EFS Reference Grep in Terraform Files

**Command:**
```bash
grep -rni 'module\.efs\|efs_volume_configuration\|efs_throughput' "$TF_DIR" --include="*.tf" | grep -v '.terraform/' | grep -v 'Binary'
```

**Expected:**
- Zero matches

**Assertions:**
- No files contain `module.efs`
- No files contain `efs_volume_configuration`
- No files contain `efs_throughput_mode` or `efs_provisioned_throughput`

### 1.4 EFS Reference Grep in Shell Scripts

**Command:**
```bash
grep -rni 'mcp_gateway_efs\|EFS_ID\|efs_access_point' "$TF_DIR/scripts/" 2>/dev/null || echo "No EFS references in scripts (PASS)"
```

**Expected:**
- No meaningful EFS references (commented-out deprecation messages are acceptable)

**Assertions:**
- `run-scopes-init-task.sh` does not reference EFS IDs (or has been deprecated entirely)
- `post-deployment-setup.sh` does not validate `mcp_gateway_efs_id`

## 2. Backwards Compatibility Tests

### 2.1 No EFS References in ECS Service Definitions

**Command:**
```bash
grep -n 'efs\|EFS' "$TF_DIR/modules/mcp-gateway/ecs-services.tf" || echo "No EFS references found (PASS)"
```

**Expected:**
- No matches, or only comments about EFS removal

**Assertions:**
- `volume = {}` appears in both auth service and MCPGW service blocks (not an EFS volume block)
- No `sourceVolume` references to `mcp-logs`, `auth-config`, or `mcpgw-data`
- `SCOPES_CONFIG_PATH` in auth service section is `/app/auth_server/scopes.yml` (not `/efs/`)

### 2.2 No EFS Outputs in Module

**Command:**
```bash
grep -n 'efs' "$TF_DIR/modules/mcp-gateway/outputs.tf" || echo "No EFS references found (PASS)"
```

**Expected:**
- No matches

**Assertions:**
- No `output "efs_id"`, `output "efs_arn"`, `output "efs_access_points"`
- Service Discovery outputs and other non-EFS outputs still exist

### 2.3 No EFS Outputs in Root Module

**Command:**
```bash
grep -n 'mcp_gateway_efs' "$TF_DIR/outputs.tf" || echo "No EFS output passthrough found (PASS)"
```

**Expected:**
- No matches

**Assertions:**
- No `output "mcp_gateway_efs_id"`, `output "mcp_gateway_efs_arn"`, `output "mcp_gateway_efs_access_points"`
- Monitoring outputs and other non-EFS outputs still exist

### 2.4 No EFS Variables in Module

**Command:**
```bash
grep -n 'efs_throughput\|efs_provisioned' "$TF_DIR/modules/mcp-gateway/variables.tf" || echo "No EFS variables found (PASS)"
```

**Expected:**
- No matches

### 2.5 Registry Service Unchanged

**Command:**
```bash
grep -A5 'ecs_service_registry' "$TF_DIR/modules/mcp-gateway/ecs-services.tf" | head -10
```

**Expected:**
- Registry service still has `volume = {}` (was already non-EFS before this change)
- Registry service SCOPES_CONFIG_PATH still `/app/auth_server/scopes.yml`

### 2.6 Auth Service SCOPES_CONFIG_PATH

**Command:**
```bash
grep -A2 'SCOPES_CONFIG_PATH' "$TF_DIR/modules/mcp-gateway/ecs-services.tf" | head -6
```

**Expected:**
- Auth service sets `SCOPES_CONFIG_PATH` to `/app/auth_server/scopes.yml`
- No reference to `/efs/` in auth service section

### 2.7 MCPGW Volume Configuration

**Command:**
```bash
grep -B5 -A10 'ecs_service_mcpgw' "$TF_DIR/modules/mcp-gateway/ecs-services.tf" | grep -A5 'volume'
```

**Expected:**
- MCPGW service has `volume = {}` (no EFS volumes)

## 3. UX Tests

### 3.1 Script Error Messages

**Test:** Run `run-scopes-init-task.sh` after EFS removal.

**Command:**
```bash
cd "$TF_DIR" && bash scripts/run-scopes-init-task.sh --skip-build 2>&1 || true
```

**Expected:**
- Script fails gracefully with a clear error message indicating EFS has been removed
- Error message guides users to bundle scopes.yml in the container image or use an alternative mechanism

### 3.2 Post-Deployment Setup Validation

**Test:** Run `post-deployment-setup.sh` with EFS outputs missing.

**Command:**
```bash
# Simulate: create a terraform-outputs.json that has all required fields EXCEPT mcp_gateway_efs_id
echo '{"vpc_id":{"value":"vpc-123"},"ecs_cluster_name":{"value":"test"},"ecs_cluster_arn":{"value":"arn:aws:ecs:..."},"mcp_gateway_url":{"value":"http://test"},"mcp_gateway_auth_url":{"value":"http://test"},"keycloak_url":{"value":"http://test"}}' > /tmp/test-outputs.json
```

**Expected:**
- Validation passes (mcp_gateway_efs_id is no longer required)
- No error about missing EFS output

### 3.3 Post-Deployment Setup EFS Fallback

**Test:** Verify that the `_initialize_scopes` function does not attempt EFS initialization when DocumentDB is absent.

**Command:**
```bash
grep -A15 '_initialize_scopes' "$TF_DIR/scripts/post-deployment-setup.sh" | grep -i 'efs\|run-scopes-init' || echo "No EFS fallback in _initialize_scopes (PASS)"
```

**Expected:**
- No EFS fallback in the `_initialize_scopes` function (or it explicitly errors instead of silently running the deprecated script)

## 4. Deployment Surface Tests

### 4.1 storage.tf Deletion

**Command:**
```bash
test -f "$TF_DIR/modules/mcp-gateway/storage.tf" && echo "FAIL: storage.tf still exists" || echo "PASS: storage.tf deleted"
```

**Expected:**
- storage.tf does not exist

### 4.2 README IAM Permissions

**Command:**
```bash
grep 'elasticfilesystem' "$TF_DIR/README.md" || echo "No elasticfilesystem reference found (PASS)"
```

**Expected:**
- No `elasticfilesystem:*` in README.md

### 4.3 README Features List

**Command:**
```bash
grep -i 'EFS Shared Storage' "$TF_DIR/README.md" || echo "No EFS Shared Storage feature mentioned (PASS)"
```

**Expected:**
- No mention of "EFS Shared Storage" in README.md

### 4.4 terraform.tfvars.example

**Command:**
```bash
grep -i 'efs' "$TF_DIR/terraform.tfvars.example" || echo "No EFS entries found (PASS)"
```

**Expected:**
- No EFS-related entries (confirmed: there were none to begin with)

### 4.5 Module Variable Wiring

**Command:**
```bash
grep 'efs_throughput_mode\|efs_provisioned_throughput' "$TF_DIR/main.tf" || echo "No EFS wiring in main.tf (PASS)"
```

**Expected:**
- No EFS variable wiring in main.tf (confirmed: there was none to begin with)

### 4.6 Helm Charts (No EFS)

**Command:**
```bash
grep -ri 'efs' "$TF_DIR/../charts/" 2>/dev/null | grep -v 'global\.' || echo "No EFS references in Helm charts (PASS)"
```

**Expected:**
- No EFS references in Helm chart templates (the charts target Kubernetes, not ECS)

### 4.7 Deploy and Verify (Full Plan Simulation)

**Command:**
```bash
cd "$TF_DIR" && terraform init -backend=false && terraform plan -input=false \
  -var='aws_region=us-east-1' \
  -var='name=test-efs-removal' \
  -var='storage_backend=file' \
  -var='keycloak_admin_password=test' \
  -var='documentdb_credentials_secret_arn=' \
  -var='ingress_cidr_blocks=["10.0.0.0/8"]' \
  -var='enable_monitoring=false' \
  -var='enable_autoscaling=false' 2>&1
```

**Expected:**
- Plan succeeds
- No EFS resources in plan

**Assertions:**
- Plan contains no `aws_efs_file_system`
- Plan contains no `aws_efs_mount_target`
- Plan contains no `aws_efs_access_point`
- Plan contains no `aws_vpc_security_group_rule` or `aws_vpc_security_group_egress_rule` for port 2049 or EFS

### 4.8 Rollback Verification (State Safety)

**Note:** This test verifies that the Terraform configuration is syntactically valid and can be re-initialized. In a real deployment, operators should:
1. Export current state: `terraform state list`
2. Review planned changes: `terraform plan -out=tfplan`
3. Verify no unexpected resource deletions (beyond EFS resources being destroyed)

**Command:**
```bash
cd "$TF_DIR" && terraform init -backend=false && terraform state list 2>&1 | head -5
```

**Expected:**
- State list command succeeds (even with no state, it should not error)

## 5. End-to-End API Tests

**Not Applicable** - This change does not modify any HTTP endpoints or CLI commands. It only modifies Terraform infrastructure configuration.

## 6. Test Execution Checklist

- [ ] Section 1 (Functional) passes
  - [ ] `terraform validate` succeeds
  - [ ] `terraform plan` succeeds
  - [ ] No `module.efs` references in `.tf` files
  - [ ] No `efs_volume_configuration` references in `.tf` files
- [ ] Section 2 (Backwards Compat) verified
  - [ ] No EFS references in ecs-services.tf
  - [ ] No EFS outputs in module outputs.tf
  - [ ] No EFS outputs in root outputs.tf
  - [ ] No EFS variables in module variables.tf
  - [ ] Registry service unchanged
  - [ ] Auth service SCOPES_CONFIG_PATH updated to `/app/auth_server/scopes.yml`
  - [ ] MCPGW volume is `volume = {}`
- [ ] Section 3 (UX) verified
  - [ ] run-scopes-init-task.sh fails gracefully
  - [ ] post-deployment-setup.sh does not require mcp_gateway_efs_id
  - [ ] post-deployment-setup.sh EFS fallback removed
- [ ] Section 4 (Deployment) verified
  - [ ] storage.tf deleted
  - [ ] README IAM permissions updated (no elasticfilesystem)
  - [ ] README features list updated (no EFS Shared Storage)
  - [ ] terraform.tfvars.example has no EFS entries
  - [ ] main.tf has no EFS wiring
  - [ ] terraform plan produces no EFS resources
- [ ] Section 5 (E2E) verified or marked Not Applicable
- [ ] Unit tests: N/A (no application code changes)
- [ ] Integration tests: N/A (Terraform validation is the integration test)