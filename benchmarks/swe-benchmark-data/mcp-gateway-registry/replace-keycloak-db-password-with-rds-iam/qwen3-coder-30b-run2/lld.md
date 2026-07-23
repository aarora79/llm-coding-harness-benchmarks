# Low-Level Design: Replace Keycloak Database Password with RDS IAM Authentication

*Created: 2026-07-22*
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
The Keycloak service currently uses static database credentials (username/password) to connect to an Aurora MySQL cluster. This approach has security implications as static passwords are harder to rotate and manage securely. We need to migrate to RDS IAM authentication which provides short-lived credentials and better security practices.

### Goals
- Replace static database password with RDS IAM authentication for Keycloak
- Enable IAM database authentication on Aurora MySQL cluster
- Update Keycloak ECS task to generate short-lived IAM auth tokens
- Maintain backwards compatibility with password authentication as feature flag
- Follow AWS ECS + Terraform deployment patterns (no Helm/EKS)

### Non-Goals
- Upgrade Keycloak version
- Change Keycloak application configuration beyond database connection
- Modify other services in the deployment
- Change deployment mechanisms beyond ECS/Terraform

## Codebase Analysis

### Key Files Reviewed

| File/Directory | Purpose | Relevance to This Change |
|----------------|---------|--------------------------|
| `terraform/aws-ecs/` | AWS ECS and infrastructure provisioning | Contains Keycloak ECS task definitions and RDS configuration |
| `terraform/aws-ecs/variables.tf` | Terraform variables | Will need new variables for IAM auth |
| `terraform/aws-ecs/keycloak-task.tf` | Keycloak ECS task definition | Key file to modify for IAM auth integration |
| `terraform/aws-ecs/rds.tf` | RDS Aurora cluster configuration | Need to enable IAM database authentication |
| `terraform/aws-ecs/iam.tf` | IAM policies and roles | Need to update roles for new permissions |
| `docker/keycloak/` | Keycloak container configuration | May need updates for IAM auth support |
| `docker-compose.yml` | Docker Compose configuration | Possibly used for local development |

### Existing Patterns Identified
1. **Infrastructure-as-Code**: Uses Terraform for AWS infrastructure provisioning
2. **ECS Task Definitions**: Standard ECS task definitions with environment variables
3. **IAM Role Management**: IAM policies and roles for service permissions
4. **Feature Flags**: Pattern for enabling/disabling features

### Integration Points

| Component | Integration Type | Details |
|-----------|------------------|---------|
| Aurora MySQL Cluster | Depends on | Must enable IAM database authentication |
| Keycloak ECS Task | Extends | Must generate IAM auth tokens |
| IAM Roles | Depends on | Must grant rds:GenerateDBAuthToken permissions |
| Terraform Variables | Modifies | New variables for IAM auth configuration |

### Constraints and Limitations Discovered
- Must maintain backwards compatibility with existing password auth
- Only ECS/Terraform deployment patterns allowed (no Helm/EKS)
- No Keycloak version changes permitted
- Need to support both authentication methods during transition

## Architecture

### System Context Diagram
```
┌─────────────────┐    ┌─────────────────────┐    ┌─────────────────┐
│                 │    │                     │    │                 │
│   Keycloak      │    │   Aurora MySQL      │    │   IAM Service   │
│   ECS Task      │───▶│   (RDS)             │◀───│   (AWS)         │
│                 │    │                     │    │                 │
└─────────────────┘    └─────────┬───────────┘    └─────────────────┘
                              │
                              ▼
                    ┌─────────────────────┐
                    │   Terraform         │
                    │   Provisioning      │
                    └─────────────────────┘
```

### Sequence Diagram
```
1. Keycloak ECS Task starts
2. Check FEATURE_FLAG for auth method
3. If IAM_AUTH_ENABLED:
   a. Generate short-lived IAM auth token via rds:GenerateDBAuthToken
   b. Connect to Aurora using IAM token
4. Else (password auth):
   a. Use existing DB_PASSWORD from environment
   b. Connect to Aurora using password
5. Database connection established
```

