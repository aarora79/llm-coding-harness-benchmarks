# Testing Plan: Replace Keycloak Database Password with RDS IAM Authentication

*Created: 2026-06-25*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Overview

### Scope of Testing
Verify that the Keycloak ECS service authenticates to its Aurora MySQL database
using short-lived RDS IAM auth tokens instead of a static password: the IAM-auth DB
user works, no static `KC_DB_PASSWORD` is present in the rendered task definition,
TLS is enforced, the connection survives past the 15-minute token TTL, the
`rds-db:connect` permission is correctly scoped, and the local docker-compose
PostgreSQL dev stack is untouched.

### Prerequisites
- [ ] Terraform CLI installed; AWS credentials with permission to plan/apply the
      `terraform/aws-ecs/` stack (apply only in a non-prod test account).
- [ ] The custom Keycloak image (with the IAM entrypoint) built and pushed to ECR.
- [ ] `keycloak_iam` user created in the Aurora cluster (post-deploy step).
- [ ] `jq`, `aws` CLI v2, and a MySQL client (`mysql`) available on the runner.
- [ ] Network path into the VPC (ECS Exec or a bastion) for DB-side checks.

### Shared Variables
```bash
export TF_DIR="$(git rev-parse --show-toplevel)/benchmarks/swe-benchmark-data/mcp-gateway-registry/repo/terraform/aws-ecs"
export AWS_REGION="us-east-1"                 # match var.aws_region
export DB_IAM_USER="keycloak_iam"
export CLUSTER_ID="keycloak"
# Resolved after apply:
export RDS_ENDPOINT="$(aws rds describe-db-clusters --db-cluster-identifier "$CLUSTER_ID" \
  --query 'DBClusters[0].Endpoint' --output text --region "$AWS_REGION")"
export CLUSTER_RESOURCE_ID="$(aws rds describe-db-clusters --db-cluster-identifier "$CLUSTER_ID" \
  --query 'DBClusters[0].DbClusterResourceId' --output text --region "$AWS_REGION")"
```

> NOTE: Per the SWE skill constraints, this benchmark stops at design. The commands
> below are the executable plan a future implementer runs; they are NOT run as part
> of producing these artifacts.

## 1. Functional Tests

### 1.1 curl / HTTP Tests

**Not Applicable** - this change adds no HTTP endpoint. The only HTTP surface is
Keycloak's own `/health/ready`, used indirectly in E2E (Section 5) to confirm the
service started after connecting to the DB via IAM auth.

### 1.2 CLI Tests

#### 1.2.1 Terraform validates and plans cleanly
```bash
cd "$TF_DIR"
terraform init -backend=false
terraform validate
# Expect: "Success! The configuration is valid."
terraform plan -out=iam-auth.plan
# Expect: aws_rds_cluster.keycloak shows iam_database_authentication_enabled true,
#         a new aws_iam_role_policy.keycloak_task_rds_iam_policy, and the task
#         definition no longer containing a KC_DB_PASSWORD secret.
```

#### 1.2.2 Rendered task definition contains NO static DB password
```bash
cd "$TF_DIR"
terraform show -json iam-auth.plan \
  | jq -r '.. | objects | select(.containerDefinitions? != null) | .containerDefinitions' 2>/dev/null \
  | grep -i "KC_DB_PASSWORD" && echo "FAIL: KC_DB_PASSWORD still present" || echo "PASS: no KC_DB_PASSWORD"
# Expect: PASS
```

Alternative source-level gate (driver-agnostic):
```bash
cd "$TF_DIR"
grep -n "KC_DB_PASSWORD" keycloak-ecs.tf locals.tf \
  && echo "FAIL: KC_DB_PASSWORD referenced in task config" \
  || echo "PASS: KC_DB_PASSWORD removed from task config"
# Expect: PASS (the only allowed mention is in comments/docs, not the secrets array)
```

