# Expert Review: Replace Keycloak Database Password with RDS IAM Authentication

*Created: 2026-06-25*

---

## 1. Frontend Engineer - Pixel

### Scope
This change primarily affects the infrastructure layer. Minimal frontend impact, but reviewing for completeness.

### Strengths
- Feature flag allows gradual rollout
- Clean conditional logic

### Concerns
1. No Frontend Concerns: No frontend changes required

### New Libraries / Infra Dependencies
- AWS CLI added to Keycloak container

### Recommendations
None specific to frontend.

### Questions for Author
Should the Keycloak admin UI show connection method (for debug visibility)?

### Verdict
APPROVED - No frontend blockers.

---

## 2. Backend Engineer - Byte

### Scope
Infrastructure changes, ECS task configuration, Docker modifications, Terraform resource changes.

### Strengths
1. Comprehensive Analysis: Correctly identifies Aurora MySQL RDS Proxy does NOT support IAM authentication
2. Clear Separation: Conditional resource creation using count allows safe rollback
3. Docker Pattern: Custom entrypoint script is standard for this use case
4. Security Focus: Proper use of IAM roles and resource-based policies

### Concerns

#### Critical: RDS Proxy IAM Auth Limitation
The design correctly identifies that Aurora MySQL RDS Proxy does not support IAM authentication. However, removing the proxy may impact connection management.

Aurora Serverless v2 has connection limits. Keycloak connection pooling should handle this, but need load testing.

#### High: Token Generation Latency
Each ECS task startup generates a token via AWS API call. This adds 1-3 seconds to cold start time.

Recommendation: Add retry logic in entrypoint script for token generation failures.

#### High: Single Point of Failure
If IAM token generation fails, Keycloak cannot start. There is no graceful degradation.

Recommendation: Consider keeping password auth as fallback initially.

#### Medium: Container Image Size
Installing AWS CLI increases Keycloak image size by approximately 100MB.

Alternative: Use minimal AWS SDK or call metadata service directly.

### New Libraries / Infra Dependencies

| Dependency | Justification |
|------------|--------------|
| AWS CLI | Required for generating RDS auth tokens in container |

### Better Alternatives Considered

1. Sidecar Pattern: Cleaner separation, but adds complexity. Not justified for this use case.
2. IAM Roles Anywhere: Overkill for ECS workload.

### Recommendations

1. Add retry logic (3 attempts with exponential backoff) to AWS CLI call
2. Log token generation latency metrics to CloudWatch
3. Consider implementing health check that validates DB connectivity
4. Document the RDS Proxy removal decision in terraform comments
5. Add metric for IAM auth vs password auth usage

### Questions for Author

1. What is the expected cold start impact with token generation?
2. How will we monitor for token generation failures?
3. Is there a rollback plan if IAM auth has issues in production?

### Verdict
APPROVED WITH CHANGES - Address retry logic and failover concerns.

---

## 3. SRE/DevOps Engineer - Circuit

### Scope
Deployment, monitoring, scaling, infrastructure, rollout procedure.

### Strengths
1. Gradual Rollout: Feature flag allows safe canary deployment
2. Conditional Resources: count parameter allows rollback without state manipulation
3. Documentation: Clear architecture diagrams

### Concerns

#### Critical: Connection Pooling Loss
Removing RDS Proxy means losing centralized connection pooling. Aurora Serverless v2 handles this but verify:

- Keycloak default connection pool size (typically 10-20)
- How many concurrent connections per task?
- With 4 tasks: 4 x 20 = 80 connections. Aurora v2 default max is 2000. Should be fine.

#### High: Regional Dependency
RDS IAM auth tokens are region-specific. Ensure region is correctly passed.

#### High: No Observability Plan
Missing CloudWatch alarms for new failure modes:
- Token generation failure rate
- Connection establishment latency
- Authentication failure count

#### Medium: Terraform State Migration
Changing conditional resources requires careful state management.

If changing from password to IAM auth:
1. Old proxy resource count goes to 0
2. Terraform will attempt to destroy existing proxy
3. May cause brief downtime during replacement

Recommendation: Use blue-green deployment or maintenance window.

### New Libraries / Infra Dependencies

| Dependency | Justification |
|------------|--------------|
| AWS CLI | Standard tool for AWS API access |

### Better Alternatives Considered

Use AWS Systems Manager Parameter Store for token caching - but tokens expire in 15 minutes anyway.

### Recommendations

1. Add CloudWatch alarm: Keycloak task restart rate > threshold
2. Add CloudWatch alarm: DB connection failure count
3. Document rollback procedure clearly
4. Test in staging with max expected load
5. Add runbook for IAM auth troubleshooting

### Questions for Author

1. What is the expected downtime during Terraform apply?
2. Do we need a maintenance window for this change?
3. What monitoring alerts should trigger for IAM auth failures?
4. How will we test connection pooling under load?

### Verdict
APPROVED WITH CHANGES - Add monitoring and test connection limits.

---

## 4. Security Engineer - Cipher

### Scope
Authentication, authorization, credential management, data protection.

### Strengths
1. Principle of Least Privilege: rds-db:connect scoped to specific DB user
2. Short-Lived Tokens: 15-minute token lifetime significantly reduces blast radius
3. No Static Credentials: Removes passwords from Terraform state
4. AWS Native: Uses RDS IAM auth which follows AWS best practices

