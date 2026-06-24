# Expert Review: Remove EFS from Terraform AWS ECS Deployment

*Created: 2026-06-15*
*Related LLD: `./lld.md`*
*Related Issue: `./github-issue.md`*

## Executive Summary

The proposed design to remove EFS from the MCP Gateway Registry Terraform deployment received **APPROVED WITH MINOR CHANGES** from the expert panel. The reviewers agreed that EFS removal aligns with cloud-native principles and will reduce complexity, cost, and maintenance burden. However, several concerns were raised about migration planning, validation, and documentation completeness.

## Review Criteria

| Reviewer | Area of Focus | Verdict |
|----------|---------------|---------|
| Frontend Engineer - Pixel | UI/UX, Console Outputs, Error Messages | APPROVED WITH MINOR CHANGES |
| Backend Engineer - Byte | Terraform Design, Storage Patterns, Error Handling | APPROVED WITH CHANGES |
| SRE/DevOps Engineer - Circuit | Deployment, Monitoring, Validation, Rollback | APPROVED WITH CHANGES |
| Security Engineer - Cipher | AuthN/AuthZ, Network Isolation, Data Protection | APPROVED |
| SMTS: Overall - Sage | Architecture, Code Quality, Maintainability | APPROVED WITH CHANGES |

## Review Details

### 1. Frontend Engineer Review (Pixel)

**Focus:** Console outputs, error messages, user-facing documentation

**Strengths:**
- ✅ Clear console output improvements (removes EFS initialization noise)
- ✅ Well-structured error handling guidance
- ✅ Comprehensive migration documentation structure
- ✅ Color-coded logging in scripts enhances UX

**Concerns:**
- ⚠️ **Missing:** User-friendly error messages for when SSM parameter is missing after EFS removal
- ⚠️ **Minor:** Console output verbosity - consider calming down "SUCCESS" messages during normal operation (difficulty: low)
- ⚠️ **Missing:** Validation testing for `Terraform state list` to show EFS no longer present

**Recommendations:**
1. Add explicit error message for SSM parameter retrieval failure in Auth Service
2. Include validation step that confirms no EFS resources exist after apply
3. Add paragraph about expected changes in CloudWatch metrics (EFSSlowAPI vs SSM operations)

**Questions for Author:**
- Q: How should users detect if their deployment still references EFS after the migration?
- Q: Will there be explicit warnings in `terraform plan` if someone tries to add EFS back manually?

**Verdict:** APPROVED WITH MINOR CHANGES (3 minor issues, 1 medium documentation gap)

### 2. Backend Engineer Review (Byte)

**Focus:** Terraform design, storage patterns, service integration

**Strengths:**
- ✅ Correct identification of partial migration (registry already off EFS)
- ✅ Appropriate service-specific storage substitutions (SSM, Secrets Manager, ephemeral)
- ✅ Good Terraform structure following existing patterns
- ✅ Proper handling of config file locations

**Concerns:**
- ❌ **Critical:** Missing detailed validation for SSM parameter size limits (8KB Advanced, 4KB Standard)
- ⚠️ **Major:** No migration testing plan for production instances with existing EFS data
- ⚠️ **Major:** Unclear how service restarts will handle transition from file-based to SSM-based config
- ⚠️ **Medium:** Need to update module signature - removing required EFS variables could break existing callers

**Better Alternatives Considered:**
- ✅ Consider putting large configuration in SSM Documents (HTML breaks at 8KB, not XML/YAML)
- ✅ Recommend automatic parameter splitting for config sizes > 7KB
- ✅ Add pre-check for parameter store quota limits before removal

**Recommendations:**
1. Add explicit validation in post-deployment: `aws ssm describe-parameters --query "Parameters[?contains(Name, 'scopes.yml')].Size"`
2. Create backup script for existing EFS users: `scripts/backup-efs-to-ssm.sh`
3. Add GraphQL to identify services using EFS mounts: `terraform plan | grep efs_volume_configuration`
4. Update module signature: add `enable_efs = false` flag for gradual migration instead of forced removal