#### 1.2.3 IAM token generation works from a task-role identity
Run inside a running Keycloak task (via ECS Exec) or assume the task role locally:
```bash
aws rds generate-db-auth-token \
  --hostname "$RDS_ENDPOINT" --port 3306 \
  --region "$AWS_REGION" --username "$DB_IAM_USER" \
  | head -c 40; echo " ...(token, 15-min TTL)"
# Expect: a non-empty signed token string.
```

#### 1.2.4 IAM-auth connection succeeds with the token (TLS)
```bash
TOKEN="$(aws rds generate-db-auth-token --hostname "$RDS_ENDPOINT" --port 3306 \
  --region "$AWS_REGION" --username "$DB_IAM_USER")"
mysql -h "$RDS_ENDPOINT" -P 3306 -u "$DB_IAM_USER" --password="$TOKEN" \
  --ssl-ca=/opt/keycloak/conf/rds-ca.pem \
  --ssl-mode=VERIFY_CA \
  -e "SELECT CURRENT_USER();"
# Expect: returns keycloak_iam@% ; connection refused WITHOUT --ssl-mode (IAM requires TLS).
```

## 2. Backwards Compatibility Tests

#### 2.1 Local docker-compose (PostgreSQL) is unchanged
```bash
cd "$(git rev-parse --show-toplevel)/benchmarks/swe-benchmark-data/mcp-gateway-registry/repo"
git diff --name-only -- docker-compose.yml docker-compose.prebuilt.yml \
  docker-compose.podman.yml docker-compose.dhi.yml .env.example
# Expect: NO output (these files are out of scope and must not change).
grep -n "KC_DB_PASSWORD" docker-compose.yml
# Expect: KC_DB_PASSWORD still present for the local postgres stack (unchanged).
```

#### 2.2 Master user still works (rollback path intact)
```bash
mysql -h "$RDS_ENDPOINT" -P 3306 -u keycloak --password="$MASTER_PASSWORD" \
  --ssl-mode=REQUIRED -e "SELECT 1;"
# Expect: succeeds. The master credential is retained for break-glass/rollback.
```

#### 2.3 Keycloak schema/data unchanged after cutover
```bash
# Before and after cutover, row counts in core Keycloak tables match.
TOKEN="$(aws rds generate-db-auth-token --hostname "$RDS_ENDPOINT" --port 3306 \
  --region "$AWS_REGION" --username "$DB_IAM_USER")"
mysql -h "$RDS_ENDPOINT" -u "$DB_IAM_USER" --password="$TOKEN" --ssl-mode=VERIFY_CA \
  -e "SELECT COUNT(*) FROM keycloak.REALM; SELECT COUNT(*) FROM keycloak.USER_ENTITY;"
# Expect: same counts as the pre-change baseline (no data migration occurred).
```

## 3. UX Tests

#### 3.1 Login flow unaffected
- Navigate to the Keycloak login page served via the ALB/CloudFront.
- Log in with an existing realm user.
- **Expect:** login succeeds exactly as before; no user-visible change. The DB auth
  mechanism is invisible to end users.

#### 3.2 Operator error clarity (CLI/logs)
- Induce a wrong `rds-db:connect` ARN (e.g. wrong username) and start the task.
- **Expect:** Keycloak logs show `Access denied for user 'keycloak_iam'` and the
  task crash-loops with a clear, greppable signal. The entrypoint logs a token-gen
  success line (no token value) so operators can distinguish "token not generated"
  from "DB rejected token".

## 4. Deployment Surface Tests

### 4.1 Docker wiring
```bash
cd "$(git rev-parse --show-toplevel)/benchmarks/swe-benchmark-data/mcp-gateway-registry/repo"
# Entrypoint wrapper exists and is wired as ENTRYPOINT.
test -f docker/keycloak/docker-entrypoint-iam.sh && echo "PASS: entrypoint present"
grep -n "docker-entrypoint-iam.sh" docker/keycloak/Dockerfile \
  && echo "PASS: entrypoint wired"
# AWS CLI + CA truststore baked in.
grep -nE "awscli|global-bundle.pem|rds-truststore.p12" docker/keycloak/Dockerfile \
  && echo "PASS: token tooling + CA present"
```

