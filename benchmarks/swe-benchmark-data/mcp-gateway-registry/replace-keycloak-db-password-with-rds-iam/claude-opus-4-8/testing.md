# Testing Plan: Replace Keycloak Database Password with RDS IAM Authentication

*Created: 2026-07-23*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview

### Scope of Testing
This change is entirely infrastructure/configuration (Terraform, Docker, IAM). It adds a feature flag (`keycloak_db_iam_auth_enabled`) that switches the Keycloak ECS task's Aurora MySQL connection from a static password to RDS IAM authentication. Testing verifies: (1) the flag-off path is byte-for-byte unchanged (backwards compatibility), (2) the flag-on path enables IAM auth on the cluster, grants `rds-db:connect`, drops `KC_DB_PASSWORD`, and boots Keycloak successfully against Aurora, and (3) rollback to password auth works. There is no application HTTP endpoint or CLI change.

### Prerequisites
- [ ] AWS credentials with permission to run `terraform plan/apply` against the `terraform/aws-ecs` stack.
- [ ] Terraform >= the version pinned in the module, initialized (`terraform init`) in `terraform/aws-ecs/`.
- [ ] `aws` CLI v2, `jq`, and a `mysql` client available on the operator/CI host.
- [ ] The custom Keycloak image built from `docker/keycloak/Dockerfile` (with the AWS Advanced JDBC Wrapper) pushed to a private ECR repo, for the flag-on tests.
- [ ] Network reachability to the Aurora endpoint for bootstrap (an in-VPC one-off task, ECS Exec session, or a bastion) - the DB is in private subnets.

### Shared Variables
```bash
export AWS_REGION="${AWS_REGION:-us-east-1}"
export TF_DIR="terraform/aws-ecs"
export CLUSTER_ID="keycloak"
export DB_USER_IAM="keycloak_iam"
export KC_LOG_GROUP="/ecs/keycloak"
# Custom image URI for the flag-on path (must include the JDBC wrapper):
export KC_IMAGE_URI="<account>.dkr.ecr.${AWS_REGION}.amazonaws.com/keycloak:1.24.4"
```

## 1. Functional Tests

### 1.1 curl / HTTP Tests
**Not Applicable** - This change adds no HTTP endpoints. The only HTTP surface is the Keycloak readiness probe, which is exercised indirectly by the deployment tests in Section 4 (`GET :9000/health/ready` must report ready once Keycloak connects to Aurora via IAM auth).

### 1.2 CLI Tests

The observable "commands" are Terraform apply, the bootstrap SQL, and the IAM auth-token generation. Each is validated below.

#### 1.2.1 Terraform variable exists and defaults to false
```bash
cd "$TF_DIR"
grep -A4 'variable "keycloak_db_iam_auth_enabled"' variables.tf
# Assert: type = bool, default = false
```
Expected: the block is present with `default = false`.

#### 1.2.2 Bootstrap script is idempotent and creates an IAM-plugin user
Run the bootstrap (mechanism per LLD: in-VPC one-off task or ECS Exec). After it completes, verify the user exists and uses the IAM plugin:
```bash
# From an in-VPC host, authenticated as the master user over verified TLS:
mysql --host="$DB_HOST" --user="$DB_MASTER_USER" --password="$DB_MASTER_PW" \
  --ssl-mode=VERIFY_IDENTITY --ssl-ca=/etc/ssl/certs/rds-global-bundle.pem \
  -e "SELECT user, plugin FROM mysql.user WHERE user='${DB_USER_IAM}';"
```
Expected output:
```
+--------------+-------------------------+
| user         | plugin                  |
+--------------+-------------------------+
| keycloak_iam | AWSAuthenticationPlugin |
+--------------+-------------------------+
```
Re-run the bootstrap script; expected: exits 0 with no error (`CREATE USER IF NOT EXISTS`), user unchanged. Assert the script never prints the master password to stdout/logs.

