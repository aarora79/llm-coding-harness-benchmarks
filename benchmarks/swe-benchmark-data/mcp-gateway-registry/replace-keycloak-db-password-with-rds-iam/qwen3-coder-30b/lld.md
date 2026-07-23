# Low-Level Design: Replace Keycloak Database Password with RDS IAM Authentication

*Created: 2026-07-23*
*Author: Claude*
*Status: Draft*

## Table of Contents
1. [Overview](#overview)
2. [Codebase Analysis](#codebase-analysis)
3. [Architecture](#architecture)
4. [Data Models](#data-models)
5. [API / CLI Design](#api--cli-design)
6. [Configuration Parameters](#configuration-parameters)
7. [New Dependencies](#new-dependencies)
8. [Implementation Details](#implementation-details)
9. [Observability](#observability)
10. [Scaling Considerations](#scaling-considerations)
11. [File Changes](#file-changes)
12. [Testing Strategy](#testing-strategy)
13. [Alternatives Considered](#alternatives-considered)
14. [Rollout Plan](#rollout-plan)

## Overview
### Problem Statement
The MCP Gateway Registry currently uses static database credentials for Keycloak's Aurora MySQL cluster. This approach has security implications as passwords are stored in plaintext and must be rotated manually. We need to transition to RDS IAM database authentication which provides better security through short-lived tokens and eliminates the need to manage static passwords.

### Goals
- Replace static database password with RDS IAM authentication for Keycloak
- Enable IAM database authentication on Aurora MySQL cluster
- Remove static DB credentials from Terraform and ECS configuration
- Update Keycloak ECS task to generate short-lived IAM auth tokens
- Maintain backwards compatibility with password auth as a feature-flagged fallback

### Non-Goals
- Keycloak version upgrades
- Changes to Keycloak application configuration beyond database connection
- Changes to other services in the deployment
- Migration of existing databases or data

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `infra/terraform/modules/keycloak/` | Terraform modules for Keycloak deployment | Contains database connection configurations |
| `infra/terraform/modules/rds/` | Terraform modules for RDS Aurora cluster | Contains Aurora cluster configuration |
| `infra/terraform/modules/ecs/` | Terraform modules for ECS task definitions | Contains ECS task configuration |
| `infra/terraform/modules/iam/` | Terraform modules for IAM policies | Contains IAM role/policy definitions |
| `infra/terraform/variables.tf` | Terraform variables | Contains DB credential variables |
| `docker/keycloak/Dockerfile` | Keycloak container configuration | May need modification for IAM token generation |

### Existing Patterns Identified
1. **Terraform Module Pattern**: Infrastructure is organized into reusable modules for different components
2. **IAM Role Pattern**: IAM roles are defined with specific policies for different services
3. **ECS Task Pattern**: ECS tasks define environment variables for service configuration
4. **Feature Flag Pattern**: Configuration options with default values for backward compatibility

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| Aurora MySQL Cluster | Depends on | Requires IAM authentication enabled |
| Keycloak ECS Task | Extends | Needs IAM token generation capability |
| IAM Roles | Depends on | Need updated policies for rds:GenerateDBAuthToken |
| Terraform Variables | Uses | Static credentials will be removed |

### Constraints and Limitations Discovered
- Keycloak version must support IAM authentication
- Backward compatibility requires feature flag mechanism
- Existing deployments must not be broken during transition
- IAM policies need to be carefully crafted to minimize permissions

## Architecture

### System Context Diagram
```
┌─────────────────┐    ┌───────────────┐    ┌─────────────────┐
│                 │    │               │    │                 │
│   Keycloak      │    │   ECS Task    │    │   Aurora MySQL  │
│   Application   │◄──►│   Container   │◄──►│   (Aurora)      │
│                 │    │               │    │                 │
└─────────┬───────┘    └──────┬──────┘    └──────┬────────┘
          │                │               │
          │                │               │
          ▼                ▼               ▼
┌─────────────────┐    ┌───────────────┐    ┌─────────────────┐
│                 │    │               │    │                 │
│   IAM Service   │    │   AWS API     │    │   RDS IAM       │
│   (AWS)         │    │   (rds)       │    │   Authentication│
│                 │    │               │    │                 │
└─────────────────┘    └───────────────┘    └─────────────────┘
```

### Sequence Diagram
```
1. Keycloak ECS Task starts
2. Task checks FEATURE_FLAG for IAM auth
3. If IAM auth enabled:
   a. Generate DB Auth Token using rds:GenerateDBAuthToken
   b. Connect to Aurora using IAM token
4. If password auth enabled:
   a. Use existing DB credentials
   b. Connect to Aurora using password
5. Keycloak connects to database and starts
```

### Component Diagram
```
┌─────────────────────────────────────────────────────────────────────┐
│                        Keycloak ECS Task                            │
├─────────────────────────────────────────────────────────────────────┤
│  ┌─────────────┐   ┌─────────────────┐   ┌─────────────────┐        │
│  │             │   │                 │   │                 │        │
│  │ Feature     │──▶│ IAM Auth        │──▶│ Password Auth   │        │
│  │ Flag        │   │ Generator       │   │ Generator       │        │
│  │ Check       │   │ (rds:GenerateDBAuthToken) │   │ (Legacy)      │        │
│  └─────────────┘   └─────────────────┘   └─────────────────┘        │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
                    ┌─────────────────────────────┐
                    │   Aurora MySQL Cluster      │
                    │   (IAM Authentication)      │
                    └─────────────────────────────┘
```

## Data Models

### New Models
```python
# No new Pydantic models needed for this infrastructure change
```

### Model Changes
None needed for this infrastructure change.

## API / CLI Design

### New Endpoints / Commands
None - this is an infrastructure change, not a service API change.

## Configuration Parameters

### New Environment Variables

| Variable Name | Type | Default | Required | Description |
|---------------|------|---------|----------|-------------|
| `KEYCLOAK_DB_AUTH_METHOD` | string | `password` | No | Authentication method: `password` or `iam` |
| `KEYCLOAK_DB_IAM_ENABLED` | boolean | `false` | No | Enable IAM authentication (feature flag) |
| `KEYCLOAK_DB_IAM_ROLE_ARN` | string | None | Yes (if IAM enabled) | IAM role ARN for database access |
| `AWS_REGION` | string | `us-east-1` | Yes | AWS region for RDS operations |

### Settings / Config Class Updates
```python
# In ECS task configuration, environment variables will be updated:
# KEYCLOAK_DB_AUTH_METHOD=password|iam
# KEYCLOAK_DB_IAM_ENABLED=true|false
# KEYCLOAK_DB_IAM_ROLE_ARN=arn:aws:iam::account:role/role-name
# AWS_REGION=us-east-1
```

### Deployment Surface Checklist
List every surface where this parameter must appear (`.env.example`, `docker-compose.yml`, Terraform vars, Helm values, etc.) so an implementer can tick them off later.

- [ ] Terraform variables for database credentials (removed)
- [ ] Terraform IAM role policies (updated)
- [ ] ECS task definition environment variables (updated)
- [ ] Aurora cluster configuration (updated)
- [ ] Keycloak Dockerfile (potentially updated)
- [ ] Deployment documentation

## New Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `boto3` | latest | AWS SDK for generating database auth tokens |
| `botocore` | latest | AWS SDK core components |

This change primarily uses existing AWS infrastructure components and Python libraries already available in the environment.

## Implementation Details

### Step-by-Step Plan (for a future implementer)

#### Step 1: Enable IAM Database Authentication on Aurora MySQL Cluster
**File:** `infra/terraform/modules/rds/main.tf`
**Lines:** New configuration block

```hcl
resource "aws_rds_cluster" "keycloak" {
  # ... existing configuration ...
  
  # Enable IAM database authentication
  iam_database_authentication_enabled = true
  
  # ... existing configuration ...
}
```

#### Step 2: Update IAM Policies for RDS Access
**File:** `infra/terraform/modules/iam/main.tf` 
**Lines:** Updated IAM policy statements

```hcl
resource "aws_iam_policy" "keycloak_ecs_task" {
  name = "keycloak-ecs-task-policy"
  
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # ... existing statements ...
      
      # Allow generating DB auth tokens
      {
        Effect = "Allow"
        Action = [
          "rds:GenerateDBAuthToken"
        ]
        Resource = aws_rds_cluster.keycloak.arn
      }
    ]
  })
}
```

#### Step 3: Modify ECS Task Definition to Support Both Auth Methods
**File:** `infra/terraform/modules/ecs/main.tf`
**Lines:** Updated environment variables and task definition

```hcl
resource "aws_ecs_task_definition" "keycloak" {
  # ... existing configuration ...
  
  container_definitions = jsonencode([
    {
      name = "keycloak"
      # ... existing container config ...
      
      environment = [
        {
          name  = "KEYCLOAK_DB_AUTH_METHOD"
          value = var.db_auth_method
        },
        {
          name  = "KEYCLOAK_DB_IAM_ENABLED"
          value = var.db_iam_enabled
        },
        {
          name  = "KEYCLOAK_DB_IAM_ROLE_ARN"
          value = var.db_iam_role_arn
        },
        {
          name  = "AWS_REGION"
          value = var.aws_region
        }
        # ... existing environment variables ...
      ]
    }
  ])
}
```

#### Step 4: Update Keycloak Container to Generate IAM Tokens
**File:** `docker/keycloak/start.sh` or similar
**Lines:** New script logic for IAM token generation

```bash
#!/bin/bash
# Keycloak startup script with IAM authentication support

if [ "$KEYCLOAK_DB_IAM_ENABLED" = "true" ]; then
  # Generate short-lived IAM auth token
  DB_TOKEN=$(aws rds generate-db-auth-token \
    --hostname "$DB_HOSTNAME" \
    --port "$DB_PORT" \
    --username "$DB_USERNAME" \
    --region "$AWS_REGION")
  
  # Use IAM token for database connection
  export DB_PASSWORD="$DB_TOKEN"
fi

# Start Keycloak with updated credentials
exec /opt/jboss/keycloak/bin/standalone.sh -b 0.0.0.0
```

#### Step 5: Update Terraform Variables
**File:** `infra/terraform/variables.tf`
**Lines:** Updated variable definitions

```hcl
variable "db_auth_method" {
  description = "Database authentication method (password or iam)"
  type        = string
  default     = "password"
}

variable "db_iam_enabled" {
  description = "Enable IAM database authentication"
  type        = bool
  default     = false
}

variable "db_iam_role_arn" {
  description = "IAM role ARN for database access"
  type        = string
  default     = ""
}
```

#### Step 6: Update Terraform Module Calls
**File:** `infra/terraform/main.tf`
**Lines:** Updated module calls

```hcl
module "keycloak" {
  source = "./modules/keycloak"
  
  db_auth_method     = var.db_auth_method
  db_iam_enabled     = var.db_iam_enabled
  db_iam_role_arn    = var.db_iam_role_arn
  # ... other variables ...
}
```

### Error Handling
- If IAM authentication is enabled but required variables are missing, fall back to password auth
- Log authentication method selection for debugging
- Graceful degradation when IAM token generation fails

### Logging
- Log authentication method selection at startup
- Log successful IAM token generation
- Log database connection attempts with method information

## Observability
### Tracing / Metrics / Logging Points
- Authentication method selection (info level)
- IAM token generation success/failure (info/error level)
- Database connection attempts (info level)
- Feature flag status (debug level)

## Scaling Considerations
- IAM tokens are short-lived (15 minutes) - no impact on connection pooling
- No additional infrastructure required for scaling
- IAM authentication is stateless and scales well
- Connection limits remain the same as before

## File Changes

### New Files

| File Path | Description |
|-----------|-------------|
| `docker/keycloak/start.sh` | Updated startup script for IAM token generation |
| `infra/terraform/modules/keycloak/outputs.tf` | New outputs for IAM-enabled configuration |

### Modified Files

| File Path | Lines | Change Description |
|-----------|-------|--------------------|
| `infra/terraform/modules/rds/main.tf` | ~30 | Added `iam_database_authentication_enabled = true` |
| `infra/terraform/modules/iam/main.tf` | ~50 | Added `rds:GenerateDBAuthToken` permission |
| `infra/terraform/modules/ecs/main.tf` | ~80 | Updated environment variables and task definition |
| `infra/terraform/variables.tf` | ~20 | Added new variables for IAM authentication |
| `infra/terraform/main.tf` | ~10 | Updated module calls to pass new parameters |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| New code | ~100 |
| New tests | ~0 |
| Modified code | ~150 |
| **Total** | **~250** |

## Testing Strategy
This section is expanded in the testing.md file.

## Alternatives Considered

### Alternative 1: Complete Migration to IAM Only
**Description:** Remove password authentication entirely and only use IAM authentication
**Pros / Cons:** 
- Pros: More secure, simpler configuration
- Cons: Breaking change for existing deployments, no fallback during transition
**Why Rejected:** Requirement explicitly states password auth must remain available as a fallback

### Alternative 2: Use Secrets Manager for DB Credentials
**Description:** Store DB credentials in AWS Secrets Manager instead of plaintext
**Pros / Cons:** 
- Pros: Better credential management than plaintext
- Cons: Still requires credential rotation, doesn't solve the core security issue
**Why Rejected:** The requirement specifically asks for RDS IAM authentication, not just better credential storage

### Alternative 3: Hybrid Approach with Different IAM Roles
**Description:** Create separate IAM roles for different environments
**Pros / Cons:** 
- Pros: More granular permissions
- Cons: Increased complexity, overkill for this requirement
**Why Rejected:** The requirement is straightforward and doesn't require complex role separation

## Rollout Plan
- Phase 1: Infrastructure changes (enable IAM, update policies, update Terraform)
- Phase 2: Container changes (update Keycloak startup script)
- Phase 3: Feature flag activation (enable IAM auth by default)
- Phase 4: Deprecation of password auth (after sufficient transition period)

## Open Questions
- What is the minimum Keycloak version that supports IAM authentication?
- Are there any existing Keycloak configuration parameters that need to be updated?
- Should the IAM token generation be moved to a separate initialization container?

## References
- AWS RDS IAM Database Authentication Documentation
- Keycloak Database Connection Configuration
- Terraform AWS Provider Documentation for RDS