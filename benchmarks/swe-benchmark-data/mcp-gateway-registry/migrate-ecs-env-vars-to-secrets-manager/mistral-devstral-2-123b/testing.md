# Testing Plan: Migrate ECS Environment Variables to AWS Secrets Manager

*Created: 2026-06-25*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview

### Scope of Testing
This testing plan verifies that the migration of sensitive ECS environment variables to AWS Secrets Manager works correctly, maintains zero-downtime service, and implements least-privilege access control.

### Prerequisites
- AWS CLI configured with access to us-east-1 region
- Terraform 1.5+ installed
- MCP Gateway Registry infrastructure provisioned
- Access to CloudWatch Logs and Secrets Manager

### Shared Variables
```bash
export AWS_REGION="us-east-1"
export ECS_CLUSTER="mcp-gateway-ecs-cluster"
export TASK_FAMILY="mcp-gateway-auth-server"
export TASK_DEFINITION="auth-server-migrated"
export SECRETS_MANAGER_ARN="arn:aws:secretsmanager:us-east-1:123456789012:secret:mcp-gateway-*"
export KMS_KEY_ID="alias/mcp-gateway-secrets"
```

## 1. Functional Tests

### 1.1 Terraform Apply Tests

**Test: Verify Terraform resources apply correctly**
```bash
terraform init
terraform plan -out=migration.tfplan
terraform show -json migration.tfplan | jq '.planned_values.root_module.resources[].type' | grep "aws_secretsmanager_secret"
```

**Expected Result:** List of new secrets manager resource types to be created
**Assertion:** Should contain expected secret resource types like `aws_secretsmanager_secret`

**Negative Case:** Should fail with error if secrets already exist
```bash
# Should fail with conflict
aws secretsmanager create-secret --name duplicate-secret-name
```

### 1.2 ECS Task Definition Tests

**Test: Verify ECS task definitions contain secrets block**
```bash
terraform show -json migration.tfplan | jq '.planned_values.root_module.resources[] | select(.type=="aws_ecs_task_definition") .values' | jq '.container_definitions' | jq '.[0] | .secrets' | grep "registry_secret_key"
```

**Expected Result:** Secrets block containing migrated secret ARNs
**Assertion:** Should include the new Secrets Manager ARN for migrated variables

### 1.3 IAM Policy Tests

**Test: Verify IAM policies include specific Secrets Manager ARNs**
```bash
terraform show -json migration.tfplan | jq '.planned_values.root_module.resources[] | select(.type=="aws_iam_policy") .values' | jq '.policy' | jq '.Statement[] | select(.Action[] | contains("secretsmanager:GetSecretValue")) | .Resource[]' | grep "secret-manager-arn"
```

**Expected Result:** Specific Secrets Manager ARNs instead of wildcards
**Assertion:** Should not contain wildcard `*` for Secrets Manager access

## 2. Backwards Compatibility Tests

**Test: Existing ECS services continue to function**
```bash
# Check existing services are still running
aws ecs list-services --cluster $ECS_CLUSTER
aws ecs describe-services --cluster $ECS_CLUSTER --services service-name
```

**Expected Result:** Existing services should remain in `ACTIVE` state
**Assertion:** Service status should be `ACTIVE` with desired running count

**Not Applicable:** No existing endpoints or CLI commands are being deprecated

## 3. UX Tests

**Test: CloudWatch logs do not expose secrets**
```bash
# Check CloudWatch logs for secret exposure
aws logs get-log-events --log-group-name "/ecs/mcp-gateway-auth-server" --log-stream-name-prefix "ecs" | jq '.events[].message' | grep -E "SECRET_.+=[^"]*" | should be-empty
```

**Expected Result:** No plaintext secrets in CloudWatch logs
**Assertion:** Log messages should not contain plaintext secret values

## 4. Deployment Surface Tests

### 4.1 Terraform Configuration Tests

**Test: Verify Terraform variables are properly scoped**
```bash
# Check for undefined variables
aws ssm get-parameters-by-path --path "/mcp-gateway/" --region $AWS_REGION | jq '.Parameters[] | select(.Name | contains("undefined"))' | should be-empty
```

