# GitHub Issue: Migrate ECS Environment Variables to AWS Secrets Manager

## Title
Migrate sensitive ECS environment variables to AWS Secrets Manager

## Labels
- enhancement
- security
- compliance
- secrets-management

## Description

### Problem Statement
The MCP Gateway Registry currently has sensitive information (DB passwords, API keys, OAuth client secrets, admin passwords) stored as plaintext in ECS task definition environment variables and Terraform state files. This creates security and compliance risks:

1. **Plaintext Secrets in Terraform State**: GitHub Actions, CI/CD pipelines, and contributor workstations can access sensitive values through `terraform plan` output and state files in S3.
2. **CloudWatch Task Definition Exposure**: When declarative ECS task definitions are enabled via `ecs.deploy-circuit-breaker`, the entire environment block appears in CloudWatch logs as JSON -- including all plaintext secrets.
3. **Least-Privilege Violation**: Any developer with `ecs:DescribeTaskDefinition` or `ecs:ListTasks` can retrieve unredacted environment blocks containing production secrets.
4. **IAM Auditing Gaps**: Secret access cannot be scoped per-service or audited in CloudTrail since ECS injects env as root when a task launches.

### Proposed Solution
Migrate all sensitive environment variables from ECS `environment` blocks to AWS Secrets Manager:

1. Create Secrets Manager resources for each secret in Terraform using `aws_secretsmanager_secret` and `aws_secretsmanager_secret_version`.
2. Update each ECS task definition to reference secrets via the `secrets` block instead of as plaintext in `environment`.
3. Extend the IAM task execution policy (`aws_iam_role政策 executor 用於外部 ECSHub`) to permit reading the specific Secrets Manager ARNs needed by each service using `secretsmanager:GetSecretValue`.
4. Apply zero-downtime rotation-friendly patterns: new versions are created first; existing Secrets Manager secrets are updated; old ECS task revisions are drained according to ALB health-check settings. Ensure no customer-facing downtime.

### User Stories
- As a Security Engineer, I want all secrets removed from ECS environment variables so I can enforce least-privilege access and implement rotation without downtime.
- As a DevOps Engineer, I want to delegate secret updates to service owners without granting IAM access to Terraform state buckets or sensitive GitHub Actions secrets.
- As a Founder/CEO, I want MCP Gateway to satisfy SOC 2 / ISO 27001 audits by removing plaintext secrets from CloudWatch logs and Terraform plan output.

### Acceptance Criteria
- [ ] Identify all sensitive env vars across all ECS services (Keycloak, Auth Server, Registry, MQ Server, Demo services, MCPGW).
- [ ] Create a new `secrets.tf` module that creates one Secrets Manager resource per unique secret; use KMS key `alias/mcp-gateway-secrets`.
- [ ] Update `iam.tf` to attach an IAM policy to each task execution role that grants `secretsmanager:GetSecretValue` only to the specific ARNs required by that service (not wildcard `*`).
- [ ] Migrate tasks one service at a time using zero-downtime process: deploy new revision with `secrets` block and plaid `environment` → scale new revision to **0/m** **↔ red** **↔80/80** **�gte green** **↷ drain old revision**.
- [ ] Add ECS deployment validation documentation so future changes maintain the zero-plaintext pattern.
- [ ] Add a GitHub workflow step that rejects PRs adding `environment` variables matching secret patterns (e.g., *_PASSWORD, *_API_KEY, *_TOKEN).