#### 1.2.3 IAM auth token can be generated and used
Confirm the task identity can mint a token and log in (run from within the task via ECS Exec, or a host with the task role):
```bash
TOKEN=$(aws rds generate-db-auth-token \
  --hostname "$DB_HOST" --port 3306 --username "$DB_USER_IAM" --region "$AWS_REGION")
mysql --host="$DB_HOST" --user="$DB_USER_IAM" --password="$TOKEN" \
  --ssl-mode=VERIFY_IDENTITY --ssl-ca=/etc/ssl/certs/rds-global-bundle.pem \
  -e "SELECT 1;"
```
Expected: `1` returned. Negative case - a non-TLS attempt is rejected:
```bash
mysql --host="$DB_HOST" --user="$DB_USER_IAM" --password="$TOKEN" --ssl-mode=DISABLED -e "SELECT 1;"
# Expected: ERROR - access denied / TLS required (RDS refuses non-TLS IAM auth)
```

## 2. Backwards Compatibility Tests

The flag defaults to `false`, so an existing deployment that does not opt in must be completely unaffected.

#### 2.1 Flag-off produces a zero-diff plan on DB/task resources
On an existing deployment (state present), with no tfvars change:
```bash
cd "$TF_DIR"
terraform plan -out=tfplan.baseline
terraform show -json tfplan.baseline | jq '
  .resource_changes[]
  | select(.address | test("aws_rds_cluster.keycloak|aws_ecs_task_definition.keycloak|aws_ssm_parameter.keycloak_database_url|aws_secretsmanager_secret"))
  | {address, actions: .change.actions}'
```
Expected: every listed resource shows `["no-op"]`. Introducing the new variable and conditional expressions must not change the rendered plan when the flag is `false`.

#### 2.2 Flag-off task definition still contains KC_DB_PASSWORD
```bash
terraform show -json tfplan.baseline | jq -r '
  .planned_values.root_module.resources[]
  | select(.address=="aws_ecs_task_definition.keycloak")
  | .values.container_definitions' | jq -r '.[0].secrets[].name'
```
Expected includes: `KEYCLOAK_ADMIN`, `KEYCLOAK_ADMIN_PASSWORD`, `KC_DB_URL`, `KC_DB_USERNAME`, `KC_DB_PASSWORD`. The JDBC URL SSM value must remain `jdbc:mysql://<endpoint>:3306/keycloak` (no `aws-wrapper` scheme).

#### 2.3 Flag-off keeps the rotation stack present
```bash
terraform state list | grep -E 'rds_rotation|secret_rotation'
```
Expected: the rotation Lambda and `aws_secretsmanager_secret_rotation.keycloak_db_secret` are still in state (not gated off).

#### 2.4 Flag-off cluster has IAM auth disabled
```bash
aws rds describe-db-clusters --db-cluster-identifier "$CLUSTER_ID" --region "$AWS_REGION" \
  --query 'DBClusters[0].IAMDatabaseAuthenticationEnabled'
```
Expected: `false`.

## 3. UX Tests

**Not Applicable to end-user UI** - no web UI, CLI output, or user-facing error surface changes; the Keycloak login page and themes are untouched.

Operator-facing UX (Terraform) is validated as a guardrail test:

#### 3.1 Misconfiguration guardrail: flag on + stock image is rejected at plan time
Per the reviews, a `precondition` must fail the plan when `keycloak_db_iam_auth_enabled = true` and `keycloak_image_uri` is the stock public default:
```bash
cd "$TF_DIR"
terraform plan \
  -var 'keycloak_db_iam_auth_enabled=true' \
  -var 'keycloak_image_uri=quay.io/keycloak/keycloak:25.0'
```
Expected: plan FAILS with a clear precondition error instructing the operator to supply a custom image URI that bundles the AWS Advanced JDBC Wrapper. (If the precondition is not yet implemented, this test documents the required behavior and must be marked failing until added.)

## 4. Deployment Surface Tests

