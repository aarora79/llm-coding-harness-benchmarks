# Expert Review: Remove EFS from terraform/aws-ecs

*Created: 2026-07-06*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Reviewer 1: Pixel (Frontend Engineer)

**Focus:** UI/UX, components, state, API integration

**Verdict:** APPROVED (Not Applicable)

**Strengths:**
- This change has no UI impact. The registry frontend communicates via REST API and MCP protocol, neither of which depends on the storage backend being EFS.

**Concerns:**
- None. This is a pure infrastructure change.

**Questions for Author:**
- None.

---

## Reviewer 2: Byte (Backend Engineer)

**Focus:** API design, data models, business logic, performance

**Verdict:** APPROVED WITH CHANGES

**Strengths:**
- The LLD correctly identifies that the registry service already removed its EFS mounts (line 1367: "EFS volumes removed").
- The approach to remove the entire `storage.tf` file is correct - EFS is a self-contained module that doesn't feed into other resources.
- The comparison matrix is thorough.

**Concerns:**
1. **SCOPES_CONFIG_PATH mismatch**: The auth-server at line 221 sets `SCOPES_CONFIG_PATH` to `/efs/auth_config/auth_config/scopes.yml`. The LLD says to change it to `/app/auth_server/scopes.yml` (matching mcpgw at line 822). However, I need to verify that the auth-server Docker image actually has scopes.yml at `/app/auth_server/scopes.yml`. The auth-server and mcpgw may use different image paths. If scopes.yml is no longer available at the new path, the auth-server will fail to start.

2. **The mcpgw `app_log_dir` variable**: The LLD flags line 1760 where `app_log_dir` is set for mcpgw. If the default path for app logs on ECS is the container's ephemeral storage (as stated in the variable description at line 1053-1055), then removing the EFS volume for mcpgw is fine. But if `app_log_dir` was previously set to an EFS path, it needs to be verified.

3. **The `volume = {}` assignment**: The LLD correctly identifies that both auth-server and mcpgw should have `volume = {}` after removing EFS volumes. This matches the pattern already used by the registry service.

**Recommendations:**
- Verify that scopes.yml is baked into the Docker image for both auth-server and mcpgw, or that it can be provided via an alternative mechanism (e.g., init container from S3).
- Verify the default value of `app_log_dir` and whether mcpgw needs it explicitly set to empty string after EFS removal.

---

## Reviewer 3: Circuit (SRE/DevOps Engineer)

**Focus:** Deployment, monitoring, scaling, infrastructure

**Verdict:** APPROVED WITH CHANGES

**Strengths:**
- The LLD's file-by-file change plan is excellent and complete.
- The observation that registry already follows the `volume = {}` pattern provides a safe template.
- Correctly identifies the 7-day pending-deletion behavior of EFS as a safety net.

**Concerns:**
1. **Destructive operation warning**: For production deployments that have the EFS file system already provisioned with data, `terraform apply` will attempt to destroy the EFS file system. Even with force deletion, this could lose any data that was written to the EFS mount points (though they are unused, the file system itself may contain old data from the migration period). The LLD should explicitly recommend a dry-run first and add a warning in README.md.

2. **Terraform state consistency**: If any operator has already run `terraform apply` against the current state with EFS resources, the plan output will show 1 EFS file system, 3 mount targets (per private subnet), 6 access points, 2 security groups (one from the module, one manual egress rule), and 3 outputs being destroyed. This is expected and correct.

3. **Script dependencies**: The `run-scopes-init-task.sh` and `post-deployment-setup.sh` scripts are called by platform engineers after deployment. If these scripts fail because they reference EFS outputs that no longer exist, it will cause confusion. The LLD correctly identifies these but should recommend either:
   - Removing the scripts entirely if they are no longer useful
   - Updating them to use a non-EFS scopes initialization method
   - Adding a deprecation notice and redirect to the new method

4. **CloudWatch monitoring**: EFS generates CloudWatch metrics (BurstBalance, TotalIOBytes, etc.). Removing EFS means these metrics will stop. This is fine since nothing was using them, but operators monitoring dashboards should be aware.

**Recommendations:**
- Add a "Pre-deployment checklist" to the README:
  - Verify no data exists on the EFS file system (or accept data loss)
  - Run `terraform plan` first and review the destruction list
  - Ensure scopes.yml is available via the Docker image or alternative mechanism