Build-time smoke (optional, requires Docker):
```bash
docker build -f docker/keycloak/Dockerfile -t keycloak-iam-test .
docker run --rm --entrypoint sh keycloak-iam-test -c "aws --version && ls -l /opt/keycloak/conf/rds-truststore.p12"
# Expect: aws v2 prints; truststore file exists.
```

### 4.2 Terraform / ECS wiring
```bash
cd "$TF_DIR"
# IAM auth enabled on the cluster.
grep -n "iam_database_authentication_enabled\s*=\s*true" keycloak-database.tf \
  && echo "PASS: IAM auth enabled"
# checkov skip for CKV_AWS_162 removed.
grep -n "CKV_AWS_162" keycloak-database.tf \
  && echo "FAIL: stale checkov skip remains" || echo "PASS: skip removed"
# task-role rds-db:connect present and scoped (not wildcard).
grep -n "rds-db:connect" keycloak-ecs.tf && echo "PASS: connect granted"
terraform show -json iam-auth.plan \
  | jq -r '.. | strings | select(test("rds-db:.*:dbuser:.*/keycloak_iam"))' | head -1
# Expect: a concrete dbuser ARN ending in /keycloak_iam, NOT ":*".
# JDBC URL enforces TLS.
grep -n "sslMode=VERIFY_CA" keycloak-database.tf && echo "PASS: TLS in JDBC URL"
```

### 4.3 Helm / EKS wiring
**Not Applicable** - the IAM-auth change targets the ECS/Terraform deployment under
`terraform/aws-ecs/`. The repo's `charts/` Helm path does not provision the Aurora
cluster or the Keycloak ECS task and is out of scope for this change. (If a future
task brings Keycloak to EKS, IRSA + `rds-db:connect` would be the equivalent.)

### 4.4 Deploy and verify
```bash
cd "$TF_DIR"
terraform apply iam-auth.plan
# Post-apply: create the IAM DB user (idempotent), then force a new task deployment.
bash scripts/create-keycloak-iam-user.sh   # or post-deployment-setup.sh
aws ecs update-service --cluster <ecs-cluster> --service keycloak \
  --force-new-deployment --region "$AWS_REGION"
# Wait for the service to stabilize.
aws ecs wait services-stable --cluster <ecs-cluster> --services keycloak --region "$AWS_REGION"
# Expect: service reaches steady state; task did not crash-loop.
```

### 4.5 Rollback verification
```bash
# Re-point the task to the prior secret-based config + previous image and redeploy.
# The Aurora master_password is unchanged, so the old KC_DB_USERNAME/KC_DB_PASSWORD
# secret path works immediately.
aws ecs update-service --cluster <ecs-cluster> --service keycloak \
  --task-definition <previous-task-def-arn> --force-new-deployment --region "$AWS_REGION"
aws ecs wait services-stable --cluster <ecs-cluster> --services keycloak --region "$AWS_REGION"
# Expect: service returns to healthy on the old password path, proving rollback works.
```

## 5. End-to-End API Tests

#### 5.1 Full service start over IAM auth
1. Apply Terraform, build/push the image, create `keycloak_iam`, deploy.
2. Poll Keycloak readiness through the ALB:
```bash
for i in $(seq 1 20); do
  curl -fsS "https://<keycloak-host>/health/ready" && break || sleep 15
done
# Expect: {"status":"UP",...} once Keycloak connected to Aurora using an IAM token.
```
3. Obtain a realm token to prove Keycloak is fully operational (DB-backed):
```bash
curl -fsS -X POST "https://<keycloak-host>/realms/mcp-gateway/protocol/openid-connect/token" \
  -d "grant_type=client_credentials" \
  -d "client_id=$KEYCLOAK_M2M_CLIENT_ID" \
  -d "client_secret=$KEYCLOAK_M2M_CLIENT_SECRET" | jq -r '.access_token' | head -c 20
# Expect: a JWT prefix; confirms Keycloak read client config from the DB via IAM auth.
```

