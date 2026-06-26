# Testing Plan: Migrate ECS Environment Variables to AWS Secrets Manager

*Created: 2026-06-25*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview
### Scope of Testing
This testing plan validates the migration of 10 sensitive environment variables from plaintext `environment` blocks to AWS Secrets Manager via the ECS `secrets` block. The change affects Terraform configuration and ECS task definitions only.

### Prerequisites
- Terraform >= 1.2 installed
- AWS credentials configured with access to deploy to a test environment
- Clone of mcp-gateway-registry at tag 1.24.4

### Shared Variables
```bash
export TERRAFORM_DIR="benchmarks/swe-benchmark-data/mcp-gateway-registry/repo/terraform/aws-ecs"
export AWS_REGION="us-east-1"
```

---

## 1. Functional Tests

### 1.1 Terraform Validation

#### Test: terraform validate passes
Run `terraform validate` to check for syntax errors.

```bash
cd "$TERRAFORM_DIR"
terraform init -backend=false
terraform validate
```

**Expected:** `Success! The configuration is valid.`

#### Test: terraform plan shows expected changes
Generate a plan to verify the changes are correct.

```bash
cd "$TERRAFORM_DIR"
terraform plan -var-file=terraform.tfvars.example -out=tfplan.out 2>&1 | head -100
```

**Expected:** Plan should show:
- 10 new `aws_secretsmanager_secret` resources
- 10 new `aws_secretsmanager_secret_version` resources
- Updates to `aws_ecs_task_definition` resources
- Updates to `aws_iam_policy` resources

#### Test: No new dependencies introduced
Verify no new Terraform providers or modules are added.

```bash
cd "$TERRAFORM_DIR"
grep -E "source|version" main.tf modules/mcp-gateway/*.tf | sort -u
```

**Expected:** Only existing providers and modules should appear.

### 1.2 Secrets Manager Resources

#### Test: Verify all secret ARNs are generated
Check that each of the 10 secrets would be created:

```bash
grep -E "resource \"aws_secretsmanager_secret\"" "$TERRAFORM_DIR"/modules/mcp-gateway/secrets.tf
```

**Expected output (10 secrets):**
- `aws_secretsmanager_secret.registry_api_token`
- `aws_secretsmanager_secret.registry_api_keys`
- `aws_secretsmanager_secret.federation_static_token`
- `aws_secretsmanager_secret.federation_encryption_key`
- `aws_secretsmanager_secret.ans_api_key`
- `aws_secretsmanager_secret.ans_api_secret`
- `aws_secretsmanager_secret.registration_webhook_auth_token`
- `aws_secretsmanager_secret.registration_gate_auth_credential`
- `aws_secretsmanager_secret.registration_gate_oauth2_client_secret`
- `aws_secretsmanager_secret.auth0_management_api_token`

### 1.3 ECS Task Definition Changes

#### Test: Verify secrets block exists in container definitions
Check that secrets are added to the ECS container definitions:

```bash
grep -A5 "secrets = concat" "$TERRAFORM_DIR"/modules/mcp-gateway/ecs-services.tf | head -30
```

**Expected:** Should show secrets array with `name` and `valueFrom` entries.

#### Test: Verify secrets reference correct ARNs
Ensure secrets reference the newly created secret ARNs:

```bash
grep -E "registry_api_token|registry_api_keys|federation_static_token" "$TERRAFORM_DIR"/modules/mcp-gateway/ecs-services.tf | grep "valueFrom"
```

**Expected:** Should show `valueFrom = aws_secretsmanager_secret.{name}.arn`

### 1.4 IAM Policy Changes

#### Test: Verify IAM policy includes all new secrets
Check that the ECS secrets access policy includes the new secrets:

```bash
grep -A50 "Resource = concat" "$TERRAFORM_DIR"/modules/mcp-gateway/iam.tf
```

**Expected:** Should list all 10 new secret ARNs plus existing secrets.

---

## 2. Backwards Compatibility Tests

### 2.1 Environment Variables Preserved

**Not Applicable** - This test intentionally adds secrets while preserving environment variables during the transition period. We verify below that env vars still exist:

```bash
# Verify environment variables are still present (for backward compat)
grep -E "value = var\.(registry_api_token|registry_api_keys|federation_static_token|federation_encryption_key|ans_api_key|ans_api_secret|registration_webhook_auth_token|registration_gate_auth_credential)" "$TERRAFORM_DIR"/modules/mcp-gateway/ecs-services.tf
```

**Expected:** All grep matches should show `value = var.` pattern (env vars retained).

### 2.2 Terraform State Handling

**Not Applicable** - State migration is handled by Terraform automatically. The test validates that `lifecycle` blocks are not changed.

### 2.3 Variable Sensitivity

Verify sensitive variables are marked correctly:

```bash
grep -E "variable \"(registry_api_token|federation_static_token|...)" "$TERRAFORM_DIR"/modules/mcp-gateway/variables.tf | head -5
```

**Expected:** Variables should have `sensitive = true` attribute.

---

## 3. UX Tests

### 3.1 Terraform Output Readability

**Not Applicable** - This is an infrastructure change. Skip UI-specific tests.