### 4.1 Docker wiring
Verify the custom image bundles the JDBC wrapper and (per review) sets the driver correctly:
```bash
grep -n 'aws-advanced-jdbc-wrapper' docker/keycloak/Dockerfile
grep -n 'KC_DB_DRIVER' docker/keycloak/Dockerfile   # expected present before kc.sh build (post-review fix)
# Inspect the built image:
docker run --rm --entrypoint ls "$KC_IMAGE_URI" /opt/keycloak/providers/ | grep aws-advanced-jdbc-wrapper
```
Expected: the JAR is present in `/opt/keycloak/providers/`. Confirm the JAR download is verified by checksum (post-review): `grep -n 'sha256' docker/keycloak/Dockerfile`.

### 4.2 Terraform / ECS wiring (flag on)
Apply in a non-production environment with the flag on and the custom image:
```bash
cd "$TF_DIR"
terraform apply \
  -var 'keycloak_db_iam_auth_enabled=true' \
  -var "keycloak_image_uri=${KC_IMAGE_URI}"
```
Assertions after apply:
```bash
# a) Cluster has IAM auth enabled
aws rds describe-db-clusters --db-cluster-identifier "$CLUSTER_ID" --region "$AWS_REGION" \
  --query 'DBClusters[0].IAMDatabaseAuthenticationEnabled'   # expected: true

# b) Task role has rds-db:connect scoped to the resource id + user
ROLE="keycloak-task-role-${AWS_REGION}"
aws iam list-role-policies --role-name "$ROLE" | jq -r '.PolicyNames[]' | grep rds-connect
aws iam get-role-policy --role-name "$ROLE" --policy-name keycloak-task-rds-connect-policy \
  | jq -r '.PolicyDocument.Statement[] | select(.Action[]?=="rds-db:connect" or .Action=="rds-db:connect") | .Resource'
# expected: arn:aws:rds-db:<region>:<acct>:dbuser:<cluster-resource-id>/keycloak_iam

# c) Task definition no longer has KC_DB_PASSWORD, and JDBC URL uses the wrapper scheme
aws ecs describe-task-definition --task-definition keycloak --region "$AWS_REGION" \
  | jq -r '.taskDefinition.containerDefinitions[0].secrets[].name'   # expected: no KC_DB_PASSWORD
aws ssm get-parameter --name /keycloak/database/url --with-decryption --region "$AWS_REGION" \
  --query 'Parameter.Value' --output text
# expected: jdbc:aws-wrapper:mysql://<endpoint>:3306/keycloak?wrapperPlugins=iam&sslMode=VERIFY_IDENTITY
```

### 4.3 Helm / EKS wiring
**Not Applicable** - the repo has no Helm chart or EKS deployment for Keycloak; IAM auth targets only the ECS + Aurora path (confirmed in scope with the requester).

### 4.4 Deploy and verify (Keycloak boots on IAM auth)
```bash
# Force a new deployment onto the updated task definition
aws ecs update-service --cluster keycloak --service keycloak --force-new-deployment --region "$AWS_REGION"
# Wait for the service to stabilize
aws ecs wait services-stable --cluster keycloak --services keycloak --region "$AWS_REGION"
# Confirm the running task is healthy and connected to the DB
aws logs tail "$KC_LOG_GROUP" --since 10m --region "$AWS_REGION" | grep -Ei 'started|listening|health'
aws logs tail "$KC_LOG_GROUP" --since 10m --region "$AWS_REGION" | grep -Ei 'access denied|ClassNotFound' && echo "FAIL: DB/driver error" || echo "OK: no DB/driver errors"
```
Expected: service stabilizes, Keycloak reports started, no "Access denied" or `ClassNotFoundException`. RDS `DatabaseConnections` remains stable:
```bash
aws cloudwatch get-metric-statistics --namespace AWS/RDS --metric-name DatabaseConnections \
  --dimensions Name=DBClusterIdentifier,Value=$CLUSTER_ID --start-time "$(date -u -d '15 min ago' +%FT%TZ)" \
  --end-time "$(date -u +%FT%TZ)" --period 60 --statistics Average --region "$AWS_REGION"
```