#### 5.2 CRITICAL: connection survives past the 15-minute token TTL
This is the make-or-break test identified in the LLD and review (the token/pool risk).
```bash
# Right after the service is healthy, record a baseline DB call through Keycloak.
curl -fsS "https://<keycloak-host>/health/ready" >/dev/null && echo "t0 OK"
# Wait beyond the token TTL (15 min) plus margin.
sleep 1200   # 20 minutes
# Exercise a DB-backed operation again (token used at boot is now long expired).
curl -fsS -X POST "https://<keycloak-host>/realms/mcp-gateway/protocol/openid-connect/token" \
  -d "grant_type=client_credentials" -d "client_id=$KEYCLOAK_M2M_CLIENT_ID" \
  -d "client_secret=$KEYCLOAK_M2M_CLIENT_SECRET" | jq -e '.access_token' >/dev/null \
  && echo "PASS: DB-backed op works 20 min after boot" \
  || echo "FAIL: connection/token expired - revisit pool/token-refresh strategy"
# Expect: PASS. A FAIL means the fixed-pool mitigation is insufficient and the JDBC
# credentials-provider / token-refresh approach (LLD Alt 2) must be adopted.
```

#### 5.3 CRITICAL: connection re-establishes after an induced drop post-TTL
```bash
# 20+ minutes after boot, force Aurora to drop Keycloak's connections, then verify
# Keycloak can open a FRESH connection (which needs a current token).
TOKEN="$(aws rds generate-db-auth-token --hostname "$RDS_ENDPOINT" --port 3306 \
  --region "$AWS_REGION" --username "$DB_IAM_USER")"
# Kill Keycloak's sessions as an admin (master user), simulating failover/scale reset.
mysql -h "$RDS_ENDPOINT" -u keycloak --password="$MASTER_PASSWORD" --ssl-mode=REQUIRED -e "
  SELECT CONCAT('KILL ',id,';') FROM information_schema.processlist
   WHERE user='$DB_IAM_USER';" 
# (execute the emitted KILL statements)
sleep 30
curl -fsS "https://<keycloak-host>/health/ready" \
  && echo "PASS: reconnected with a fresh token" \
  || echo "FAIL: reconnect used a stale token - token refresh required"
# Expect: PASS only if the design refreshes tokens for new connections. This test
# is the definitive check on the LLD's central open risk.
```

## 6. Test Execution Checklist
- [ ] Section 1 (Functional/CLI): terraform validate/plan clean; no `KC_DB_PASSWORD`
      in rendered task def; token generation + TLS IAM connect succeed
- [ ] Section 2 (Backwards Compat): docker-compose PostgreSQL stack unchanged; master
      user still works; Keycloak data unchanged
- [ ] Section 3 (UX): login flow unaffected; operator error signal is clear
- [ ] Section 4 (Deployment): Docker entrypoint/CLI/CA wired; IAM auth + scoped
      `rds-db:connect` + TLS present in Terraform; deploy stabilizes; rollback works
- [ ] Section 4.3 (Helm/EKS): marked Not Applicable
- [ ] Section 5 (E2E): service starts over IAM auth; **>15-min survival** and
      **post-TTL reconnect** tests PASS (or design revised per their failure)
- [ ] Unit/lint: `terraform fmt -check`, `terraform validate`, and `checkov` run
      clean (CKV_AWS_162 no longer skipped because IAM auth is now enabled)
- [ ] No regressions in the existing `terraform plan` for unrelated resources