### Component Diagram
```
┌─────────────────────────────────────────────────────────────────────┐
│                        Keycloak ECS Task                            │
├─────────────────────────────────────────────────────────────────────┤
│  ┌─────────────┐   ┌──────────────────────┐   ┌──────────────────┐ │
│  │             │   │                      │   │                  │ │
│  │ Feature     │──▶│  Auth Method         │──▶│  Connection      │ │
│  │ Flag        │   │  Selector            │   │  Manager         │ │
│  │ Check       │   │                      │   │                  │ │
│  └─────────────┘   └──────────────────────┘   └──────────────────┘ │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │  IAM Auth Generator                                         │  │
│  │  - Generates short-lived tokens via rds:GenerateDBAuthToken │  │
│  └─────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

## Data Models

### New Models
```python
class KeycloakDatabaseConfig(BaseModel):
    """Database configuration for Keycloak with support for both auth methods."""
    
    db_host: str = Field(..., description="Database hostname")
    db_port: int = Field(default=3306, description="Database port")
    db_name: str = Field(..., description="Database name")
    db_username: str = Field(..., description="Database username")
    db_password: str | None = Field(None, description="Database password (deprecated)")
    iam_auth_enabled: bool = Field(default=False, description="Enable IAM authentication")
    feature_flag_enabled: bool = Field(default=True, description="Enable feature flag")
```

### Model Changes
- Existing Keycloak configuration models will need to support both authentication methods
- New configuration parameters for IAM authentication

## API / CLI Design

### New Endpoints / Commands
**Description:** No new endpoints, but new configuration options for Keycloak

**Request / Invocation:**
```bash
# Environment variables for IAM auth
export KEYCLOAK_DB_IAM_AUTH_ENABLED=true
export KEYCLOAK_FEATURE_FLAG_ENABLED=true
```

**Expected Response / Output:**
Keycloak connects successfully to Aurora using either password or IAM authentication

**Error Cases:**
- 400 / nonzero exit: When database connection fails with invalid credentials
- 500 / nonzero exit: When IAM token generation fails

## Configuration Parameters

### New Environment Variables

| Variable Name | Type | Default | Required | Description |
|---------------|------|---------|----------|-------------|
| `KEYCLOAK_DB_IAM_AUTH_ENABLED` | bool | `false` | No | Enable RDS IAM authentication for Keycloak |
| `KEYCLOAK_FEATURE_FLAG_ENABLED` | bool | `true` | No | Enable feature flag for backward compatibility |
| `KEYCLOAK_DB_HOST` | str | `""` | Yes | Database hostname |
| `KEYCLOAK_DB_PORT` | int | `3306` | No | Database port |
| `KEYCLOAK_DB_NAME` | str | `""` | Yes | Database name |
| `KEYCLOAK_DB_USERNAME` | str | `""` | Yes | Database username |
| `KEYCLOAK_DB_PASSWORD` | str | `""` | No | Database password (deprecated) |

### Settings / Config Class Updates
```python
class KeycloakSettings(BaseSettings):
    """Keycloak configuration settings."""
    
    # Database settings
    db_host: str = Field(..., description="Database hostname")
    db_port: int = Field(default=3306, description="Database port")
    db_name: str = Field(..., description="Database name")
    db_username: str = Field(..., description="Database username")
    db_password: str | None = Field(None, description="Database password (deprecated)")
    
    # IAM authentication settings
    db_iam_auth_enabled: bool = Field(default=False, description="Enable IAM authentication")
    feature_flag_enabled: bool = Field(default=True, description="Enable feature flag")
    
    # AWS settings
    aws_region: str = Field(default="us-east-1", description="AWS region")
    
    class Config:
        env_file = ".env"