- Recommend removing or replacing the EFS-dependent scripts in the same PR.

---

## Reviewer 4: Cipher (Security Engineer)

**Focus:** AuthN/AuthZ, validation, OWASP, data protection

**Verdict:** APPROVED

**Strengths:**
- Removing EFS eliminates a potential attack surface (NFS port 2049 is no longer exposed).
- The EFS security group ingress rule (NFS from VPC CIDR) is removed, reducing the network attack surface.
- The IAM permission `elasticfilesystem:*` is removed from the README, reducing the principle of least privilege footprint.

**Concerns:**
1. **No data loss risk**: EFS was encrypted (`encrypted = true` in storage.tf line 16). When the file system is destroyed, the data is unrecoverable. This is acceptable for unused infrastructure but should be documented.

2. **Access point POSIX users**: The EFS access points use `posix_user` with `uid = 1000, gid = 1000`. These are not security-sensitive (they are just ownership mappings for the file system) and are not credentials.

3. **Transit encryption**: The EFS volumes had `transit_encryption = "ENABLED"`. Removing EFS eliminates the need for transit encryption configuration, which is a simplification.

**Questions for Author:**
- None. This change only removes infrastructure, not adds security-sensitive features.

---

## Reviewer 5: Sage (SMTS - Overall Architecture)

**Focus:** Architecture, code quality, maintainability

**Verdict:** APPROVED WITH CHANGES

**Strengths:**
- The LLD demonstrates thorough understanding of the codebase. The grep-based analysis found all EFS references and correctly identified dead vs. active code paths.
- The decision to remove the entire `storage.tf` file (183 lines) rather than conditionally disable EFS is correct for a fully obsolete dependency.
- The estimated lines of code (-268) is accurate and shows this is a net reduction in complexity.
- The alternatives considered section correctly rejects both the `prevent_destroy` and `enable_efs` variable approaches.

**Concerns:**
1. **Script removal or update is critical**: The `run-scopes-init-task.sh` script creates an entire ECS task definition with EFS volumes. If this script is removed, it should be done carefully - there may be operators who have not yet migrated their scopes.yml management. The LLD should recommend adding a deprecation notice in the README before script removal, or providing an alternative mechanism in the same PR.

2. **Helm charts and Docker Compose**: The LLD correctly marks these as out of scope. However, if the Helm charts still reference EFS volumes for auth-server or mcpgw, there will be an inconsistency between the Terraform deployment and the Helm deployment. The LLD should note this and recommend filing a follow-up issue for the Helm chart cleanup.

3. **Module coupling**: The mcp_gateway module passes EFS IDs via `module.efs.id` to the ECS service modules. After removal, these references will cause compilation errors. The LLD correctly plans to remove all references in the correct order (storage.tf first, then ecs-services.tf, then outputs).

**Recommendations:**
- Add a follow-up task for Helm chart and Docker Compose EFS cleanup.
- Add a deprecation notice before removing the EFS-dependent scripts.
- The overall approach (full removal, no toggle) is the right call for infrastructure that is fully obsolete.

---

## Review Summary

| Reviewer | Verdict | Blockers | Key Recommendations |
|----------|---------|----------|---------------------|
| Pixel (Frontend) | APPROVED | 0 | N/A - no UI impact |
| Byte (Backend) | APPROVED WITH CHANGES | 0 | Verify scopes.yml availability in Docker image; verify app_log_dir default |
| Circuit (SRE) | APPROVED WITH CHANGES | 0 | Add dry-run warning; remove/update EFS-dependent scripts; note CloudWatch metric loss |
| Cipher (Security) | APPROVED | 0 | None - security posture improves with EFS removal |
| Sage (SMTS) | APPROVED WITH CHANGES | 0 | Add follow-up task for Helm/Docker; add deprecation notice for scripts |

**Total blockers: 0**
**Total concerns: 3 (all addressable in the implementation)**

## Next Steps

1. Verify that scopes.yml is available in the Docker images for both auth-server and mcpgw (or define the alternative mechanism).
2. Add a pre-deployment warning to README.md for operators performing this change on live infrastructure.
3. Decide whether to remove or update the EFS-dependent scripts (run-scopes-init-task.sh, post-deployment-setup.sh) in the same PR or as a follow-up.
4. File a follow-up issue for Helm chart and Docker Compose EFS cleanup.
5. Execute the implementation per the LLD step-by-step plan.