### Concerns

#### Critical: Token Handling in Container
The RDS auth token is passed as KC_DB_PASSWORD environment variable.

Risks:
- Token appears in container metadata (docker inspect equivalent in ECS)
- Token may be logged if error includes connection string
- Token is in process environment (accessible to any process in container)

Mitigations:
- Token lifetime is only 15 minutes
- Use entrypoint to set, not ECS task definition
- Ensure Keycloak does not log connection strings

#### High: IAM Principal Wildcards
IAM policy template uses wildcards. Need to ensure proper scoping.

Specific concern:
```
"Resource": "arn:aws:rds-db:REGION:ACCOUNT:dbuser:CLUSTER-ID/USERNAME"
```

Recommendation: Verify CLUSTER-ID is the cluster_resource_id, not cluster name.

#### Medium: CloudTrail Logging
rds-db:connect calls should be logged to CloudTrail for audit.

Recommendation: Ensure CloudTrail is enabled and monitored.

#### Medium: Network Security
With IAM auth, we are removing RDS Proxy which provided some network-level protection.

Ensure:
- Security groups properly restrict RDS access to ECS tasks only
- Consider VPC flow logging for RDS connections

### New Libraries / Infra Dependencies

| Dependency | Security Impact |
|------------|-----------------|
| AWS CLI | Adds attack surface in container. Ensure minimal permissions. |

### Better Alternatives Considered

AWS Secrets Manager with IAM auth rotation - but this adds complexity without security benefit.

### Recommendations

1. Verify IAM policy Resource ARN uses cluster_resource_id not cluster_id
2. Add CloudTrail alarm for rds-db:connect failures
3. Document security benefits in CHANGELOG
4. Review Keycloak logging to ensure tokens not logged
5. Consider rotating the IAM database user periodically

### Questions for Author

1. Is the IAM policy Resource ARN correct (using cluster_resource_id)?
2. How will we detect if tokens are being logged accidentally?
3. Should we implement additional logging for security audit?
4. Have we verified CloudTrail captures rds-db:connect calls?

### Verdict
APPROVED WITH CHANGES - Verify token handling and CloudTrail coverage.

---

## 5. SMTS (Overall) - Sage

### Scope
Architecture, code quality, maintainability, long-term support.

### Strengths
1. Architecture Clarity: Clear diagrams showing before/after
2. Security Improvement: Significantly better security posture
3. Operational Simplicity: Eliminates rotation Lambda
4. Feature Flag: Allows safe experimentation and rollback

### Concerns

#### Critical: Aurora MySQL RDS Proxy Limitations
The design correctly handles the RDS Proxy limitation, but this should be prominently documented.

Action: Add a section to README explaining why Proxy is removed.

#### High: Long-term Maintenance
Container with AWS CLI is non-standard. Future updates may break this.

Recommendation: Consider contributing upstream or using a sidecar if long-term support is concern.

#### Medium: Testing Coverage
The testing plan is comprehensive but ensure:
- Load testing with maximum expected connections
- Chaos testing (token service unavailable scenarios)
- Regression testing with existing patterns

#### Medium: Documentation
Need clear decision record for future maintainers explaining IAM auth choice.

### New Libraries / Infra Dependencies

| Dependency | Long-term Impact |
|------------|------------------|
| Custom Entrypoint | Adds maintenance burden, document thoroughly |

### Better Alternatives Considered

Full assessment was done. Recommendation is sound.

### Recommendations

1. Create ADR (Architecture Decision Record) for this change
2. Add decision to docs/infrastructure/database-auth.md
3. Include load testing results in PR
4. Document rollback procedure in runbook
5. Add metric for migration tracking (IAM vs password auth usage)
6. Schedule follow-up review 30 days post-deployment

### Questions for Author

1. Who maintains this custom entrypoint long-term?
2. What is the rollback SLA if issues arise?
3. How will we measure success of this migration?
4. Should this pattern be applied to DocumentDB as well?

### Verdict
APPROVED WITH CHANGES - Add ADR and comprehensive documentation.

---

## Review Summary

| Reviewer | Verdict | Blockers | Key Recommendations |
|----------|---------|----------|---------------------|
| Frontend (Pixel) | APPROVED | 0 | None |
| Backend (Byte) | APPROVED WITH CHANGES | 0 | Add retry logic, document rollback |
| SRE (Circuit) | APPROVED WITH CHANGES | 0 | Add monitoring, test connections |
| Security (Cipher) | APPROVED WITH CHANGES | 0 | Verify token handling, CloudTrail |
| SMTS (Sage) | APPROVED WITH CHANGES | 0 | Add ADR, document decisions |

### Combined Blockers
None. All concerns are addressable through implementation details.

### Next Steps

1. Address feedback from Byte, Circuit, Cipher, and Sage
2. Add retry logic to entrypoint script
3. Add CloudWatch alarms for new failure modes
4. Verify IAM policy ARN format
5. Create ADR document
6. Conduct load testing
7. Final review before implementation

### Risk Assessment
- **Low**: Security risk (improvement)
- **Medium**: Operational risk (new pattern, learning curve)
- **Low**: Technical risk (well-understood AWS feature)

Overall: **PROCEED WITH CAUTION** - Good security improvement with manageable risks.