```

### Deployment Surface Checklist
List every surface where this parameter must appear (`.env.example`, `docker-compose.yml`, Terraform vars, ECS task definitions, etc.) so an implementer can tick them off later.

- [ ] `.env.example` - Add new IAM auth environment variables
- [ ] `terraform/aws-ecs/variables.tf` - Add new Terraform variables for IAM auth
- [ ] `terraform/aws-ecs/keycloak-task.tf` - Update ECS task definition with IAM auth support
- [ ] `terraform/aws-ecs/rds.tf` - Enable IAM database authentication on Aurora cluster
- [ ] `terraform/aws-ecs/iam.tf` - Update IAM policies for rds:GenerateDBAuthToken
- [ ] `docker/keycloak/Dockerfile` - Update container configuration if needed
- [ ] `docker-compose.yml` - Update for local development if needed

## New Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `boto3` | latest | AWS SDK for generating IAM auth tokens |
| `botocore` | latest | AWS SDK core components |
| `pydantic` | latest | Configuration model validation |

This change uses only existing dependencies, with boto3 being added for AWS integration.

## Implementation Details

### Step-by-Step Plan (for a future implementer)

#### Step 1: Enable IAM Database Authentication on Aurora Cluster
**File:** `terraform/aws-ecs/rds.tf`
**Lines:** ~100-150

```hcl
resource "aws_rds_cluster" "keycloak" {
  # ... existing configuration ...
  
  # Enable IAM database authentication
  iam_database_authentication_enabled = true
  
  # ... rest of configuration ...
}
```

#### Step 2: Update Terraform Variables
**File:** `terraform/aws-ecs/variables.tf`
**Lines:** ~10-30

```hcl
variable "keycloak_db_iam_auth_enabled" {
  description = "Enable RDS IAM authentication for Keycloak"
  type        = bool
  default     = false
}

variable "keycloak_feature_flag_enabled" {
  description = "Enable feature flag for backward compatibility"
  type        = bool
  default     = true
}
```

#### Step 3: Update IAM Policies
**File:** `terraform/aws-ecs/iam.tf`
**Lines:** ~50-80

```hcl
resource "aws_iam_role_policy" "keycloak_rds_access" {
  name = "keycloak-rds-access"
  role = aws_iam_role.keycloak.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "rds:GenerateDBAuthToken"
        ]
        Resource = "*"
      }
    ]
  })
}
```

#### Step 4: Update Keycloak ECS Task Definition
**File:** `terraform/aws-ecs/keycloak-task.tf`
**Lines:** ~100-150

```hcl
resource "aws_ecs_task_definition" "keycloak" {
  # ... existing configuration ...
  
  container_definitions = jsonencode([
    {
      name  = "keycloak"
      image = var.keycloak_image
      
      # Add environment variables for IAM auth
      environment = [
        {
          name  = "KEYCLOAK_DB_IAM_AUTH_ENABLED"
          value = var.keycloak_db_iam_auth_enabled
        },
        {
          name  = "KEYCLOAK_FEATURE_FLAG_ENABLED"
          value = var.keycloak_feature_flag_enabled
        },
        {
          name  = "KEYCLOAK_DB_HOST"
          value = aws_rds_cluster.keycloak.endpoint
        },
        {
          name  = "KEYCLOAK_DB_PORT"
          value = aws_rds_cluster.keycloak.port
        },
        {
          name  = "KEYCLOAK_DB_NAME"
          value = var.keycloak_db_name
        },
        {
          name  = "KEYCLOAK_DB_USERNAME"
          value = var.keycloak_db_username
        }
      ]
      
      # ... rest of container definition ...
    }
  ])
}
```

#### Step 5: Update Keycloak Application Code (if needed)
**File:** `src/keycloak/config.py` or similar
**Lines:** ~20-50

```python
import boto3
from botocore.exceptions import ClientError

def get_database_connection_params():
    """Get database connection parameters supporting both auth methods."""
    
    # Check feature flag
    if os.getenv("KEYCLOAK_FEATURE_FLAG_ENABLED", "true").lower() == "false":
        # Use legacy password auth
        return {
            "host": os.getenv("KEYCLOAK_DB_HOST"),
            "port": int(os.getenv("KEYCLOAK_DB_PORT", "3306")),
            "database": os.getenv("KEYCLOAK_DB_NAME"),
            "user": os.getenv("KEYCLOAK_DB_USERNAME"),
            "password": os.getenv("KEYCLOAK_DB_PASSWORD")
        }
    
    # Check if IAM auth is enabled
    if os.getenv("KEYCLOAK_DB_IAM_AUTH_ENABLED", "false").lower() == "true":
        # Generate IAM auth token
        try:
            rds_client = boto3.client("rds", region_name=os.getenv("AWS_REGION", "us-east-1"))
            token = rds_client.generate_db_auth_token(
                DBHostname=os.getenv("KEYCLOAK_DB_HOST"),
                Port=int(os.getenv("KEYCLOAK_DB_PORT", "3306")),
                DBUsername=os.getenv("KEYCLOAK_DB_USERNAME")
            )
            
            return {
                "host": os.getenv("KEYCLOAK_DB_HOST"),
                "port": int(os.getenv("KEYCLOAK_DB_PORT", "3306")),
                "database": os.getenv("KEYCLOAK_DB_NAME"),
                "user": os.getenv("KEYCLOAK_DB_USERNAME"),
                "password": token
            }
        except ClientError as e:
            logger.error(f"Failed to generate IAM auth token: {e}")
            raise
    else:
        # Fallback to password auth
        return {
            "host": os.getenv("KEYCLOAK_DB_HOST"),
            "port": int(os.getenv("KEYCLOAK_DB_PORT", "3306")),
            "database": os.getenv("KEYCLOAK_DB_NAME"),
            "user": os.getenv("KEYCLOAK_DB_USERNAME"),
            "password": os.getenv("KEYCLOAK_DB_PASSWORD")
        }
