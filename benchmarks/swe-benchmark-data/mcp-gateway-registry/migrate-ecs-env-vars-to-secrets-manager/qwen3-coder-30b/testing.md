# Testing Plan: Migrate ECS Environment Variables to AWS Secrets Manager

*Created: 2026-07-22*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview
### Scope of Testing
This testing plan covers verification of the migration from plaintext ECS environment variables to AWS Secrets Manager. Tests ensure that sensitive configuration data is properly secured, services can still access secrets, and fallback mechanisms work correctly during migration.

### Prerequisites
- [ ] AWS account with Secrets Manager enabled
- [ ] Terraform environment configured for deployment
- [ ] Existing ECS services running with current environment variables
- [ ] Access to deployment environments (staging/production)

### Shared Variables
```bash
export TF_VAR_database_password="test-db-password"
export TF_VAR_api_key="test-api-key"
export TF_VAR_oauth_client_secret="test-oauth-secret"
```

## 1. Functional Tests
### 1.1 Terraform Configuration Tests
**Test:** Verify Terraform can create Secrets Manager resources
```bash
# Run terraform plan to validate secret creation
cd terraform/
terraform init
terraform plan -out=tfplan
```
**Expected Status:** 0 (success)
**Expected Response:** No errors, secret resources shown in plan
**Assertions:**
- No errors in terraform plan
- Secret resources created as expected
- IAM policy updates included in plan

**Negative Case:** 
```bash
# Test with invalid secret values
export TF_VAR_database_password=""
terraform plan
```
**Expected Status:** Non-zero exit code
**Expected Response:** Error about empty secret values

### 1.2 ECS Task Definition Tests
**Test:** Verify ECS task definitions reference secrets correctly
```bash
# Check that secrets are in the container definitions
cd terraform/
terraform show -no-color | grep -A 10 -B 5 "secrets"
```
**Expected Status:** 0 (success)
**Expected Response:** Secrets block present in container definitions
**Assertions:**
- Secrets block exists in ECS task definitions
- Correct ARNs are referenced
- Environment variables are still present as fallback

**Negative Case:**
```bash
# Test with missing secret references
# Manually modify terraform to remove secret references
terraform plan
```
**Expected Status:** Non-zero exit code
**Expected Response:** Error about missing secret references

### 1.3 IAM Permission Tests
**Test:** Verify IAM role has proper secret access permissions
```bash
# Validate IAM policy attached to task execution role
aws iam get-role-policy --role-name ecs-task-execution-role --policy-name ecs-task-execution-policy
```
**Expected Status:** 0 (success)
**Expected Response:** Policy includes secretsmanager:GetSecretValue action
**Assertions:**
- Policy includes required secretsmanager:GetSecretValue action
- Resource ARNs match expected secret names
- No overly permissive permissions

## 2. Backwards Compatibility Tests
**Test:** Verify existing services continue to function with fallbacks
```bash
# Deploy with fallback mechanism and verify service startup
cd terraform/
terraform apply -auto-approve
# Check service logs for successful startup with fallback
kubectl logs <service-pod> | grep "using fallback"
```
**Expected Status:** 0 (success)
**Expected Response:** Service starts successfully, logs indicate fallback usage
**Assertions:**
- Services start without errors
- Environment variables still accessible as fallback
- No breaking changes to existing functionality

**Test:** Verify application code can still access environment variables
```bash
# Test that environment variables are still accessible in container
docker run -e DATABASE_PASSWORD=test123 web-app:latest printenv DATABASE_PASSWORD
```
**Expected Status:** 0 (success)
**Expected Response:** test123
**Assertions:**
- Environment variables accessible as before
- No regression in existing application behavior

## 3. UX Tests
**Test:** Verify deployment process remains user-friendly
```bash
# Run terraform apply and verify output
cd terraform/
terraform apply -auto-approve
```
**Expected Status:** 0 (success)
**Expected Response:** Clean output showing successful deployment
**Assertions:**
- Clear success messages
- No confusing error messages
- Deployment process unchanged from before

## 4. Deployment Surface Tests
### 4.1 Terraform / ECS Wiring
**Test:** Validate that all ECS services are updated correctly
```bash
# Check all service terraform files for secret usage
find terraform/services -name "*.tf" -exec grep -l "secrets" {} \;
```
**Expected Status:** 0 (success)
**Expected Response:** All service files show secret usage
**Assertions:**
- All services updated with secrets block
- IAM policies updated for all services
- Fallback environment variables maintained

### 4.2 Deploy and verify
**Test:** Complete deployment cycle with secrets
```bash
# Full deployment and verification
cd terraform/
terraform init
terraform apply -auto-approve
# Verify deployment succeeded
terraform show -no-color | grep -i "secret"
```
**Expected Status:** 0 (success)
**Expected Response:** Successful deployment with secrets shown
**Assertions:**
- All resources deployed successfully
- Secrets created in AWS
- Services configured to use secrets

### 4.3 Rollback verification
**Test:** Verify rollback capability
```bash
# Test reverting to environment variables only
cd terraform/
terraform destroy -auto-approve
# Verify environment variables work
terraform apply -auto-approve
```
**Expected Status:** 0 (success)
**Expected Response:** Successful rollback to original state
**Assertions:**
- Rollback works without errors
- Original environment variables restored
- No data loss during rollback

## 5. End-to-End API Tests
**Test:** Verify complete migration workflow
```bash
# Full workflow test
cd terraform/
# 1. Create secrets
terraform apply -auto-approve
# 2. Verify secret access
aws secretsmanager get-secret-value --secret-id web-app/database-password
# 3. Verify service startup with secrets
# 4. Verify fallback still works
```
**Expected Status:** 0 (success)
**Expected Response:** All steps succeed without errors
**Assertions:**
- Secrets created successfully
- Secrets accessible via AWS CLI
- Services start with secret values
- Fallback mechanism works when secrets unavailable

## 6. Test Execution Checklist
- [ ] Section 1 (Functional) passes
- [ ] Section 2 (Backwards Compat) verified or marked Not Applicable
- [ ] Section 3 (UX) verified or marked Not Applicable
- [ ] Section 4 (Deployment) verified or marked Not Applicable
- [ ] Section 5 (E2E) verified or marked Not Applicable
- [ ] Unit tests added under `tests/unit/`
- [ ] Integration tests added under `tests/integration/`
- [ ] `uv run pytest tests/` passes with no regressions