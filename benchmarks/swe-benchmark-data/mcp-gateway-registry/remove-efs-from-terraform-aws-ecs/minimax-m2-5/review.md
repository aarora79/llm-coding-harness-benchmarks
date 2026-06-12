# Expert Review: Remove EFS from terraform-aws-ecs

*Created: 2026-06-12*
*Author: Claude (minimax-m2.5 benchmark)*

---

## Reviewer: Pixel (Frontend Engineer)

### Strengths
- Clean separation of concerns - storage Terraform is isolated
- Clear documentation of what files to modify
- Good rollback strategy identified

### Concerns
1. None relevant to frontend

### New Libraries / Infra Dependencies Required
- None

### Better Alternatives Considered
- Keep EFS but mark as deprecated
- **Why Rejected**: Ongoing costs and complexity

### Recommendations
- Consider adding a variable to optionally re-enable EFS for backward compatibility (but default to disabled)

### Questions for Author
- Will existing deployments automatically pick up these changes?

### Verdict: APPROVED

---

## Reviewer: Byte (Backend Engineer)

### Strengths
- Comprehensive list of all files with EFS references
- Clear line numbers for each change location
- Good mapping of current vs. target architecture

### Concerns
1. **Ephemeral Storage Limits**: ECS containers have limited ephemeral storage (20GB default). Need to ensure application doesn't exceed this.
2. **Path References**: Application code may reference `/efs` paths that will no longer exist after removal.

### New Libraries / Infra Dependencies Required
- None

### Better Alternatives Considered
- Use EFS "just-in-time" mounting only when needed
- **Why Rejected**: Complexity not worth the minimal use case

### Recommendations
1. Verify application handles missing `/efs` directories gracefully
2. Test with production-like data volume to ensure ephemeral storage is sufficient
3. Add health check for storage availability

### Questions for Author
1. What is the current EFS data volume?
2. Are there any application-level path hard-coded to `/efs`?

### Verdict: APPROVED WITH CHANGES

---

## Reviewer: Circuit (SRE/DevOps Engineer)

### Strengths
- Clear step-by-step implementation plan
- Good identification of all EFS-related resources
- Includes Terraform validation in test plan

### Concerns
1. **Service Disruption**: Removing EFS will cause ECS task restart. Need to plan for zero-downtime deployment.
2. **Cost Impact**: This removes ongoing EFS costs - positive!
3. **Monitoring**: Need to update any alerts/metrics referencing EFS

### New Libraries / Infra Dependencies Required
- None

### Better Alternatives Considered
- Keep EFS for disaster recovery scenarios
- **Why Rejected**: DocumentDB serves this purpose

### Recommendations
1. Use `terraform apply -target` to remove EFS resources first, then update ECS
2. Add ECS service deployment configuration to ensure graceful rolling update
3. Document the cost savings in release notes
4. Update any CloudWatch metrics dashboards that reference EFS

### Questions for Author
1. What is the monthly cost of the current EFS setup?
2. How many access points will be removed?

### Verdict: APPROVED WITH CHANGES

---

## Reviewer: Cipher (Security Engineer)

### Strengths
- Removes security group rules (attack surface reduction)
- Simplifies network architecture

### Concerns
1. **Data Residual**: After EFS deletion, ensure old EFS data is properly cleaned up in AWS
2. **Secrets Path**: `/efs/auth_config/scopes.yml` was used for auth config - verify this is now in Secrets Manager

### New Libraries / Infra Dependencies Required
- None

### Better Alternatives Considered
- None identified

### Recommendations
1. Before destroying EFS, verify all sensitive data has been migrated to Secrets Manager or DocumentDB
2. Ensure old EFS file system is properly terminated (not just the Terraform resource)
3. Update any security documentation

### Questions for Author
1. Is auth config (scopes.yml) now stored in Secrets Manager?
2. What happens to existing data on EFS when this is applied?

### Verdict: APPROVED

---

## Reviewer: Sage (SMTS - Overall Architecture)

### Strengths
- Well-structured approach - isolated infrastructure change
- Good understanding of application data flow (DocumentDB persistence)
- Clear identification of all affected components

### Concerns
1. **Terraform State**: Removing the module will leave orphaned EFS resource in AWS if not properly handled
2. **Module Dependency**: Need to verify no other modules depend on EFS outputs

### New Libraries / Infra Dependencies Required
- None

### Better Alternatives Considered
- Keep EFS as optional via variable
- **Why Rejected**: Goal is complete removal

### Recommendations
1. Run `terraform plan` multiple times to ensure stable output
2. Use `terraform state mv` if there's any drift during migration
3. Consider adding a lifecycle rule to prevent accidental re-creation of EFS

### Questions for Author
1. Has this been tested in a non-production environment first?
2. What's the deployment order - Terraform apply before/after container deploy?

### Verdict: APPROVED WITH CHANGES

---

## Review Summary

| Reviewer | Verdict | Blockers | Key Recommendations |
|----------|---------|----------|---------------------|
| Pixel (Frontend) | APPROVED | 0 | Consider backward-compat variable |
| Byte (Backend) | APPROVED WITH CHANGES | 0 | Verify path handling, storage limits |
| Circuit (SRE) | APPROVED WITH CHANGES | 0 | Plan zero-downtime deployment |
| Cipher (Security) | APPROVED | 0 | Clean up old EFS data |
| Sage (SMTS) | APPROVED WITH CHANGES | 0 | Test Terraform stability |

### Critical Items

1. **Application Path Handling** (Byte): Verify `/efs` path references in application code
2. **Zero-Downtime Deployment** (Circuit): Plan rolling update strategy
3. **Ephemeral Storage Limits** (Byte): Confirm container storage capacity is sufficient

### High-Priority Items

1. CloudWatch metrics updates
2. Cost documentation in release notes
3. Terraform state verification

### Medium-Priority Items

1. Optional variable for backward compatibility
2. Lifecycle rule to prevent EFS re-creation

### Conclusion

The design is sound and achieves the goal of removing EFS resources. The main concerns are around deployment ordering and verifying application compatibility with ephemeral-only storage. No blockers were identified that would prevent implementation.