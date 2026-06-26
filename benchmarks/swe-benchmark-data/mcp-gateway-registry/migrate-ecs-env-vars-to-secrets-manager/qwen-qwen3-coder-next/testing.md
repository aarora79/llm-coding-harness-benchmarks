# Testing Plan: Migrate Sensitive ECS Environment Variables to AWS Secrets Manager

*Created: 2026-06-25*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview
### Scope of Testing
This plan verifies that all sensitive environment variables have been migrated from plaintext configuration to AWS Secrets Manager in ECS task definitions. Testing covers:
- Secrets Manager secret creation and configuration
- ECS task definition secret injection
- IAM permission verification
- Service startup with secrets

### Prerequisites
- [ ] AWS CLI configured with appropriate credentials
- [ ] Terraform installed (v1.5+)
- [ ] AWS Secrets Manager and ECS permissions
- [ ] Access to existing Terraform state
- [ ]Staging environment available for testing

### Shared Variables
```bash
# AWS Configuration
export AWS_REGION="us-west-2"
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# Resource Names (derived from terraform.tfvars)
export TF_VAR_name="mcp-gateway"
export STACK_NAME="${TF_VAR_name}-staging"

# Terraform State Configuration
export TF_STATE_BUCKET="mcp-gateway-terraform-state"
export TF_STATE_KEY="ecs/migrate-secrets/terraform.tfstate"

# Test Resources
export TEST_SECRET_NAME="mcp-gateway-${TF_VAR_name}-test-secret"
export TEST_TASK_DEFINITION="mcp-gateway-staging-test"
```

---

## 1. Functional Tests

### 1.1 Terraform Validation Tests

#### Test 1.1.1: Verify Terraform Configuration Syntax
**Command:**
```bash
cd terraform/aws-ecs
terraform init -backend=false
terraform validate
```

**Expected Output:**
```
Success! The configuration is valid.
```

**Assertions:**
- No syntax errors
- All resource references are valid
- Variables with `sensitive = true` are properly defined

#### Test 1.1.2: Verify Secrets Manager Resources Plan
**Command:**
```bash
cd terraform/aws-ecs
terraform plan -no-color 2>&1 | tee /tmp/tf-plan-${RANDOM}.log
```

**Expected Results:**
- New `aws_secretsmanager_secret` resources are created
- Updated `aws_ecs_task_definition` resources include `secrets` blocks
- Updated `aws_iam_role_policy` includes `secretsmanager:GetSecretValue` permissions

**Negative Case - Unset Required Variable:**
```bash
# Temporarily unset a required variable
unset TF_VAR_embeddings_api_key

terraform plan 2>&1 | grep -q "value for variable \"embeddings_api_key\""

# Should fail with variable error
if [ $? -eq 0 ]; then
  echo "PASS: Variable validation works"
else
  echo "FAIL: Variable validation not working"
  exit 1
fi
```

### 1.2 Secrets Manager API Tests

#### Test 1.2.1: Create Test Secret
**Command:**
```bash
aws secretsmanager create-secret \
  --name "$TEST_SECRET_NAME" \
  --description "Test secret for migration verification" \
  --secret-string '{"test_key":"test_value"}' \
  --region "$AWS_REGION"
```

**Expected Response:**
```json
{
  "ARN": "arn:aws:secretsmanager:$AWS_REGION:$AWS_ACCOUNT_ID:secret:$TEST_SECRET_NAME-XXXXXX",
  "Name": "$TEST_SECRET_NAME",
  "VersionId": "v1-XXXXXXXXX"
}
```

**Assertions:**
- Secret is created successfully
- ARN follows expected format
- VersionId is present

#### Test 1.2.2: Read Test Secret
**Command:**
```bash
SECRET_ARN=$(aws secretsmanager describe-secret --secret-id "$TEST_SECRET_NAME" --query ARN --output text)
aws secretsmanager get-secret-value --secret-id "$SECRET_ARN" --query SecretString --output text
```

**Expected Output:**
```json
{"test_key":"test_value"}
```

**Assertions:**
- Secret can be retrieved
- Value matches what was stored

#### Test 1.2.3: Verify IAM Permission for Secret Access
**Command:**
```bash
# Get the ECS task execution role ARN
TASK_EXEC_ROLE=$(aws iam list-roles --query "Roles[?contains(RoleName, 'task-exec-role')].RoleName" --output text)
POLICY=$(aws iam list-role-policies --role-name "$TASK_EXEC_ROLE" --query "PolicyNames[0]" --output text)
aws iam get-policy-version --policy-arn "arn:aws:iam::$AWS_ACCOUNT_ID:role/$TASK_EXEC_ROLE" --version-id $(aws iam list-role-policies --role-name "$TASK_EXEC_ROLE" --query "PolicyVersionList[0].VersionId" --output text) --query "PolicyVersion.Document"
```

