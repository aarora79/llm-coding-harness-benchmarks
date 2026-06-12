# Expert Review: Remove EFS from terraform/aws-ecs/

*Created: 2026-06-12*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

---

## Reviewer: Pixel (Frontend Engineer)

**Focus:** UI, components, state, API integration

### Strengths
- Clean removal of obsolete infrastructure - no EFS means simpler architecture
- The registry service already has comments indicating EFS was removed from container mounts
- EFS access point definitions are isolated in one file (`storage.tf`)

### Concerns
1. **SCOPES_CONFIG_PATH environment variable** (line 221 in ecs-services.tf):
   - Current: `/efs/auth_config/auth_config/scopes.yml`
   - After EFS removal, this path must exist inside the container image
   - If the container doesn't have this local path, the auth server will fail to start
   - **Recommendation:** Verify the container image structure or update this path

2. **mcpgw data directory** (line 1806 in ecs-services.tf):
   - Currently mounts `mcpgw-data` EFS volume at `/app/data`
   - After removal, verify the container can run without this volume

### New Dependencies
- None required

### Recommendations
1. Test auth-server startup with the updated environment variables
2. Verify the container images contain the expected local paths for config files
3. Consider adding a pre-flight check in the container entrypoint to validate config paths exist

### Questions for Author
1. Have you verified what paths the container images actually expect for `/efs/auth_config` and `/app/data`?
2. Are these paths already baked into the container images, or do they依赖 on EFS mounts?

### Verdict: **APPROVED WITH CHANGES**

---

## Reviewer: Byte (Backend Engineer)

**Focus:** API design, data models, business logic, performance

### Strengths
- The design is straightforward - pure infrastructure removal with no business logic changes
- Comments in the code (lines 1367-1368, 1419-1420) already document that EFS volumes were removed for registry
- Module isolation - all EFS code is in `storage.tf` making it easy to remove

### Concerns

1. **SCOPES_CONFIG_PATH path mismatch:**
   ```hcl
   value = "/efs/auth_config/auth_config/scopes.yml"
   ```
   - This path suggests the container expects EFS to be mounted
   - After EFS removal, this file will not exist unless it's also in the container image
   - **Risk:** Container startup failure

2. ** mcpgw data persistence:**
   - The `mcpgw-data` EFS mount was at `/app/data`
   - Check if the mcpgw service needs persistent storage or if ephemeral is sufficient
   - Review the mcpgw codebase to see if it writes to `/app/data`

3. **Log directory:**
   - The auth-server has `SCOPES_CONFIG_PATH` pointing to EFS
   - The auth-server also has a log volume (`mcp-logs` -> `/app/logs`)
   - Verify log volume is not also using EFS (should be using CloudWatch based on existing config)

### Dependencies
- None required

### Better Alternatives Considered
1. **Option A (Chosen):** Delete EFS module entirely - Simplest, cleanest
2. **Option B:** Keep EFS but disable it - More complex, leaves dead code
3. **Option C:** Comment out EFS - Leaves dead code, hard to track later

### Recommendations
1. **High Priority:** Verify SCOPES_CONFIG_PATH is valid locally in the container
2. **High Priority:** Verify mcpgw doesn't need the `/app/data` directory
3. Add a test step in CI to verify auth-server starts without EFS mounts
4. Consider adding a health check that validates config file existence on startup

### Questions for Author
1. What is the actual source of `scopes.yml`? Is it in the container image or EFS?
2. Does mcpgw write to `/app/data` or just read from it?

### Verdict: **APPROVED WITH CHANGES**

---

## Reviewer: Circuit (SRE/DevOps Engineer)

**Focus:** Deployment, monitoring, scaling, infrastructure

### Strengths
- The infrastructure removal is straightforward and non-invasive
- No changes to VPC, subnet, or security group architecture
- ECS service definitions remain unchanged except for volume mounts

### Concerns

1. **State cleanup:**
   - If there's an existing deployment with EFS resources, `terraform apply` will DESTROY them
   - EFS file systems with data will be deleted - **this is intentional per the task**
   - Ensure no backups or dependencies on EFS exist before applying

2. **Terraform state migration:**
   ```bash
   # After removal, run:
   terraform state rm module.mcp_gateway.module.efs
   terraform state rm module.mcp_gateway.aws_vpc_security_group_egress_rule.efs_all_outbound
   ```