**Questions for Author:**
- Q: What happens to running containers during migration - do they gracefully handle config reloading?
- Q: Will session cookies still work across service restarts after config changes?
- Q: Are there any known issues with the sheer number of SSM parameters we'd be creating?

**Verdict:** APPROVED WITH CHANGES (1 critical, 2 major issues; design sound but needs migration safety)

### 3. SRE/DevOps Engineer Review (Circuit)

**Focus:** Deployment workflow, monitoring, rollback, operational readiness

**Strengths:**
- ✅ Excellent monitoring direction (track SSM vs EFS API calls)
- ✅ Comprehensive rollback guidance
- ✅ Realistic performance bottleneck analysis
- ✅ Good CloudWatch integration pattern

**Concerns:**
- ❌ **Critical:** No rollout validation happened - need staged rollout plan
- ❌ **Critical:** No SSM parameter pre-provisioning in documentation
- ⚠️ **Major:** Incomplete rollback procedure - missing SSM parameter cleanup
- ⚠️ **Medium:** Undefined CI/CD pipeline changes required

**Recommendations:**
1. Add SSM parameter bootstrapping to README
2. Create validation template for CI/CD: check SSM parameter exists before deployment
3. Add explicit rollback command sequences
4. Include template for SSM parameter creation with correct IAM policy

**τραύμα εταιρεία:**
- We need to validate SSM bootstrap: `uv run scripts/ssm-provision.sh --queries scopes.yml --type String`
- We need to validate IAM permissions: `sts decrypt` policy should include `ssm:GetParameters` action
- We need validation for `/` namespace limits: `aws ssm describe-parameter --name "/mcp-gateway/prod/scopes.yml"`

**Verdict:** APPROVED WITH CHANGES (2 critical operational deficiencies; design sound)

### 4. Security Engineer Review (Cipher)

**Focus:** Authentication, data protection, network isolation

**Strengths:**
- ✅ Complete removal of EFS mounts reduces attack surface
- ✅ Reduction in network services decreases lateral movement risk
- ✅ Consolidated storage minimizes secrets leakage potential
- ✅ Strong break-glass procedure guidance

**Security Concerns:**
- ✅ **Minor:** Common concerns addressed, no new issues

**New Libraries/Tools:**
- None needed; existing AWS platform services sufficient

**Verdict:** APPROVED (No security issues identified)

### 5. SMTS: Overall Review (Sage)

**Focus:** Architecture, code quality, maintainability, standards

**Strengths:**
- ✅ Exceptional analysis of existing patterns
- ✅ Follows established Terraform best practices
- ✅ Maintains consistent naming conventions
- ✅ Proper attention to variables and outputs
- ✅ Excellent documentation structure

**Concerns:**
- ⚠️ **Medium:** Terraform code deletion without preservation strategy
- ⚠️ **Minor:** Code occupies 313 lines (small but significant); should consider progressive feature flag approach
- ⚠️ **Minor:** Missing TODOs for migration plan items

**Recommendations:**
1. Add migration guide containing:
   - Deletion verification: `terraform state rm module.efs`
   - Backup recommendation: `scripts/backup-efs-config.sh`
   - Terraform plan check: `terraform graph | grep -v efs`
2. Preserve Terraform code in `.archive/` directory for historical reference
3. Add TODO comments for incomplete migration path elements

**Questions for Author:**
- Q: Should we keep deleted Terraform code in version control for rollback scenarios?
- Q: Have we validated the removal eyecatching in Terraform graph?
- Q: Should we add progressive migration stages?

**Verdict:** APPROVED WITH CHANGES (3 medium changes needed; fundamentally sound)

## Review Summary

| Reviewer | Verdict | Blockers | Key Recommendations |
|----------|---------|----------|---------------------|
| **Frontend (Pixel)** | APPROVED WITH MINOR CHANGES | 1 Medium | Add SSM error message, Show context-aware outputs |
| **Backend (Byte)** | APPROVED WITH CHANGES | 1 Critical, 2 Major | Add parameter size validation, Migration backing, Sharding logic |
| **SRE (Circuit)** | APPROVED WITH CHANGES | 2 Critical | Staged rollout plan, IAM permissions guidance, Validation template inclusion |
| **Security (Cipher)** | APPROVED | 0 | None required |
| **SMTS (Sage)** | APPROVED WITH CHANGES | 1 Medium | preserve deleted Terraform code, Add TODO guidance |

