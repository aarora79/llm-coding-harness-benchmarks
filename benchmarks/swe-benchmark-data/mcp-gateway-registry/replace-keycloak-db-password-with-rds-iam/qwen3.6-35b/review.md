# Expert Review: Replace Keycloak DB Password with RDS IAM Authentication

*Created: 2026-07-22*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*
*Model: qwen3.6-35b*

## Reviewers

| Role | Reviewer | Focus |
|------|----------|-------|
| Backend Engineer | Byte | API design, data models, business logic, performance |
| SRE/DevOps Engineer | Circuit | Deployment, monitoring, scaling, infrastructure |
| Security Engineer | Cipher | AuthN/AuthZ, validation, OWASP, data protection |
| SMTS (Overall) | Sage | Architecture, code quality, maintainability |

---

## SRE/DevOps Engineer (Circuit)

### Strengths

- Correct identification that RDS Proxy `auth_scheme` for MySQL must be `"DEFAULT"` (not `"AWS_IAM"`, which is PostgreSQL/SQL Server only). The LLD correctly calls out this common pitfall.
- The feature flag `KEYCLOAK_DB_IAM_AUTH_ENABLED` with default `false` provides a fast rollback path -- operators can flip the flag and force-new-deploy without reverting Terraform.
- Good use of `null_resource` with `local-exec` for the one-time MySQL IAM user creation, integrating a manual step into the Terraform workflow.
- The conditional secrets block using `concat()` and `count` on the IAM policy is clean Terraform idiomatic code.
- The LLD correctly distinguishes between `keycloak_task_exec_role` (SSM/Secrets Manager read) and `keycloak_task_role` (where the `rds-db:connect` policy attaches).
- The rollout plan includes a maintenance window acknowledgment for the RDS Proxy re-creation.

### Concerns

1. **RC01: RDS Proxy `require_tls` changed from `false` to `true`.** The LLD sets `require_tls = true` on the RDS Proxy (keycloak-database.tf). This is a good security change, but if existing clients (including Keycloak) connect without TLS, the connection will fail. The LLD adds SSL parameters to the SSM `KC_DB_URL` (`ssl=true&sslmode=require`), which should handle this, but it should be explicitly verified. If the Aurora cluster does not have an SSL certificate configured, `require_tls = true` could cause a permanent failure. The LLD should note that Aurora's auto-generated SSL certificate must be in place.

2. **RC02: The `null_resource` for MySQL IAM user runs with `sleep 30`.** The LLD uses a `sleep 30` in the `local-exec` provisioner to wait for the cluster to be available. This is fragile -- the sleep may be too short if the cluster is still modifying, or too long if it's ready sooner. A better approach would be to use a `depends_on` on a resource that signals readiness, or use a retry loop with `local-exec`. The LLD should use a retry-based approach:

   ```bash
   for i in $(seq 1 10); do
     mysql -h <endpoint> -u <user> -p<pass> -e "SELECT 1" && break
     sleep 5
   done
   ```

3. **RC03: The AWS CLI install fallback chain is fragile.** The LLD tries `pip3`, `pip`, `apk`, `apt` in order. If none succeed (e.g., the image has no package manager and no network), the wrapper continues with `true`, and Keycloak starts without `KC_DB_PASSWORD`, causing a connection failure. The LLD should either: (a) use a more reliable installation method, or (b) make the failure fatal (remove the `|| true` fallback) so ECS restarts the task with a clear error.

### Recommendations

- Verify Aurora SSL certificate availability before changing `require_tls` to `true`.
- Replace the `sleep 30` in the `null_resource` with a retry loop.
- Consider making AWS CLI install failure fatal (remove `|| true`) to surface the error to ECS for restart.

### Verdict
**APPROVED WITH CHANGES** -- 0 critical, 3 moderate.

---

## Security Engineer (Cipher)

### Strengths

- Excellent security improvement: eliminating static database credentials from the entire stack -- no more password in Secrets Manager, no more rotation Lambda, no more env var leakage at rest.
- The `rds-db:connect` IAM permission is correctly scoped to the specific DB user ARN (`arn:aws:rds-db:{region}:{account}:dbuser:{cluster}/{username}`), following least privilege.
- The `require_tls = true` change on the RDS Proxy is the right security move for defense-in-depth.
- Good acknowledgment that the `master_password` must remain on the cluster and a plan to rotate it in a future phase.
- The checkov skip removal (`CKV_AWS_162`) addresses a documented compliance gap.

### Concerns

1. **SC01: IAM token briefly exposed in the ECS task environment.** The entrypoint wrapper does `export KC_DB_PASSWORD=$TOKEN`, which means the token is visible in the container's environment. ECS task metadata, `ecs describe-tasks`, and process listings can reveal the environment. While the token expires in 15 minutes, this is still a transient exposure. A better approach would be to write the token to a file and have Keycloak read it, but Keycloak's JDBC configuration only supports the `KC_DB_PASSWORD` env var. This exposure is unavoidable with the stock Keycloak image and is an acceptable trade-off given the 15-minute TTL. The LLD acknowledges this.