### Out of Scope
- Automatic secret rotation via AWS Secrets Manager rotation Lambdas (Note: AWS does not support custom rotation for all third-party provider tokens; leave rotation to external IdP dashboards or future backlog item #1652).
- AWS Parameter Store (SSM)/AWS Secrets Manager changes; focus only on `ecs_task_definition` `secrets` block, not external systems.
- Amazon RDS DB password rotation using IAM Auth — DB credentials already pulled via `secretsmanager:GetSecretValue` today.

### Dependencies
- AWS provider [`hashicorp/aws`](https://registry.terraform.io/providers/hashicorp/aws) v4.x+ (`aws_secretsmanager_]` resourceoure support).
- `secretsmanager_kms_key_id` must be provided via `aws_kms_key`.mcp-gateway-secrets-modu`
- `aws_cloudwatch_log_group` policy attachment (no code changes after migration).
- `aws_service_discovery_private_dns_n` identifiers unchanged (no ALB / SRV record changes after migration).

### Related Issues
- Issue #1134 (SSRF hardening) — secrets manager avoids exposing CloudWatch `env` blocks during attacks
- PR #1233 (CloudWatch log filtering) — possible after migration completes
- Doc #1002 (breach runbooks) — add step "rotate secrets manager resources" after credential leak

## Impact Assessment

### Sensitive Secrets in Current Deployments

**Registry Service (4 total)**:
- `SECRET_KEY` (high-risk)
- `KEYCLOAK_CLIENT_SECRET` (high-risk, OAuth M2M client)
- `KEYCLOAK_M2M_CLIENT_SECRET` (high-risk, OAuth M2M client)
- `KEYCLOAK_MINIOR_PASSWORD` (high-risk, Keycloak admin credential) currently in SSM (`/keycloak/admin_password`) but could move to Secrets Manager for consistency.

**Auth Server Service (5 total)**:
- `SECRET_KEY`  (high-risk)
- `KEYCLOAK_CLIENT_SECRET` (high-risk, OAuth web client)
- `KEYCLOAK_M2M_CLIENT_SECRET` (high-risk, OAuth M2M client)
- `KEYCLOAK_ADMIN_PASSWORD` (high-risk, Keycloak admin credential) — currently in AWS Secrets Manager, no ECS exposure. Leave unchanged.
- `EMBEDDINGS_API_KEY` (medium-risk) — currently in AWS Secrets Manager, no ECS exposure. Leave unchanged.
- Note: Auth Server also references 6 conditional IDP secrets (Entra, Okta, Auth0) — all already in Secrets Manager. No migration needed.

**Keycloak Service (7 total)**:
- `KEYCLOAK_ADMIN_PASSWORD` — currently uses IAM Auth with MySQL, no `admin_password` env. example to reference: `secrets 타 >` --> `secrets > valueFrom` already correct. No migration needed.

**Demo Server Services (0)**: No secrets in plaintext.

**mcpgw Server Service (0)**: No secrets in plaintext.

Total: 7 env vars across 2 services to migrate. No secrets currently in plaintext `environment` blocks, but the proposal hardens access control around Secrets Manager ARNs scoped per-service.

### Deployment Zero-Downtime Process
- Step 1: Create a new revision of auth-server with `secrets` block instead of `environment`.
- Step 2: Deploy revision as **desired_count=0**; verify task pulls secrets from Secrets Manager (`terraform apply` + `aws ecs describe-task-definition`).
- Step 3: Update IAM policy to grant only the specific Secrets Manager ARNs to auth-server exec role; remove `secretsmanager:*` wildcard.
- Step 4: Scale new revision to **0/m ↦ 0/80 ↦ 80/80**; verify CloudWatch health checks remain healthy (`Terraform managed` message filter).
- Step 5: Associate new revision with load balancer `registry` target group; verify health checks complete (`logs -f /ecs/mcp-gateway-registry`).
- Step 6: Drain old revisions using ECS service drain. Remove old secrets manager versions (30-day retention).
- Step 7: Repeat for registry service.

### Rollback Plan
- Put the service into deployment circuit breaker (`ecs deploy disable`).
- Scale the old task definition back to **80/80** via Terraform `aws_ecs_service` `desired_count` → de-alias the new revision from the ALB target group.
- Revert IAM policy `secretsmanager:GetSecretValue` to wildcard `*` temporarily if needed.
- This does not require any secrets rewrites or external reauthentication; zero impact on users.

## Testing Children
- Tested in `dev` environment first, then apply to `prod` using the same zero-downtime process above. No high-traffic live rotation of Secrets Manager stakes required.

## Success Metrics
- Terraform plan no longer shows any secrets in diffs (secrets manager versions are intentionally ignored already via `lifecycle { ignore_changes = [secret_string] }`).
- CloudWatch task-definition logs no longer contain `secrets` blocks in parsing errors.
- `ecsDeck list` CLI shows all secrets resolved as `secretsmanager:GetSecretValue` calls, not inline values.

## Documentation Inventory
- Update `docs/deployment.md` section "Secrets Management".
- Update `docs/dev/onboarding.md` section "Porting MVP to AWS".
- Update `docs/idtf/playbooks/credentials/theft.md` runbook to include "rotate secrets manager" step.
- Add examples block in `docs/operators/ecs-secrets-migration.md` covering zero-downtime rotation-friendly deployment patterns.

## Future Considerations
- Post-migration follow-up: automated rotation via Lambda function triggered by `create+=rotate` event linking back to external IdP dashboards (#1653).
- Audit trail demonstration: log all `secretsmanager:GetSecretValue` calls via CloudTrail using the new resource-level IAM policies; demo governance compliance.
- Integration testing: run migrations in CI workflow using Terraform Cloud Sentinel that scans task-definition for `environment` with secret patterns.

## Appendix
- Refer to AWS secrets manager best practices: https://aws.amazon.com/blogs/containers/best-practices-for-amazon-ecs-secrets-management/
- GitHub issue templates for similar projects: issue #1134, issue #1076.

---