## Key Recommendations Summary

### Critical Issues (Must Be Resolved Before Implementation)
1. **SSM Parameter Size Validation** - Add automatic parameter splitting for config sizes > 7KB
2. **Rollout Validation** - Need staged canary rollout plan with validation gates
3. **SSM Parameter Pre-Provisioning** - Add SSM bootstrap script and IAM policy templates
4. **Honesty Policy** - Add explicit error message when SSM parameter retrieval fails

### Major Issues (Should Be Resolved Before Implementation)
1. **Migration Path** - Need backup script and flipping scripts for existing users
2. **Service Restart Behavior** - Define how services handle config transition
3. **Parameter Splitting Strategy** - Define how to break large configurations into multiple parameters
4. **TODOs List** - Add TODO comments for incomplete migration path items

### Minor Issues (Nice to Have)
1. **User-Friendly Messages** - Add context about missing SSM parameters
2. **Output Verification** - Confirm no EFS in Terraform state
3. **Code Archives** - Preserve deleted Terraform code in `.archive/`
4. **SSM Error Handling** - Include optimized error messages

### Missing Elements (Additional Content Needed)
1. **SSP Parameters** - Service-specific parameters with clear SSM path structure
2. **Rollback Cleanup** - Add SSM parameter cleanup to rollback scripts
3. **Parameter Limits** - Add AWS API call limit guidance
4. **Migration Guide** - Step-by-step migration guide for existing users

## Next Steps

### Immediate Actions
1. ✅ Address critical issues (SSM validation, rollout plan, parameter provisioning)
2. ✅ Add missing rollback and fallback logic
3. ✅ Create comprehensive migration guide
4. ✅ Update README documentation with migration path

### Implementation Questions
1. How do we want to handle configuration sharding for large configs (> 7KB)?
2. Should we preserve deleted Terraform code in archives for rollback scenarios?
3. What specific validation commands should be included in post-deployment?

### Suggestions for Future Enhancement
1. Consider creating a parameter mgmt layer based on CSV import to break huge config
2. Consider adding parameter caching for container performance optimization
3. Add health checks to validate SSM parameter availability before service startup
4. Create structured search capability for parameter store

## Verdict

**APPROVED WITH CHANGES** - The SWE team approves this feature with the understanding that critical and major issues will be resolved before implementation. The work represents a strong architectural improvement that eases maintenance burden and reduces attack surface.

**Implementation Timeline:** Medium complexity change requiring 2-3 weeks of focused work plus migration planning. Sufficient scope for early- or mid-career engineer with Terraform/AWS experience.

## Sign-off

This presentation concludes the review cycle. The team stands ready to assist with implementation guidance once prerequisites are addressed.

**Review Date:** 2026-06-15
**Effective Date:** 2026-06-15
**Sign-off By:** SMPT Team - MCP Gateway Registry Collaboration

## Decision Log

1. Differences from initial proposal:
   - Additional SSM parameter splitting guidance required
   - Enhanced spectr & Bandsupport (STS/SSM) configuration guidance
   - Additional rollout validation templates requested
   - Backup and rollback guidance enhanced
   - Additional template guidance

2. Content decisions:
   - All source content has been presented in good faith with templates
   - No DBA, Docker, or Kubernetes access risks exist
   - High and low changes have been shown
   - Caching and retrieval guidance across multiple iterations

3. Suggestions received:
   - Support additional template guidance in repository
   - Provide workspace presets with curated templates
   - Create custom templates using decoded iterations

## Research Perspective

This constitutes SMPT guidance to prepare this design for production deployment. The team provides comprehensive analysis, error recommendations, and explicit suggestions that validate against design quality requirements.

- **Acceptance Radius**: 2-3 days of full access for policy validation testing
- **Expiration Window**: 30 days for content updates and iteration requests
- **Protective Measures**: Standard mechanisms for transparency during sprint cycles

The approved design prepares MCP Gateway Registry for all new deployments and is ready for graduated rollout to production deployments after migration documentation is finalized.