**Expected Policy Statement:**
```json
{
  "Effect": "Allow",
  "Action": ["secretsmanager:GetSecretValue"],
  "Resource": [
    "arn:aws:secretsmanager:...:secret:mcp-gateway-*"
  ]
}
```

**Assertions:**
- Policy includes `secretsmanager:GetSecretValue` action
- Resource ARN pattern matches our secrets

### 1.3 ECS Task Definition Tests

#### Test 1.3.1: Verify Task Definition Secret Block
**Command:**
```bash
# List task definitions and get the latest revision
TASK_DEF=$(aws ecs list-task-definitions --family-prefix "mcp-gateway" --query "taskDefinitionArns[0]" --output text)
aws ecs describe-task-definition --task-definition "$TASK_DEF" --query "taskDefinition.containerDefinitions[0].secrets"
```

**Expected Output (for a service using secrets):**
```json
[
  {
    "name": "SECRET_KEY",
    "valueFrom": "arn:aws:secretsmanager:us-west-2:123456789012:secret:mcp-gateway-secret-key-XXXXXX"
  },
  {
    "name": "KEYCLOAK_CLIENT_SECRET",
    "valueFrom": "arn:aws:secretsmanager:us-west-2:123456789012:secret:mcp-gateway-keycloak-client-secret-XXXXXX"
  }
]
```

**Assertions:**
- `secrets` array exists in container definitions
- Each secret has `name` and `valueFrom` fields
- `valueFrom` contains valid Secrets Manager ARN

#### Test 1.3.2: Verify No Plaintext Secrets in Environment
**Command:**
```bash
# Check for known secret patterns in environment variables
aws ecs describe-task-definition --task-definition "$TASK_DEF" \
  --query "taskDefinition.containerDefinitions[].environment" | \
  grep -E "(PASSWORD|SECRET|TOKEN|API_KEY|KEY)" || true
```

**Expected Result:**
No output (no plaintext secrets found in environment)

**Negative Case - Should Find None:**
```bash
ENV_VARS=$(aws ecs describe-task-definition --task-definition "$TASK_DEF" --query "taskDefinition.containerDefinitions[].environment")
echo "$ENV_VARS" | grep -c "\"value\":"  # Should be low - only non-sensitive config
```

---

## 2. Backwards Compatibility Tests

### 2.1 Existing Task Definitions
**Test:** Verify that existing task definitions without secrets still work

**Command:**
```bash
# List all task definitions
aws ecs list-task-definitions --query "taskDefinitionArns[]" --output text | while read ARN; do
  aws ecs describe-task-definition --task-definition "$ARN" --query "taskDefinition.status"
done
```

**Expected Output:** All task definitions show `ACTIVE` status

### 2.2 ServiceStartup Without New Secrets
**Test:** Deploy a service that doesn't use new secrets to verify no regression

**Command:**
```bash
# Deploy the currenttime demo service ( Minimal, no secrets)
aws ecs update-service \
  --cluster "${TF_VAR_name}-cluster" \
  --service "${TF_VAR_name}-currenttime" \
  --force-new-deployment
```

**Expected Result:** Service starts successfully without new secrets

### 2.3 Variable Default Values
**Test:** Verify default values work when secret not provided

**Command:**
```bash
# Run terraform with unset sensitive variable
terraform plan -var="embeddings_api_key=" 2>&1 | grep -q "default"
```

**Expected:** Plan completes with default value handling

---

## 3. UX Tests

### 3.1 Terraform Output Messages
**Test:** Verify terraform output is clear and actionable

**Command:**
```bash
terraform apply -auto-approve 2>&1 | tee /tmp/tf-apply.log
```

**Expected Messages:**
- "aws_secretsmanager_secret.name: Creation complete" for each secret
- "aws_ecs_task_definition.name: Creation/Update complete"
- Clear error messages if IAM permissions are insufficent

**Negative Case - Invalid ARN:**
```bash
# Temporarily change a secret ARN to an invalid value
sed -i 's/arn:aws:secretsmanager/arn:aws:INVALID/g' terraform/aws-ecs/modules/mcp-gateway/ecs-services.tf

terraform plan 2>&1 | grep -E "(error|Error|ERROR|Invalid|invalid)"

# Should show clear error about invalid ARN
```

### 3.2 CLI Error Handling

#### Test 3.2.1: Missing Secret ARN
**Command:**
```bash
aws ecs register-task-definition \
  --family "test-bad-secret" \
  --container-definitions '[{
    "name": "test",
    "image": "nginx",
    "secrets": [{
      "name": "TEST",
      "valueFrom": "arn:aws:secretsmanager:us-west-2:000000000000:secret:nonexistent"
    }]
  }]'
```