2. **SC02: The `null_resource` with `local-exec` passes the master password on the command line.** The SQL provisioner passes ` -p"${var.keycloak_database_password}"` on the MySQL command line, which may appear in process listings or Terraform plan output. The LLD should note that this is only run during `terraform apply` (a one-time or infrequent operation) and the master password is already in Terraform state. An alternative would be to read the password from a file or stdin.

3. **SC03: The inline shell command uses `set -e` but the AWS CLI install has `|| true`.** The `|| true` on the AWS CLI install fallback means a silent failure is possible -- the install fails, `true` succeeds, and Keycloak starts without `KC_DB_PASSWORD`. The LLD should use a more robust approach: capture the install result and fail explicitly if it fails.

### Recommendations

- Note that `master_password` on the MySQL command line in the `null_resource` is visible in Terraform plan output; consider using a file-based approach for the one-time SQL step.
- Make the AWS CLI install failure fatal rather than silently continuing.
- The env var token exposure is acknowledged as unavoidable with the stock Keycloak image.

### Verdict
**APPROVED WITH CHANGES** -- 0 critical, 3 moderate.

---

## SMTS (Sage)

### Strengths

- The design is comprehensive and well-structured, covering all aspects of the change: Terraform, ECS, IAM, and rollback.
- The alternatives considered section shows thoughtful analysis of three distinct approaches with clear pros/cons.
- The feature flag with default `false` is the right call for a critical infrastructure change -- it prevents accidental production disruption.
- The LLD correctly identifies and addresses the MySQL-specific RDS Proxy `auth_scheme` pitfall (a common error that would cause Terraform apply to fail).
- The conditional secrets block using `concat()` is clean Terraform code that handles both IAM and password auth modes from a single codebase.
- The LLD acknowledges the 15-minute token TTL limitation and proposes acceptable mitigation (ECS Fargate restart patterns).

### Concerns

1. **TC01: The conditional secrets block may have ordering issues with `aws_secretsmanager_secret.keycloak_db_secret` removal.** When `keycloak_db_iam_auth_enabled = true`, the `concat()` still references `aws_secretsmanager_secret.keycloak_db_secret.arn` for `KC_DB_USERNAME` and `KC_DB_URL`. If the secret is also removed (Step 8), the `KC_DB_USERNAME` reference will fail. The LLD should either: (a) keep `KC_DB_USERNAME` as a hardcoded value (it is just the username string, not a secret), or (b) keep the secret but remove only `KC_DB_PASSWORD` from the secrets block. Option (a) is simpler and safer:

   ```hcl
   {
     name  = "KC_DB_USERNAME"
     value = var.keycloak_database_username  # Just the username, not a secret
   }
   ```

2. **TC02: The ECS task command uses `aws rds generate-db-auth-token` piped to `while read`.** The command uses `aws rds generate-db-auth-token ... | while read -r token; do export KC_DB_PASSWORD="$token"; done`. This pipes the token through a subshell (`while read`), and the `export` inside the subshell does NOT affect the parent shell. The `exec kc.sh start` after the `while` loop will NOT have `KC_DB_PASSWORD` set. This is a critical bug. The fix is to use a command substitution or process substitution:

   ```bash
   export KC_DB_PASSWORD=$(aws rds generate-db-auth-token \
     --hostname <endpoint> \
     --port 3306 \
     --username <user> \
     --region <region>)
   ```

   The `$(...)` command substitution runs in a subshell but the result is captured and exported in the parent shell, which is the correct behavior.

