# Expert Review: Remove EFS from terraform/aws-ecs/

*Created: 2026-07-22*
*Author: Claude*
*Status: Draft*

## Review Summary

This is a straightforward infrastructure cleanup task. The EFS file system has become obsolete since the application migrated to S3/DocumentDB for all persistent storage. The registry ECS service has already been migrated away from EFS (confirmed by comments in `ecs-services.tf` lines 1367 and 1419), confirming this is the right direction. The task scope is well-defined: remove EFS resources, update ECS volume mounts, and clean up scripts.

### Final Verdicts

| Reviewer | Verdict | Blockers |
|----------|---------|----------|
| Frontend (Pixel) | APPROVED | 0 |
| Backend (Byte) | APPROVED WITH CHANGES | 1 |
| SRE (Circuit) | APPROVED WITH CHANGES | 1 |
| Security (Cipher) | APPROVED | 0 |
| SMTS (Sage) | APPROVED WITH CHANGES | 1 |

---

## Frontend Engineer: Pixel

### Strengths
- Clean separation of concerns: only Terraform files and shell scripts are modified, no application code changes required.
- The pattern follows the existing migration of the registry service (volume = {}, updated SCOPES_CONFIG_PATH).
- The LLD provides before/after code snippets for every change, making it easy to verify.

### Concerns
- None relevant to UI/frontend.

### Recommendations
- None.

### Questions for Author
- None.

### Verdict: APPROVED

---

## Backend Engineer: Byte

### Strengths
- The LLD correctly identifies that EFS variables (`efs_throughput_mode`, `efs_provisioned_throughput`) are not passed through from root module to child module, simplifying the scope.
- The analysis correctly notes that the registry service already uses `/app/auth_server/scopes.yml` as the non-EFS alternative.
- The file-change table is comprehensive with line numbers.
- Correctly identifies that 3 of 6 EFS access points (servers, models, agents) are unused.

### Concerns

1. **Critical: SCOPES_CONFIG_PATH change assumes container image bundling.** The LLD proposes changing `SCOPES_CONFIG_PATH` from `/efs/auth_config/auth_config/scopes.yml` to `/app/auth_server/scopes.yml`. This assumes the auth service container image already includes `scopes.yml` at that path. If the image does not include it, the auth service will fail to start. The LLD should explicitly state this as a deployment prerequisite.

2. **MCPGW `/app/data` persistence gap.** The MCPGW service mounts EFS at `/app/data` for persistence. The LLD notes this but does not detail what happens to existing data when EFS is removed. If MCPGW stores state in `/app/data`, that state will be lost on the next deployment. The LLD should recommend verifying that MCPGW re-syncs its state from DocumentDB/S3 on startup, or that `/app/data` is ephemeral.

3. **Auth service logs volume.** The auth service mounts EFS for `/app/logs`. Removing this volume mount means logs go to stdout/CloudWatch (which is the desired behavior for ECS/Fargate with `enable_cloudwatch_logging = true`). The LLD should explicitly confirm that the auth service's log configuration uses CloudWatch.

### Recommendations
1. Add a deployment prerequisite note: "Ensure the auth service container image includes scopes.yml at the new path before applying the Terraform change."
2. Verify MCPGW does not depend on `/app/data` persistence across restarts, or document the expected state-loss behavior.
3. Confirm auth service logs go to CloudWatch and the EFS logs mount is not the primary log destination.

### Questions for Author
- Q1: Does the auth service container image include `scopes.yml` at `/app/auth_server/scopes.yml`?
- Q2: Does the MCPGW service store persistent state in `/app/data`, or is that directory used only for transient data?
- Q3: Is the auth service's EFS logs mount redundant given `enable_cloudwatch_logging = true`?

### Verdict: APPROVED WITH CHANGES

---

## SRE/DevOps Engineer: Circuit

### Strengths
- Comprehensive identification of all files with EFS references across the terraform directory.
- The file-change table with line numbers is excellent for the implementer.
- Correctly identifies that `run-scopes-init-task.sh` is entirely EFS-dependent and should be deprecated or rewritten.
- The LLD's comparison matrix (Chosen vs. Keep Trimmed EFS vs. S3 + Mountpoint) is well-reasoned.

### Concerns

1. **Critical: `terraform plan` will produce a large destroy-only plan for existing EFS resources.** If operators have existing deployments with EFS file systems, running `terraform apply` after this change will attempt to destroy the EFS file system, all mount targets, and the security group. If any of these resources have data (especially the EFS file system itself), that data will be permanently lost. The LLD recommends a staged approach but should be more explicit about this being a breaking change.

2. **`run-scopes-init-task.sh` needs migration guidance.** This script is used to initialize scopes.yml on EFS. After EFS is removed, operators will need a new mechanism to distribute scopes.yml. The LLD suggests deprecation, but operators need a concrete migration path. The recommended approach is to bundle scopes.yml in the container image and remove the script. If that is not feasible, the script should be rewritten to use a different mechanism (e.g., write to Secrets Manager and mount as a secret).

3. **Terraform state management.** The LLD should recommend that operators export their current state, apply the changes to a staging environment first, and verify the destroy plan looks correct before applying to production.

4. **The `_initialize_scopes` function in `post-deployment-setup.sh` has an EFS fallback.** When DocumentDB is not configured, the script falls through to "EFS mode (default)" and runs `run-scopes-init-task.sh`. After EFS removal, this fallback becomes a silent failure point. Operators who have not configured DocumentDB will not get their scopes initialized and may not notice until the auth service fails. The script should be updated to error explicitly rather than silently skip.