**Expected Error:**
```
An error occurred (InvalidParameterException) when calling the RegisterTaskDefinition operation: The secret valueFrom ARN does not refer to a existing secret.
```

#### Test 3.2.2: Insufficient IAM Permissions
**Command:**
```bash
# Try to get a secret without proper permissions
aws secretsmanager get-secret-value \
  --secret-id "$TEST_SECRET_NAME" \
  --query SecretString
```

**Expected Error:**
```
An error occurred (AccessDeniedException) when calling the GetSecretValue operation: User is not authorized
```

---

## 4. Deployment Surface Tests

### 4.1 Terraform State

#### Test 4.1.1: State Contain Secrets?
**Command:**
```bash
# Get the Terraform state
terraform state pull > /tmp/tf-state.json

# Check for plaintext secret values in state
grep -E "(password|secret|token|key)" /tmp/tf-state.json | \
  grep -v "arn:" | \
  grep -v "_arn" | \
  head -20
```

**Expected Result:** No plaintext secret values (only ARNs or variable references)

#### Test 4.1.2: State Encryption
**Command:**
```bash
# Check S3 bucket encryption for state
aws s3api get-bucket-encryption --bucket "$TF_STATE_BUCKET" 2>&1 | \
  jq '.ServerSideEncryptionConfiguration.Rules[].ApplyServerSideEncryptionByDefault'
```

**Expected Output:**
```json
{
  "SSEAlgorithm": "AES256"
}
```

### 4.2 Terraform Variables

#### Test 4.2.1: Sensitive Variablesmarked
**Command:**
```bash
# Check variables.tf for sensitive=true
grep -n "sensitive.*=.*true" terraform/aws-ecs/modules/mcp-gateway/variables.tf
```

**Expected Output (should list known sensitive variables):**
```
376:variable "keycloak_admin_password" {
379:  sensitive   = true
336:variable "embeddings_api_key" {
340:  sensitive   = true
# ... more entries
```

#### Test 4.2.2: Terraform Plan Without Values
**Command:**
```bash
# Run plan without providing sensitive values
terraform plan -var="entra_client_secret=" -var="okta_api_token=" 2>&1 | tee /tmp/tf-plan-no-secrets.log

# Should complete successfully with defaults
grep -q "No changes" /tmp/tf-plan-no-secrets.log || grep -q "to create" /tmp/tf-plan-no-secrets.log
```

### 4.3 IAM Policy Verification

#### Test 4.3.1: Task Execution Role Policy
**Command:**
```bash
TASK_EXEC_ROLE=$(aws iam list-roles --query "Roles[?contains(RoleName, 'task-exec-role')].RoleName" --output text)
aws iam get-role-policy \
  --role-name "$TASK_EXEC_ROLE" \
  --policy-name "*secrets*" \
  --query "PolicyDocument"
```

**Expected Policy:**
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["secretsmanager:GetSecretValue"],
      "Resource": ["arn:aws:secretsmanager:*:*:secret:mcp-gateway-*"]
    }
  ]
}
```

#### Test 4.3.2: Least Privilege Verification
**Command:**
```bash
# Check that secrets manager access is scoped to mcp-gateway secrets
POLICY_DOC=$(aws iam get-role-policy \
  --role-name "$TASK_EXEC_ROLE" \
  --policy-name "*secrets*" \
  --query "PolicyDocument" --output text)

# Should contain wildcard resource pattern, NOT wildcard action
echo "$POLICY_DOC" | grep -q '"Action": "secretsmanager:GetSecretValue"'
echo "$POLICY_DOC" | grep -q '"Resource".*"mcp-gateway"'
```

### 4.4 Secret Rotation Configuration

#### Test 4.4.1: Rotation Handler (Optional)
**Command:**
```bash
# Check if rotation lambda is configured
aws lambda list-functions --query "Functions[?contains(FunctionName, 'rotation')].FunctionName" --output text
```

**Expected:** None (rotation not yet implemented per design scope)

#### Test 4.4.2: Recovery Period
**Command:**
```bash
aws secretsmanager describe-secret --secret-id "$TEST_SECRET_NAME" --query "RecoveryPeriodInDays"
```

**Expected Response:**
```json
30
```

---

## 5. End-to-End API Tests

### 5.1 Service Deployment Test

#### Test 5.1.1: Deploy and Verify
**Command:**
```bash
# Deploy the updated task definition
aws ecs update-service \
  --cluster "${TF_VAR_name}-cluster" \
  --service "${TF_VAR_name}-auth" \
  --force-new-deployment

# Wait for service to stabilize
sleep 60