3. **Monitoring gaps:**
   - No EFS-specific CloudWatch alarms need to be removed (they're likely in the EFS module outputs)
   - Verify no external monitoring references EFS resources

4. **Cost verification:**
   - Verify no EFS-related costs remain after deployment
   - Check if any automation depends on EFS outputs

### Dependencies
- None required

### Better Alternatives Considered
1. **Option A (Chosen):** Complete removal - Cleanest for long-term maintenance
2. **Option B:** Keep but don't provision - Better for rollback but leaves clutter

### Recommendations
1. **Before deploy:** Take a snapshot of any existing EFS if data might be needed
2. **After deploy:** Run `terraform state list` to verify EFS resources are gone
3. **CI/CD:** Add a test that verifies no EFS resources are created during `terraform plan`
4. **Documentation:** Update README.md to note that EFS is no longer provisioned
5. **Backups:** Ensure any data that was in EFS has been migrated or is now in DocumentDB

### Questions for Author
1. Is there any EFS data that needs to be preserved (snapshotted) before deletion?
2. Are there any CI/CD pipelines or automation that reference EFS outputs?

### Verdict: **APPROVED WITH CHANGES**

---

## Reviewer: Cipher (Security Engineer)

**Focus:** AuthN/AuthZ, validation, OWASP, data protection

### Strengths
- Removing EFS eliminates a potential attack surface
- No new dependencies introduced
- Simplified architecture is easier to secure

### Concerns

1. **SCOPES_CONFIG_PATH security:**
   - Current: `/efs/auth_config/auth_config/scopes.yml`
   - After EFS removal, verify the new path:
     - Is it readable by the application user only?
     - Is it immutable (no write access at runtime)?
     - Are file permissions correct?

2. **Authentication config integrity:**
   - Verify `scopes.yml` is not writable by the container after removal
   - If the file is in the image, verify image signature/HashiCorp Vault integration

3. **EFS security group removal:**
   - The EFS security group (`*efs*`) had ingress for NFS (port 2049)
   - Confirm no other resources depend on this security group
   - Run `terraform plan` to verify the SG is not referenced elsewhere

4. **Access point deletion:**
   - Access points have IAM policies for POSIX compliance
   - Verify no IAM policies reference these access points

### Dependencies
- None required

### Better Alternatives Considered
1. **Option A (Chosen):** Complete removal - Simplifies security posture
2. **Option B:** Disable instead of remove - Leaves unused resources to audit

### Recommendations
1. **Security scanning:** Run Trivy or similar on container images to verify no secrets in EFS-like paths
2. **IAM review:** Check if any IAM policies reference EFS access points
3. **VPC endpoints:** Verify no VPC endpoint policies reference EFS
4. **Audit logging:** Ensure CloudTrail still captures all changes after EFS removal

### Questions for Author
1. Have you checked IAM policies for EFS access point references?
2. Are there any security groups that reference the EFS security group?

### Verdict: **APPROVED WITH CHANGES**

---

## Reviewer: Sage (SMTS - Overall Architecture)

**Focus:** Architecture, code quality, maintainability

### Strengths
- The codebase already has documentation that EFS volumes were removed for registry service
- Module structure is clean - EFS is isolated in `storage.tf`
- The task aligns with "remove obsolete infrastructure" principle

### Concerns

1. **Code consistency:**
   - Registry service has comments indicating EFS removal
   - Auth and mcpgw services still reference EFS in module but not in service definition
   - This is an opportunity to harmonize all three services

2. **Configuration drift:**
   - The variables `efs_throughput_mode` and `efs_provisioned_throughput` are defined but may not be used anywhere else
   - Removing them is correct, but verify no other modules reference them

3. **Output clean-up:**
   - Root outputs reference EFS via `module.mcp_gateway.efs_id`
   - Are anyterraform_remote_state consumers expecting these outputs?
   - **Risk:** External consumers breaking

4. **Documentation debt:**
   - README.md and OPERATIONS.md should be updated to note EFS is no longer provisioned
   - Consider adding a section "Removed Infrastructure" documenting what was taken out

### Dependencies
- None required

### Better Alternatives Considered
1. **Option A (Chosen):** Direct removal - Fastest path to clean state
2. **Option B:** Gradual deprecation - Would require keeping unused code longer

### Recommendations
1. **Pre-flight checklist:**
   - `grep -r "efs" terraform/aws-ecs/` - Find all remaining references
   - `grep -r "mcp_gateway_efs" .` - Check for external references
   - Review any backup/DR documentation that mentions EFS

2. **Post-merge actions:**
   - Update CHANGELOG.md with "Removed: EFS provisioning"
   - Update README.md Section X: "Storage: EFS removed, using DocumentDB/ephemeral"
   - Add deprecation note in any API documentation

3. **Future-proofing:**
   - Consider making storage backend a clear choice (file/documentdb/mongodb) without implied EFS
   - Document that ephemeral storage is suitable for temporary data only

### Questions for Author
1. Are there any external consumers of the `mcp_gateway_efs_*` outputs via terraform_remote_state?
2. Has the removal been confirmed against any documentation that mentions EFS storage?

### Verdict: **APPROVED WITH CHANGES**

---

## Review Summary

| Reviewer | Verdict | Blockers | Key Recommendations |
|----------|---------|----------|---------------------|
| **Pixel (Frontend)** | APPROVED WITH CHANGES | 1 | Verify SCOPES_CONFIG_PATH exists in container image |
| **Byte (Backend)** | APPROVED WITH CHANGES | 1 | Verify mcpgw `/app/data` is not needed for persistence |
| **Circuit (SRE)** | APPROVED WITH CHANGES | 0 | Take EFS snapshot before deploy if data might be needed |
| **Cipher (Security)** | APPROVED WITH CHANGES | 0 | Verify no IAM policies reference EFS access points |
| **Sage (SMTS)** | APPROVED WITH CHANGES | 0 | Check for external terraform_remote_state consumers |

### Overall Status: **APPROVED WITH CHANGES (4/5 reviewers)**

**Total Blockers:** 1 (auth-server SCOPES_CONFIG_PATH path verification)

---

## Next Steps

### Before Implementation
1. [ ] Verify container image structure for `/efs/auth_config` path
2. [ ] Verify mcpgw doesn't require `/app/data` persistence
3. [ ] Take EFS snapshot if any data needs to be preserved
4. [ ] Run `grep -r "efs" terraform/aws-ecs/` to find all references
5. [ ] Check for external terraform_remote_state consumers

### During Implementation
1. [ ] Update `lld.md` with final answers on SCOPES_CONFIG_PATH
2. [ ] Implement changes as documented in Step 1-7 of `lld.md`
3. [ ] Run `terraform validate` after each step
4. [ ] Run `terraform plan` to verify no unexpected changes

### After Implementation
1. [ ] Run `terraform apply` (with approval)
2. [ ] Run `terraform validate` and `terraform plan` (should show clean state)
3. [ ] Verify auth-server and mcpgw services start successfully
4. [ ] Update README.md with EFS removal notes
5. [ ] Add entry to CHANGELOG.md
