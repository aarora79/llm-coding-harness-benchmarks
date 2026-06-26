# Testing Plan: Migrate Sensitive ECS Environment Variables to AWS Secrets Manager

*Created: 2026-06-25*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview

### Scope of Testing
This plan covers testing of the Terraform changes to migrate sensitive environment variables from ECS task definition's `environment` block to the `secrets` block with AWS Secrets Manager.

### Prerequisites
- [ ] AWS account with ECS, Secrets Manager, and KMS access
- [ ] Terraform >= 1.2 installed
- [ ] AWS CLI configured with appropriate credentials
- [ ] Access to dev environment ECS cluster
- [ ] Reviewed `./lld.md` implementation details

### Shared Variables

```bash
export AWS_REGION="us-west-2"
export TF_VAR_name="mcp-gateway-test"
export TF_VAR_aws_region="${AWS_REGION}"

# Required for testing
export TF_VAR_auth0_enabled="true"
export TF_VAR_auth0_management_api_token="test-auth0-mgmt-token-$(date +%s)"
export TF_VAR_registry_static_token_auth_enabled="true"
export TF_VAR_registry_api_token="test-api-token-$(openssl rand -base64 24)"
export TF_VAR_federation_static_token_auth_enabled="true"
export TF_VAR_federation_static_token="test-federation-token-$(openssl rand -base64 24)"
export TF_VAR_ans_integration_enabled="true"
export TF_VAR_ans_api_key="test-ans-key-$(date +%s)"
export TF_VAR_ans_api_secret="test-ans-secret-$(openssl rand -base64 24)"
export TF_VAR_keycloak_admin_password="test-kc-admin-pass-$(openssl rand -base64 16)"
```

---

## 1. Functional Tests

### 1.1 Terraform Plan Validation

**Test 1.1.1: Verify No Plaintext Secrets in Environment**

```bash
cd /Users/prsinp/claude-code-multi-model/benchmarks/swe-benchmark-data/mcp-gateway-registry/repo/terraform/aws-ecs/
terraform plan -out=tfplan 2>&1 | tee plan-output.txt
```

**Expected Result:**
- No sensitive values shown in plan output (marked as `(sensitive)`)
- Plan succeeds without errors
- New `aws_secretsmanager_secret` resources appear in plan

**Assertions:**
```bash
# Verify plan contains new secrets
grep -E "aws_secretsmanager_secret\." plan-output.txt | wc -l
# Expected: >= 8 new secrets

# Verify no plaintext AUTH0_MANAGEMENT_API_TOKEN in environment
grep -E '"value".*test-auth0-mgmt-token' plan-output.txt
# Expected: Empty (no results)
```

---

**Test 1.1.2: Verify Secrets Block References**

```bash
terraform show -json tfplan | jq '.resource_changes[] | select(.type == "aws_ecs_task_definition") | .change.after.container_definitions' | grep -o '"name": *"[^"]*"' | sort | uniq
```

**Expected Result:**
- References to `valueFrom` with Secret Manager ARNs
- No `value` fields containing sensitive data

---

### 1.2 IAM Policy Validation

**Test 1.2.1: Task Execution Role Has Secrets Access**

```bash
cd terraform/aws-ecs/modules/mcp-gateway/
terraform plan -target=aws_iam_policy.ecs_secrets_access 2>&1 | tee iam-plan.txt
```

**Expected Result:**
- Policy includes `secretsmanager:GetSecretValue`
- Resource list includes new secret ARNs

**Assertion:**
```bash
grep -A 20 'ecs_secrets_access' iam-plan.txt | grep -E "(GetSecretValue|new-secret-arn)"
```

---

### 1.3 Secrets Manager Resource Validation

**Test 1.3.1: Conditional Secret Creation**