### 4.5 Rollback verification
```bash
cd "$TF_DIR"
terraform apply -var 'keycloak_db_iam_auth_enabled=false'
aws ecs update-service --cluster keycloak --service keycloak --force-new-deployment --region "$AWS_REGION"
aws ecs wait services-stable --cluster keycloak --services keycloak --region "$AWS_REGION"
# Assert password auth resumed:
aws ecs describe-task-definition --task-definition keycloak --region "$AWS_REGION" \
  | jq -r '.taskDefinition.containerDefinitions[0].secrets[].name' | grep KC_DB_PASSWORD  # expected: present
aws rds describe-db-clusters --db-cluster-identifier "$CLUSTER_ID" --region "$AWS_REGION" \
  --query 'DBClusters[0].IAMDatabaseAuthenticationEnabled'  # may stay true (harmless) or return false
```
Expected: Keycloak stabilizes on password auth again. Note (per Circuit/Cipher): confirm the tfvars password still matches the cluster (rotation-drift check) before relying on rollback.

## 5. End-to-End API Tests

A single end-to-end scenario spanning the DB switch and the auth workflow:

1. With the flag on and Keycloak healthy (Section 4.4), obtain an admin token to prove the realm and DB-backed state are intact:
```bash
export KEYCLOAK_URL="https://<keycloak-domain>"
curl -s -X POST "$KEYCLOAK_URL/realms/master/protocol/openid-connect/token" \
  -d "grant_type=password" -d "client_id=admin-cli" \
  -d "username=$KEYCLOAK_ADMIN" -d "password=$KEYCLOAK_ADMIN_PASSWORD" | jq -r '.access_token' | head -c 20
```
Expected: a JWT prefix is returned (Keycloak served an auth request backed by an IAM-authenticated DB connection).

2. Read a persisted realm to confirm DB reads work under IAM auth:
```bash
ADMIN_TOKEN=$(curl -s -X POST "$KEYCLOAK_URL/realms/master/protocol/openid-connect/token" \
  -d "grant_type=password" -d "client_id=admin-cli" \
  -d "username=$KEYCLOAK_ADMIN" -d "password=$KEYCLOAK_ADMIN_PASSWORD" | jq -r '.access_token')
curl -s -H "Authorization: Bearer $ADMIN_TOKEN" "$KEYCLOAK_URL/admin/realms/mcp-gateway" | jq -r '.realm'
```
Expected: `mcp-gateway`. This confirms the existing realm survives the auth-mechanism switch (no data migration, only connection auth changed).

3. Token-longevity check: leave the service running > 15 minutes (past IAM token expiry), then repeat step 1. Expected: still succeeds, proving the JDBC wrapper refreshes tokens on new connections without a restart.

## 6. Test Execution Checklist
- [ ] Section 1 (Functional): variable/default, bootstrap idempotency + IAM plugin user, token generation + TLS-required negative case
- [ ] Section 2 (Backwards Compat): flag-off zero-diff plan, `KC_DB_PASSWORD` retained, rotation stack retained, cluster IAM auth disabled
- [ ] Section 3 (UX): misconfig precondition rejects flag-on + stock image
- [ ] Section 4 (Deployment): Docker JAR/driver/checksum, IAM-auth enabled, `rds-db:connect` scoped policy, `KC_DB_PASSWORD` dropped, wrapper JDBC URL, Keycloak boots clean, rollback restores password auth
- [ ] Section 5 (E2E): admin token + realm read under IAM auth, and > 15-min token-refresh check
- [ ] Unit tests / static checks added: `terraform validate`, `terraform fmt -check`, `checkov` on the module (CKV_AWS_162 handled honestly per review), `bash -n scripts/bootstrap-iam-db-user.sh`
- [ ] `terraform plan` for both flag states reviewed and matches expectations
- [ ] No regression in the default (flag-off) deployment