**Expected Result:** No undefined SSM parameters
**Assertion:** All required parameters should be defined

### 4.2 Zero-Downtime Deployment Test

**Test: New task revision deploys with zero downtime**
```bash
# Start new task revision
aws ecs run-task --cluster $ECS_CLUSTER --launch-type FARGATE --task-definition $TASK_DEFINITION:2 --network-configuration "awsvpcConfiguration={subnets=[subnet-12345],securityGroups=[sg-12345],assignPublicIp=DISABLED}" --started-by "migration-test"

# Monitor health checks
aws ecs wait tasks-running --cluster $ECS_CLUSTER --tasks $(aws ecs list-tasks --cluster $ECS_CLUSTER | jq -r '.taskArns[-1]')
```

**Expected Result:** Task reaches `PENDING` → `RUNNING` state with healthy status
**Assertion:** Task health check should pass within expected time

## 5. End-to-End API Tests

**Test: Auth server responds correctly after migration**
```bash
# Test health endpoint
curl -s -o /dev/null -w "%{http_code}" https://$ALB_DNS/health | should equal "200"

# Test authentication flow
AUTH_RESPONSE=$(curl -s -X POST https://$ALB_DNS/auth -H "Content-Type: application/json" -d '{"username":"test","password":"test"}')
echo $AUTH_RESPONSE | jq -e '.token' | should not be-empty
```

**Expected Result:** Health endpoint returns 200, authentication returns valid token
**Assertion:** API should function normally after migration

## 6. Test Execution Checklist

- [ ] Section 1 (Functional) passes - Terraform apply and ECS/IAM resources validated
- [ ] Section 2 (Backwards Compat) verified - Existing services continue to function
- [ ] Section 3 (UX) verified - No secrets exposed in logs
- [ [ ] Section 4 (Deployment) verified - Zero-downtime deployment process works
- [ ] Section 5 (E2E) verified - APIs function correctly after migration
- [ ] Unit tests added under `tests/unit/` for new Terraform modules
- [ ] Integration tests added under `tests/integration/` for migration process
- [ ] `uv run pytest tests/` passes with no regressions

## 7. Rollback Testing

**Test: Rollback plan works correctly**
```bash
# Deploy new revision and verify it works
aws ecs update-service --cluster $ECS_CLUSTER --service auth-server --task-definition $TASK_DEFINITION:2
aws ecs wait services-stable --cluster $ECS_CLUSTER --services auth-server

# Rollback to previous revision
aws ecs update-service --cluster $ECS_CLUSTER --service auth-server --task-definition $TASK_DEFINITION:1
aws ecs wait services-stable --cluster $ECS_CLUSTER --services auth-server
```

**Expected Result:** Services should remain stable during rollback
**Assertion:** Rollback should complete without service interruption

## 8. Validation Scripts

```bash
#!/bin/bash
# validation-script.sh
# Run all validation tests for ECS secrets migration

# 1. Validate Terraform plan
echo "=== Validating Terraform Plan ==="
terraform plan -no-color > tf_plan_output.txt 2>&1
if [ $? -eq 0 ]; then
    echo "✓ Terraform plan passed"
else
    echo "✗ Terraform plan failed"
    exit 1
fi

# 2. Check for undefined variables
echo "=== Checking for Undefined Variables ==="
UNDEFINED=$(terraform plan 2>&1 | grep -i "undefined\|not set" || true)
if [ -z "$UNDEFINED" ]; then
    echo "✓ No undefined variables found"
else
    echo "✗ Undefined variables found: $UNDEFINED"
    exit 1
fi

# 3. Validate CloudWatch integration
echo "=== Validating CloudWatch Integration ==="
aws logs describe-log-groups --log-group-name-prefix "/ecs/mcp-gateway" | jq '.logGroups | length' | grep "> 0" >/dev/null
if [ $? -eq 0 ]; then
    echo "✓ CloudWatch log groups exist"
else
    echo "✗ CloudWatch log groups missing"
    exit 1
fi

echo "=== All Validation Tests Passed ==="
```

**Usage:** `chmod +x validation-script.sh && ./validation-script.sh`