```bash
# Test with auth0_enabled = false
cat > /tmp/test-no-auth0.tfvars << 'EOF'
auth0_enabled = false
auth0_management_api_token = ""
EOF

terraform plan -var-file=/tmp/test-no-auth0.tfvars 2>&1 | grep -E "aws_secretsmanager_secret\.auth0"
# Expected: No results (secrets not created when feature disabled)

# Test with auth0_enabled = true
cat > /tmp/test-auth0.tfvars << 'EOF'
auth0_enabled = true
auth0_management_api_token = "test-token-12345"
EOF

terraform plan -var-file=/tmp/test-auth0.tfvars 2>&1 | grep -E "aws_secretsmanager_secret\.auth0"
# Expected: Shows resources to be created
```

---

## 2. Backwards Compatibility Tests

### 2.1 Existing Deployments Without Secrets

**Test 2.1.1: Empty Secret Handling**

```bash
# Create tfvars with empty secret values
cat > /tmp/test-empty.tfvars << 'EOF'
auth0_enabled = true
auth0_management_api_token = ""
registry_static_token_auth_enabled = false
federation_static_token_auth_enabled = false
ans_integration_enabled = false
keycloak_admin_password = ""
EOF

terraform plan -var-file=/tmp/test-empty.tfvars 2>&1
```

**Expected Result:**
- Plan succeeds
- No secret resources created (count = 0 for optional secrets)
- No errors for empty string values

---

### 2.2 Feature Flag Compatibility

**Test 2.2.1: Default Configuration (No Optional Features)**

```bash
cat > /tmp/test-minimal.tfvars << 'EOF'
# Use all defaults - no optional features enabled
EOF

terraform plan -var-file=/tmp/test-minimal.tfvars 2>&1 | tee minimal-plan.txt
```

**Expected Result:**
- Only required secrets (SECRET_KEY, KEYCLOAK_CLIENT_SECRET) appear
- No conditional secrets for disabled features
- All ECS services defined

---

### 2.3 Upgrade Without Downtime

**Test 2.3.1: Environment Variable Removal Does Not Force Recreation**

```bash
# First, check if task definition will be updated in-place
terraform plan -out=tfplan-test
terraform show -json tfplan-test | jq '.resource_changes[] | select(.type == "aws_ecs_task_definition") | {name: .name, actions: .change.actions}'
```

**Expected Result:**
- `"actions": ["create"]` for task definition (ECS creates new revision)
- Not `"actions": ["destroy", "create"]` (no recreation of service)

---

## 3. UX Tests

### 3.1 Terraform Output Clarity

**Test 3.1.1: Sensitive Values Hidden**

```bash
terraform plan 2>&1 | grep -E "('[a-zA-Z0-9+/]{20,}'|[A-Za-z0-9]{32,})"
# Look for sensitive-looking strings
```

**Expected Result:**
- No base64-encoded strings in plan output
- No 32+ character alphanumeric strings (potential tokens)
- Sensitive values show as `(sensitive value)`

---

**Test 3.1.2: Error Messages Clarity**

```bash
# Test with invalid configuration
cat > /tmp/test-invalid.tfvars << 'EOF'
auth0_enabled = true
# Missing required token when enabled
auth0_management_api_token = ""
EOF

terraform plan -var-file=/tmp/test-invalid.tfvars 2>&1 | tee error-output.txt
```

**Expected Result:**
- Clear error message if validation rules exist
- Or plan succeeds but secret not created (count = 0)

---

### 3.2 AWS Console Experience

**Test 3.2.1: Secrets Hidden in ECS Console**

After applying changes:

```bash
# Get task definition ARN
TASK_DEF_ARN=$(aws ecs describe-task-definition \
  --task-definition mcp-gateway-test-registry \
  --region $AWS_REGION \
  --query 'taskDefinition.taskDefinitionArn' \
  --output text)

# View container definition
aws ecs describe-task-definition \
  --task-definition $TASK_DEF_ARN \
  --region $AWS_REGION \
  --query 'taskDefinition.containerDefinitions[0].secrets' \
  --output table
```

**Expected Result:**
- Secrets show with `name` and `valueFrom` (ARN)
- No `value` field is present
- Values appear as "Hidden" in AWS Console

---

## 4. Deployment Surface Tests

### 4.1 Terraform Wiring

**Test 4.1.1: Module Variable Passing**

Verify root module passes variables correctly:

```bash
grep -A 3 'auth0_management_api_token' terraform/aws-ecs/main.tf
```

**Expected Result:**
- Variable passed from root to module
- No transformation of value

---

**Test 4.1.2: IAM Policy Permissions**

```bash
# After apply, verify IAM policy
aws iam get-policy-version \
  --policy-arn arn:aws:iam::123456789:policy/mcp-gateway-test-ecs-secrets \
  --version-id v1 \
  --query 'PolicyVersion.Document' \
  --output text | jq '.Statement[] | select(.Action[] | contains("secretsmanager:GetSecretValue"))'
```

**Expected Result:**
- Policy allows `secretsmanager:GetSecretValue`
- Resource ARNs include new secrets

---

### 4.2 Deploy and Verify

**Test 4.2.1: Terraform Apply Success**

```bash
terraform apply -auto-approve 2>&1 | tee apply-output.txt
```

**Expected Result:**
- Apply succeeds
- New secrets created
- IAM policy updated
- ECS services updated (new task definitions)

---

**Test 4.2.2: Secrets Accessible**

```bash
# List created secrets
aws secretsmanager list-secrets \
  --region $AWS_REGION \
  --query 'SecretList[].Name' \
  --output table | grep mcp-gateway-test
```

**Expected Result:**
- Secrets exist with names like `mcp-gateway-test-auth0-mgmt-token-*`
- Secrets have KMS key ID set

---

### 4.3 Rollback Verification

**Test 4.3.1: Rollback Procedure**

```bash
# Revert to previous terraform version
git checkout HEAD~1 terraform/

# Apply rollback
terraform apply -auto-approve 2>&1 | tee rollback-output.txt
```

**Expected Result:**
- Rollback succeeds
- Environment variables restored to task definition
- Secrets removed from ECS secrets block (but kept in Secrets Manager)
- Service redeploys with previous configuration

---

## 5. End-to-End Tests

### 5.1 Application Startup with Secrets

**Test 5.1.1: Auth Server Starts**

```bash
# Verify ECS service deployments
aws ecs describe-services \
  --cluster mcp-gateway-test-ecs-cluster \
  --services mcp-gateway-test-auth \
  --region $AWS_REGION \
  --query 'services[0].deployments[0].{status: status, runningCount: runningCount, desiredCount: desiredCount}'
```

**Expected Result:**
- Running count equals desired count
- No deployment errors
- Task shows `RUNNING` status

---

**Test 5.1.2: Verify Secrets Injected**

```bash
# Access container via ECS Exec (requires enable_execute_command)
aws ecs execute-command \
  --cluster mcp-gateway-test-ecs-cluster \
  --task $(aws ecs list-tasks --cluster mcp-gateway-test-ecs-cluster --service-name mcp-gateway-test-auth --region $AWS_REGION --query 'taskArns[0]' --output text) \
  --container auth-server \
  --interactive \
  --command "/bin/sh -c 'env | grep -E (AUTH0|REGISTRY|FEDERATION|ANS) | head -20'" \
  --region $AWS_REGION
```

**Expected Result:**
- Environment variables are present
- Values match secrets stored in Secrets Manager
- No errors about missing env vars

---

**Test 5.1.3: Application Functionality**

After deployment, test:

```bash
# Test federation (if configured)
curl -s http://mcp-gateway-test-alb-xxx.us-west-2.elb.amazonaws.com/api/v1/health \
  -H "Authorization: Bearer $(aws secretsmanager get-secret-value --region $AWS_REGION --secret-id mcp-gateway-test-registry-api-token --query 'SecretString' --output text)"
```

**Expected Result:**
- HTTP 200 response
- Health check passes
- No authentication errors due to secret injection

---

### 5.2 Multi-Service Secret Access

**Test 5.2.1: Registry and Auth Server Share Secrets**

Verify both services can access shared secrets:

```bash
# For each service, verify task role has access
for SERVICE in mcp-gateway-test-auth mcp-gateway-test-registry; do
  TASK_DEF=$(aws ecs describe-services \
    --cluster mcp-gateway-test-ecs-cluster \
    --services $SERVICE \
    --region $AWS_REGION \
    --query 'services[0].taskDefinition' --output text)

  EXEC_ROLE=$(aws ecs describe-task-definition \
    --task-definition $TASK_DEF \
    --region $AWS_REGION \
    --query 'taskDefinition.executionRoleArn' --output text)

  echo "Service: $SERVICE, Task Execution Role: $EXEC_ROLE"
done
```

**Expected Result:**
- Both services have task execution roles
- Roles have Secrets Manager access policy attached

---

### 5.3 Secret Update Flow

**Test 5.3.1: Secret Rotation**

```bash
# Update a secret value
NEW_TOKEN="rotated-token-$(openssl rand -base64 16)"

aws secretsmanager put-secret-value \
  --secret-id $(aws secretsmanager list-secrets --region $AWS_REGION --query "SecretList[?contains(Name, 'mcp-gateway-test-registry-api-token')].ARN | [0]" --output text) \
  --secret-string "$NEW_TOKEN" \
  --region $AWS_REGION

# Force new deployment
aws ecs update-service \
  --cluster mcp-gateway-test-ecs-cluster \
  --service mcp-gateway-test-registry \
  --force-new-deployment \
  --region $AWS_REGION
```

**Expected Result:**
- Secret updated in Secrets Manager
- New ECS deployment triggered
- Application uses rotated secret after restart

---

## 6. Test Execution Checklist

- [ ] Section 1.1 (Terraform Plan Validation) passes
- [ ] Section 1.2 (IAM Policy Validation) verified
- [ ] Section 1.3 (Conditional Secret Creation) verified
- [ ] Section 2.1 (Empty Secret Handling) verified
- [ ] Section 2.2 (Feature Flag Compatibility) verified
- [ ] Section 2.3 (Upgrade Without Downtime) verified
- [ ] Section 3.1 (Sensitive Values Hidden) verified
- [ ] Section 3.2 (AWS Console Experience) verified
- [ ] Section 4.1 (Module Variable Passing) verified
- [ ] Section 4.2 (Terraform Apply) passes
- [ ] Section 4.3 (Rollback Procedure) tested
- [ ] Section 5.1 (Application Startup) passes
- [ ] Section 5.2 (Multi-Service Access) verified
- [ ] Section 5.3 (Secret Rotation) tested (optional)

---

## Appendix A: Manual Verification Commands

### Verify All Secrets Created

```bash
aws secretsmanager list-secrets \
  --region $AWS_REGION \
  --filters Key=name,Values=mcp-gateway-test \
  --query 'SecretList[?Tags[?Key==`Name` && Value==`mcp-gateway-test`]].{Name: Name, ARN: ARN, LastChangedDate: LastChangedDate}' \
  --output table
```

### Verify Secret Values

```bash
# Example: Get registry api token
aws secretsmanager get-secret-value \
  --secret-id $(aws secretsmanager list-secrets --region $AWS_REGION --query "SecretList[?contains(Name, 'mcp-gateway-test-registry-api-token')].Name | [0]" --output text) \
  --query 'SecretString' \
  --region $AWS_REGION
```

### Verify ECS Task Definition

```bash
aws ecs describe-task-definition \
  --task-definition mcp-gateway-test-registry \
  --region $AWS_REGION \
  --query 'taskDefinition.containerDefinitions[0].{Environment: environment, Secrets: secrets}' \
  --output json
```

---

## Appendix B: Failed Test Recovery

### If Task Fails to Start with "Cannot pull secret"

1. Check IAM policy attached to task execution role
2. Verify secret ARN is correct
3. Check KMS key grants
4. Review VPC endpoints (if using private subnets)

### If Application Cannot Find Environment Variable

1. Verify secret name in `secrets` block matches expected env var name
2. Check container logs for "Secret not found" errors
3. Verify secret value is not empty in Secrets Manager

### If Terraform Plan Shows Unintended Changes

1. Run `terraform state list` to identify drift
2. Use `terraform state show` to inspect actual state
3. Consider using `terraform import` for manually created secrets