3. **TC03: The `null_resource` with `local-exec` and MySQL client is a deployment risk.** The `local-exec` provisioner runs on the machine that executes Terraform (the developer's laptop or CI server), not inside the VPC. The MySQL client needs network access to the Aurora cluster, which requires the Terraform runner to be inside the VPC or have a VPN. If the Terraform runner is outside the VPC, the `local-exec` will fail. An alternative would be to run the SQL as a separate pre-apply step (documented as a manual operation) or use an AWS Lambda with `null_resource` and `local-exec` running from within the VPC. The LLD should note this constraint and provide an alternative.

4. **TC04: No handling for the case where the MySQL user already exists.** The `CREATE USER IF NOT EXISTS ... IDENTIFIED WITH AWSAuthenticationPlugin` command should work, but if the user already exists with a different authentication plugin, it will fail. The LLD should use `ALTER USER` as a fallback if `CREATE USER` fails:

   ```sql
   CREATE USER IF NOT EXISTS 'keycloak'@'%' IDENTIFIED WITH AWSAuthenticationPlugin as IAM IAMAuth ENABLE;
   -- Or, if the user already exists with password auth:
   ALTER USER 'keycloak'@'%' IDENTIFIED WITH AWSAuthenticationPlugin as IAM IAMAuth ENABLE;
   ```

### Recommendations

- Fix TC01: Use `value = var.keycloak_database_username` for `KC_DB_USERNAME` instead of reading from the secret, or keep the secret but remove only `KC_DB_PASSWORD`.
- Fix TC02: Use `export KC_DB_PASSWORD=$(aws rds generate-db-auth-token ...)` instead of piping through `while read`.
- Fix TC03: Document the VPC constraint for the `null_resource` MySQL user creation, or provide an alternative (manual step with documented SQL).
- Fix TC04: Add `ALTER USER` fallback for the MySQL IAM user creation.

### Verdict
**APPROVED WITH CHANGES** -- 1 critical (TC02), 3 moderate (TC01, TC03, TC04).

---

## Backend Engineer (Byte)

### Strengths

- The design is infrastructure-only (no Python code changes), which aligns with the task scope.
- The conditional Terraform logic using `count` and `concat()` is clean and maintainable.
- The LLD correctly identifies that no new Python dependencies are needed.

### Concerns

1. **BC01: The LLD does not address what happens if Keycloak's JDBC pool holds stale connections after an IAM token refresh.** Keycloak's underlying JDBC driver (MySQL Connector/J) maintains persistent connections. When the token expires after 15 minutes, the old connections are still valid from MySQL's perspective (the token was the password at connection time), but if MySQL rotates or invalidates the token, the connection will fail. The LLD should note that Keycloak's connection pool validation should be enabled to detect and close stale connections, triggering a reconnect with a fresh token on next startup.

2. **BC02: The LLD does not address Keycloak's own connection pool settings.** The default HikariCP settings in Keycloak may hold connections for longer than 15 minutes, which means stale connections will accumulate. The LLD should recommend setting `maxLifetime` in the JDBC URL or Keycloak configuration to be less than the 15-minute token TTL (e.g., `maxLifetime=600000` for 10 minutes).

### Recommendations

- Recommend setting JDBC connection pool `maxLifetime` to less than the 15-minute IAM token TTL.
- Document the assumption that Keycloak's connection pool will be refreshed on container restart.

### Verdict
**APPROVED WITH CHANGES** -- 0 critical, 2 moderate.

---

## Review Summary

| Reviewer | Verdict | Critical | Moderate | Info | Key Recommendations |
|----------|---------|----------|----------|------|---------------------|
| Backend (Byte) | APPROVED WITH CHANGES | 0 | 2 | 0 | Set JDBC pool maxLifetime < 15 min token TTL |
| SRE (Circuit) | APPROVED WITH CHANGES | 0 | 3 | 0 | Verify Aurora SSL; replace sleep 30 with retry; fix AWS CLI install failure handling |
| Security (Cipher) | APPROVED WITH CHANGES | 0 | 3 | 0 | Note master_password in command line; make AWS CLI install failure fatal |
| SMTS (Sage) | APPROVED WITH CHANGES | 1 | 3 | 0 | Fix command substitution (pipe bug); fix KC_DB_USERNAME ref; document VPC constraint |

### Consolidated Critical Issues

1. **TC02: ECS command pipes token through `while read` in a subshell.** The `export KC_DB_PASSWORD` inside `while read` does not affect the parent shell. Fix: use `export KC_DB_PASSWORD=$(aws rds generate-db-auth-token ...)` command substitution.

### Consolidated Moderate Issues

1. **TC01: KC_DB_USERNAME still references removed secret.** Fix: use `value = var.keycloak_database_username` directly.
2. **RC01: `require_tls = true` without verifying Aurora SSL cert availability.** Verify before applying.
3. **RC02: `sleep 30` in `null_resource` is fragile.** Replace with a retry loop.
4. **TC03: `null_resource` MySQL client needs VPC network access.** Document constraint or provide manual alternative.
5. **BC01/BC02: JDBC pool `maxLifetime` may exceed 15-minute token TTL.** Recommend setting `maxLifetime` to 10 minutes.
6. **RC03: AWS CLI install `|| true` masks failures.** Make failure fatal.
7. **SC02: Master password on MySQL command line visible in plan output.** Document risk or use file-based approach.
8. **TC04: `CREATE USER IF NOT EXISTS` may fail if user exists with different plugin.** Add `ALTER USER` fallback.

### Consolidated Informational Issues

1. **15-minute token TTL is acceptable** given Fargate restart patterns, but a background refresh could be added in a follow-up.
2. **`master_password` must remain on the cluster** until replaced; plan a future phase to rotate it.
3. **Docker Compose local development is unaffected** (PostgreSQL, not MySQL).

### Next Steps

1. Fix the critical command substitution bug (TC02) -- use `$(...)` instead of pipe + `while read`.
2. Fix the KC_DB_USERNAME reference (TC01) -- use the Terraform variable directly.
3. Add `ALTER USER` fallback for the MySQL IAM user creation (TC04).
4. Replace `sleep 30` with a retry loop (RC02).
5. Make AWS CLI install failure fatal (RC03).
6. Verify Aurora SSL certificate before changing `require_tls = true` (RC01).
7. Document VPC constraint for `null_resource` MySQL user (TC03).
8. Recommend JDBC pool `maxLifetime` < 15 minutes (BC01/BC02).