```

### Error Handling
- When IAM token generation fails, fallback to password authentication if feature flag is enabled
- When database connection fails, log error and fail gracefully
- Validate that required environment variables are present

### Logging
- Log when IAM authentication is used vs password authentication
- Log errors during IAM token generation
- Log successful connection establishment

## Observability
### Tracing / Metrics / Logging Points
- Log authentication method used at connection time
- Log IAM token generation success/failure
- Log database connection attempts and success/failure
- Track feature flag usage statistics

## Scaling Considerations
- Current load assumptions: Single Keycloak instance
- Horizontal scaling: Each instance needs IAM credentials
- Bottlenecks: IAM token generation rate limits
- Caching strategy: Short-lived tokens are generated on-demand

## File Changes

### New Files

| File Path | Description |
|-----------|-------------|
| `terraform/aws-ecs/variables.tf` | Add new IAM authentication variables |
| `terraform/aws-ecs/iam.tf` | Update IAM policies for rds:GenerateDBAuthToken |
| `terraform/aws-ecs/keycloak-task.tf` | Update ECS task definition for IAM auth support |

### Modified Files

| File Path | Lines | Change Description |
|-----------|-------|--------------------|
| `terraform/aws-ecs/rds.tf` | ~100-150 | Enable IAM database authentication on Aurora cluster |
| `src/keycloak/config.py` | ~20-50 | Add logic to support both authentication methods |
| `.env.example` | ~1-10 | Add new environment variables for IAM auth |

### Estimated Lines of Code

| Category | Lines |
|----------|-------|
| New code | ~50 |
| New tests | ~20 |
| Modified code | ~30 |
| **Total** | **~100** |

## Testing Strategy
See detailed testing plan in `testing.md`

## Alternatives Considered

### Alternative 1: Complete Replacement with IAM Only
**Description:** Remove password authentication entirely and only support IAM
**Pros / Cons:** 
- Simpler implementation
- Better security
- No backward compatibility
**Why Rejected:** Requirement states need to maintain backward compatibility

### Alternative 2: Separate Deployment
**Description:** Deploy a separate Keycloak instance with IAM auth
**Pros / Cons:** 
- Isolated changes
- Complex deployment
- Longer transition period
**Why Rejected:** Adds operational complexity and doesn't align with single-service deployment pattern

### Comparison Matrix

| Criteria | Chosen | Alt 1 | Alt 2 |
|----------|--------|-------|-------|
| Security | High | High | High |
| Complexity | Medium | Low | High |
| Backward Compatibility | Excellent | Poor | Fair |
| Implementation Time | Medium | Fast | Slow |
| Operational Impact | Low | High | High |

## Rollout Plan
- Phase 1: Infrastructure changes (enable IAM auth on RDS, update IAM policies)
- Phase 2: Application changes (update Keycloak to support both auth methods)
- Phase 3: Feature flag activation (enable IAM auth by default)
- Phase 4: Password auth deprecation (schedule removal of password auth support)

## Open Questions
- What is the exact Keycloak version being used?
- Are there specific AWS regions or account configurations that affect the implementation?
- How should the transition period be managed?

## References
- AWS RDS IAM Authentication Documentation
- Keycloak Database Configuration Guide
- Terraform AWS Provider Documentation