### 3.2 Error Messages

**Not Applicable** - Testing error conditions requires actual deployment.

---

## 4. Deployment Surface Tests

### 4.1 Verify terraform.tfvars.example is documented

Check that new variables are documented:

```bash
grep -E "registry_api_token|registration_webhook|registration_gate|federation_(static_token|encryption_key)" "$TERRAFORM_DIR"/terraform.tfvars.example | head -20
```

**Expected:** Variables should appear in `.tfvars.example` with comments explaining they are sensitive.

### 4.2 Verify variables.tf has descriptions

```bash
grep -B2 "variable \"registry_api_token\"" "$TERRAFORM_DIR"/modules/mcp-gateway/variables.tf
```

**Expected:** Should have description field explaining the variable.

### 4.3 Docker / ECS Container Configuration

**Not Applicable** - No Docker changes required. Container configuration is handled by Terraform ECS module.

---

## 5. End-to-End API Tests

### 5.1 Secrets Manager Access from ECS Task

**Not Applicable** - Actual ECS task execution requires AWS deployment. We verify via Terraform plan that IAM policies would allow it.

### 5.2 Multi-Service Secret Access

Verify secret access is correctly scoped:

- Auth Server: Should have access to registry_api_token, registry_api_keys, federation_* secrets, ans_*, auth0_* secrets
- Registry: Should have access to all above plus registration_webhook_* and registration_gate_* secrets

```bash
# Check auth-server container secrets (lines around 413-480)
sed -n '413,480p' "$TERRAFORM_DIR"/modules/mcp-gateway/ecs-services.tf | grep "name.*="

# Check registry container secrets (lines around 1288-1362)
sed -n '1288,1362p' "$TERRAFORM_DIR"/modules/mcp-gateway/ecs-services.tf | grep "name.*="
```

**Expected:** Auth server should not have registration_gate_* secrets.

---

## 6. Test Execution Checklist

- [ ] Section 1.1 (terraform validate) passes
- [ ] Section 1.2 (terraform plan) shows expected changes
- [ ] Section 1.3 (10 secrets created) verified
- [ ] Section 1.4 (ECS container secrets) verified
- [ ] Section 1.5 (IAM policy) verified
- [ ] Section 2.1 (backward compat env vars) verified - env vars still present
- [ ] Section 2.2 N/A - state handled by Terraform
- [ ] Section 2.3 (variable sensitivity) verified
- [ ] Section 3 N/A - no UI changes
- [ ] Section 4.1 (.tfvars.example) verified
- [ ] Section 4.2 (variables.tf) verified
- [ ] Section 4.3 N/A - no Docker changes
- [ ] Section 5 N/A - requires actual AWS deployment
- [ ] All secrets identified in issue are covered

---

## 7. Integration with Existing Infrastructure

### 7.1 Verify Compatibility with Existing Secrets

The codebase already uses Secrets Manager for:
- `SECRET_KEY`
- `KEYCLOAK_CLIENT_SECRET`
- `KEYCLOAK_M2M_CLIENT_SECRET`
- `DOCUMENTDB_USERNAME` / `DOCUMENTDB_PASSWORD`
- `ENTRA_CLIENT_SECRET` (conditional)
- `OKTA_CLIENT_SECRET` / `OKTA_M2M_CLIENT_SECRET` / `OKTA_API_TOKEN` (conditional)
- `AUTH0_CLIENT_SECRET` / `AUTH0_M2M_CLIENT_SECRET` (conditional)

```bash
# Verify existing pattern is followed
grep -E "resource \"aws_secretsmanager_secret\"" "$TERRAFORM_DIR"/modules/mcp-gateway/secrets.tf | wc -l
```

**Expected:** Should show total count > 10 (existing + new secrets).

### 7.2 Verify Keycloak ECS Also Uses Secrets

Keycloak already uses secrets in `keycloak-ecs.tf`:

```bash
grep -E "secrets" "$TERRAFORM_DIR"/keycloak-ecs.tf | head -5
```

**Expected:** Keycloak should continue to work with its existing secrets.

---

## 8. Security Verification

### 8.1 No Plaintext Secrets in Plan Output

When running `terraform plan`, verify secret values are not shown:

```bash
cd "$TERRAFORM_DIR"
terraform plan -var-file=terraform.tfvars.example 2>&1 | grep -E "(registry_api_token|federation_static_token)" | grep -v "var\."
```

**Expected:** Should not show actual secret values, only variable references.

### 8.2 IAM Least Privilege

Verify IAM policy only allows GetSecretValue:

```bash
grep -A10 "secretsmanager:GetSecretValue" "$TERRAFORM_DIR"/modules/mcp-gateway/iam.tf
```

**Expected:** Should show only `secretsmanager:GetSecretValue` action, not other actions.

---

## Summary

This testing plan validates:
1. Terraform configuration is valid and generates expected resources
2. All 10 secrets are correctly created and referenced
3. Backward compatibility is maintained (environment variables preserved)
4. IAM policies allow ECS tasks to read the new secrets
5. No new dependencies or breaking changes are introduced

The tests focus on static analysis and Terraform validation. Full E2E testing requires deployment to an AWS environment.