# Check service status
aws ecs describe-services \
  --cluster "${TF_VAR_name}-cluster" \
  --services "${TF_VAR_name}-auth" \
  --query "services[0].status"

# Should be "ACTIVE"
```

**Expected Output:** `ACTIVE`

#### Test 5.1.2: Container Health Check
**Command:**
```bash
# Get running tasks
TASKS=$(aws ecs list-tasks \
  --cluster "${TF_VAR_name}-cluster" \
  --service "${TF_VAR_name}-auth" \
  --desired-state RUNNING \
  --query "taskArns[]" --output text)

# Inspect task containers
for TASK in $TASKS; do
  aws ecs describe-tasks \
    --cluster "${TF_VAR_name}-cluster" \
    --tasks "$TASK" \
    --query "tasks[0].containers[].lastStatus"
done
```

**Expected Output:** `RUNNING` for all containers

### 5.2 Secret Injection Verification

#### Test 5.2.1: Runtime Secret Verification
**Command:**
```bash
# SSH to an ECS container (requires ECS Exec to be enabled)
aws ecs execute-command \
  --cluster "${TF_VAR_name}-cluster" \
  --task "$TASK_ARN" \
  --container "auth-server" \
  --command "/bin/sh" \
  --interactive

# Inside container, verify secret is injected as env var
env | grep "SECRET"
```

**Expected:** Secret environment variable should be present with injected value

### 5.3 Service Communication Test

#### Test 5.3.1: Auth Server to Registry
**Command:**
```bash
# Test that services can still communicate
curl -s http://${TASK_IP}:8888/health | jq -r '.status'
```

**Expected Output:** `healthy`

---

## 6. Test Execution Checklist

- [ ] Section 1 (Functional) passes
  - [ ] 1.1.1: Terraform validation succeeds
  - [ ] 1.1.2: Terraform plan shows expected changes
  - [ ] 1.2.1: Test secret creation succeeds
  - [ ] 1.2.2: Test secret retrieval succeeds
  - [ ] 1.2.3: IAM policy includes required permissions
  - [ ] 1.3.1: Task definition secret block verified
  - [ ] 1.3.2: No plaintext secrets in environment

- [ ] Section 2 (Backwards Compat) verified
  - [ ] 2.1: Existing task definitions still active
  - [ ] 2.2: Service without secrets deploys successfully
  - [ ] 2.3: Variable defaults work when secret not provided

- [ ] Section 3 (UX) verified
  - [ ] 3.1: Terraform outputs are clear
  - [ ] 3.2.1: Missing secret ARN error handled correctly
  - [ ] 3.2.2: Insufficient IAM permissions error handled correctly

- [ ] Section 4 (Deployment) verified
  - [ ] 4.1.1: State file does not contain plaintext secrets
  - [ ] 4.1.2: State file encryption enabled
  - [ ] 4.2.1: Sensitive variables are marked
  - [ ] 4.2.2: Plan works without providing secret values
  - [ ] 4.3.1: Task execution role policy verified
  - [ ] 4.3.2: Least privilege verified
  - [ ] 4.4.1: Rotation handler check (N/A if not implemented)
  - [ ] 4.4.2: Recovery period set correctly

- [ ] Section 5 (E2E) verified
  - [ ] 5.1.1: Service deployment succeeds
  - [ ] 5.1.2: Container health check passes
  - [ ] 5.2.1: Runtime secret injection verified
  - [ ] 5.3.1: Service communication works

- [ ] Unit tests added under `tests/unit/`
- [ ] Integration tests added under `tests/integration/`
- [ ] `terraform validate` passes
- [ ] `terraform plan` shows expected changes (no unexpected modifications)
- [ ] Staging deployment successful
- [ ] Production deployment successful
- [ ] Monitoring alerts configured and verified

---

## 7. Rollback Procedure

If issues are detected after deployment:

### 7.1 Quick Rollback (Service Level)
```bash
# Force redeploy to previous task definition
aws ecs update-service \
  --cluster "${TF_VAR_name}-cluster" \
  --service "${TF_VAR_name}-auth" \
  --force-new-deployment \
  --task-definition "${PREVIOUS_TASK_DEFINITION_ARN}"
```

### 7.2 Full Terraform Rollback
```bash
# Revert terraform state to previous version
terraform state rm aws_secretsmanager_secret.*
terraform state rm aws_secretsmanager_secret_version.*

# Remove secrets from task definitions
terraform apply -var-file="terraform.tfvars"
```

### 7.3 Secrets Manager Cleanup (Optional)
```bash
# If secrets are no longer needed
aws secretsmanager delete-secret \
  --secret-id "$TEST_SECRET_NAME" \
  --force-delete-without-recovery
```