### Recommendations
1. Add a phased rollout plan: (a) deploy ECS changes first (removes mounts), (b) verify services run without EFS, (c) run `terraform apply` to destroy EFS resources.
2. Provide a concrete migration path for `run-scopes-init-task.sh` (bundle in image, deprecate script, or rewrite for new mechanism).
3. Include terraform state verification steps in the testing plan.
4. Update `post-deployment-setup.sh` to error explicitly rather than silently skip when EFS outputs are missing.

### Questions for Author
- Q1: What is the migration strategy for operators with existing EFS data?
- Q2: Will the auth service container image be updated to include scopes.yml before or after the Terraform change?
- Q3: What happens to the `_initialize_scopes` fallback path for operators who have not configured DocumentDB?

### Verdict: APPROVED WITH CHANGES

---

## Security Engineer: Cipher

### Strengths
- Removing EFS reduces the attack surface (no NFS port 2049 open to VPC CIDR).
- All EFS volumes used `transit_encryption = "ENABLED"`, which is good practice.
- Removing `elasticfilesystem:*` IAM permission is consistent with least-privilege.
- The EFS security group (created by `terraform-aws-modules/efs/aws`) had ingress from VPC CIDR only and an egress rule for all outbound. Both are removed, reducing the network attack surface.

### Concerns

1. **EFS security group removal.** The EFS security group has ingress rules for NFS from VPC CIDR and an egress rule for all outbound. When the EFS module is removed, the security group is also destroyed. The analysis confirms no other resources reference `module.efs.security_group_id`, which is good. However, operators should verify that any Network ACLs or security group rules referencing the EFS security group by ID (beyond Terraform) are cleaned up.

2. **No encryption key dependency.** The EFS module uses `encrypted = true` with AWS-managed keys. Removing EFS also removes this encryption dependency. No KMS key changes are needed.

3. **No new security risks introduced.** The change only removes resources; it does not introduce new network paths, IAM permissions, or data exposure vectors.

### Recommendations
- None. The change reduces attack surface.

### Questions for Author
- Q1: Has the EFS security group been referenced by any other security group rules in the Terraform config? (Answer: No, confirmed during analysis.)

### Verdict: APPROVED

---

## SMTS (Overall): Sage

### Strengths
- The LLD is thorough and includes exact line numbers, before/after code snippets, and a comprehensive file-change table.
- The pattern follows an existing precedent (registry service migration) which reduces risk.
- No new dependencies are introduced; this is a pure removal change.
- The LLD correctly identifies that EFS variables are not passed through from the root module, simplifying the scope.
- The alternatives considered section is well-reasoned and the comparison matrix is useful.
- Net deletion of ~581 lines of code is a positive outcome.

### Concerns

1. **Data loss risk.** The biggest concern is that operators with existing EFS deployments will lose all data on the EFS file system when `terraform apply` is run. The LLD mentions this in the error handling section but should provide a more prominent warning in the rollout plan. This should be a required acknowledgment before applying to production.

2. **Auth service SCOPES_CONFIG_PATH migration.** The change from `/efs/auth_config/auth_config/scopes.yml` to `/app/auth_server/scopes.yml` is correct, but it assumes the container image already includes the file. If not, the first deployment after this change will fail. The LLD should recommend updating the container image as a prerequisite.

3. **`run-scopes-init-task.sh` deprecation.** The script is entirely EFS-dependent. The LLD's recommendation to "replace with graceful error" is reasonable but operators need a clear migration path. The recommendation should be: deprecate the script entirely if scopes.yml is bundled in the container image, or rewrite it for a different storage mechanism if external distribution is still needed.

4. **MCPGW `/app/data` persistence.** The LLD correctly identifies this gap but should more strongly recommend verifying that MCPGW does not require persistent local storage. If MCPGW stores important data in `/app/data`, that data will be lost.

5. **post-deployment-setup.sh silent failure.** The `_initialize_scopes` function's EFS fallback (lines 549-567) will become a silent failure point. When operators run post-deployment setup without DocumentDB configured, the script will silently skip scopes initialization and not notify anyone. This should be an explicit error.

### Recommendations
1. Add a prominent data-loss warning before the EFS destruction step in the rollout plan.
2. Require container image update as a deployment prerequisite.
3. Provide a clear deprecation path for `run-scopes-init-task.sh`.
4. Verify MCPGW persistence assumptions before applying to production.
5. Update `post-deployment-setup.sh` to error explicitly when EFS outputs are missing and DocumentDB is not configured.

### Open Questions
- Q1: What data exists on the EFS file system, and can it be safely lost?
- Q2: Is there a separate PR to update the auth service container image with bundled scopes.yml?
- Q3: What happens to the `_initialize_scopes` fallback for operators who have not configured DocumentDB?

### Verdict: APPROVED WITH CHANGES

---

## Final Summary

| Reviewer | Verdict | Blockers | Key Recommendations |
|----------|---------|----------|---------------------|
| Frontend (Pixel) | APPROVED | 0 | N/A |
| Backend (Byte) | APPROVED WITH CHANGES | 1 | Verify container image includes scopes.yml; check MCPGW /app/data persistence |
| SRE (Circuit) | APPROVED WITH CHANGES | 1 | Phased rollout; provide migration path for run-scopes-init-task.sh |
| Security (Cipher) | APPROVED | 0 | N/A |
| SMTS (Sage) | APPROVED WITH CHANGES | 1 | Data-loss warning; container image prerequisite; script deprecation |

### Next Steps
1. Address Byte's concerns about the auth service SCOPES_CONFIG_PATH and MCPGW /app/data persistence
2. Add a prominent data-loss warning to the rollout plan
3. Clarify the migration path for run-scopes-init-task.sh (deprecate vs. rewrite)
4. Verify container image includes scopes.yml before applying Terraform changes
5. Update post-deployment-setup.sh to error explicitly when EFS outputs are missing