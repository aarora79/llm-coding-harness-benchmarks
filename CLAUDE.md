# Claude Coding Rules

## Overview

Coding standards and best practices for all code in this repository. These rules prioritize maintainability, simplicity, and modern Python development.

## Core Principles

- Write code with minimal complexity for maximum maintainability and clarity.
- Choose simple, readable solutions over clever or complex implementations.
- Prioritize code that any team member can confidently understand, modify, and debug.

## Technology Stack

### Package Management

- Always use `uv` and `pyproject.toml` for package management. Never use `pip` directly.

### Modern Python Libraries

- **Data Processing**: use `polars` instead of `pandas`.
- **Web APIs**: use `fastapi` instead of `flask`.
- **Formatting/Linting**: use `ruff` for both linting and formatting.
- **Type Checking**: use `mypy` as part of CI/CD.
- **Performance**: leverage modern CPython improvements — recent CPython is significantly faster.

## Code Style

### Function Structure

- Internal/private functions start with an underscore (`_`) and are placed at the top of the file, followed by public functions.
- Keep functions modular — no more than 30-50 lines.
- Two blank lines between function definitions; one parameter per line for readability.

### Type Annotations (Python 3.10+)

Use modern PEP 604/585 syntax — built-in generics and `|` unions — instead of importing from `typing`.

```python
# Good — modern syntax
def process_data(
    sample_size: int | None = None,
    language: str | None = None,
) -> list[dict[str, Any]]:
    ...

# Avoid — legacy syntax
from typing import Optional, List, Dict
def process_data(
    sample_size: Optional[int] = None,
    language: Optional[str] = None,
) -> List[Dict[str, Any]]:
    ...
```

- `X | None` instead of `Optional[X]`.
- `list`, `dict`, `tuple`, `set` directly instead of `List`, `Dict`, `Tuple`, `Set`.

### Class Definitions with Pydantic

Prefer Pydantic `BaseModel` for classes that carry data — it provides validation, type coercion, and serialization. Use modern type hints inside models.

```python
from pydantic import BaseModel, Field

class UserConfig(BaseModel):
    """User configuration settings."""
    username: str = Field(..., min_length=3, max_length=50)
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, str] | None = None
```

### Main Function Pattern

- `main()` acts as a control-flow orchestrator: parse arguments and delegate.
- Do not implement business logic directly in `main()`.

### Command-Line Interface Design

- Use `argparse` with comprehensive help and examples in the epilog.
- Support both CLI args and environment variables, with CLI taking precedence.
- Provide sensible defaults and use special values (e.g. `0` for "all").

### Imports

- Write multi-line imports for readability.

### Constants

- Don't hard-code constants inside functions. Declare trivial ones at the top of the file; for many, create a `constants.py`.

### Logging

- Configure logging with `basicConfig` at `INFO` level, using this format:

  ```python
  import logging

  logging.basicConfig(
      level=logging.INFO,
      format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
  )
  ```

- Add sufficient log messages for debugging; use `logging.debug()` freely and consider a `--debug` flag that sets the level to `DEBUG`.
- Pretty-print dictionaries in trace messages: `logger.info(f"...\n{json.dumps(data, indent=2, default=str)}")`.
- For long-running operations, show configuration at startup, warn about expensive work, and report elapsed time on completion.

### Avoid Deep Nesting

- Limit nesting to 2-3 levels. Use early returns and extract nested logic into well-named functions.

```python
# Good — early returns
def process_data(data):
    if not data:
        return
    for user in data.get("users", []):
        _process_active_user(user)
```

### Decorators and Functional Patterns

- **Use decorators** when they are built-in or widely known (`@property`, `@dataclass`, `@lru_cache`), have a single clear purpose, and don't change behavior dramatically.
- **Use functional patterns** (comprehensions, simple `map`) when they are clearer than a loop.
- **Avoid** chaining multiple complex operations (nested `reduce`/`filter`/`map`/`lambda`) — if the code needs explaining or an entry-level developer would struggle to modify it, write an explicit loop instead.
- Use `@lru_cache` for expensive, pure computations.

### Code Validation

- After editing a Python file, run `uv run python -m py_compile <filename>`.
- After editing a shell script, run `bash -n <filename>`.

## Error Handling

- Use specific exception types; avoid bare `except:`.
- Always log exceptions with context; fail fast and clearly — don't suppress errors silently.
- Use custom exceptions for domain-specific errors.
- Write clear, actionable error messages that include what was attempted and suggest a fix.

```python
def process_data(data: dict) -> dict:
    try:
        return _validate_and_transform(data)
    except ValidationError as e:
        logger.error(f"Validation failed: {e}")
        raise DomainSpecificError(f"Invalid input data: {e}") from e
    except Exception:
        logger.exception("Unexpected error in process_data")
        raise
```

## Testing

- Use `pytest` as the primary framework, with `pytest-cov` for coverage.
- Follow the AAA pattern (Arrange, Act, Assert); one assertion per test where possible.
- Use descriptive test names, fixtures for shared data, and mock external dependencies.
- Test both happy paths and error cases.
- Run the full test suite before submitting a PR and after major features or refactors. A PR with failing tests should never be merged.

```python
class TestFeatureName:
    def test_happy_path(self):
        result = function_under_test({"key": "value"})
        assert result["status"] == "success"

    def test_error_handling(self):
        with pytest.raises(ValueError, match="Invalid input"):
            function_under_test(None)
```

## Async/Await

- Use `async with` for async context managers and `asyncio.gather()` for concurrent operations.
- Handle exceptions in async code; don't mix blocking and async code.
- Use `asyncio.run()` to run async functions from sync code.

## Documentation

Use Google-style docstrings for all public functions, with type hints in the signature, documented exceptions, and usage examples for complex functions.

```python
def calculate_metrics(data: list[float], threshold: float = 0.5) -> dict[str, float]:
    """Calculate statistical metrics for the given data.

    Args:
        data: List of numerical values to analyze.
        threshold: Minimum value to include in calculations.

    Returns:
        Dictionary with mean, std, and count.

    Raises:
        ValueError: If data is empty or non-numeric.
    """
    ...
```

## Security

### General

- Always validate and sanitize inputs; use Pydantic models for request/response validation. Never trust external data.
- Never log sensitive information (passwords, tokens, PII).
- Use environment variables for configuration and secrets — never hardcode secrets in source.
- Use parameterized queries for database operations; keep dependencies updated for security patches.

```python
def get_secret(key: str, default: str | None = None) -> str:
    """Retrieve a secret from an environment variable. Never hardcode secrets."""
    value = os.environ.get(key, default)
    if value is None:
        raise ValueError(f"Required secret '{key}' not found in environment")
    return value
```

### Server Binding

- Never bind a server to `0.0.0.0` unless absolutely necessary. Prefer `127.0.0.1` for local-only access; use a specific private IP if external access is required.

### Bandit Scanning

- Run `uv run bandit -r src/` regularly.
- Handle false positives with `# nosec <code>` comments that include a clear justification.

### Subprocess

- Always use the list form (never `shell=True`), always set a `timeout`, and always handle `TimeoutExpired` and `CalledProcessError`.
- Commands must be hardcoded — never construct them from user input. Pass user data as list arguments, not interpolated into the command.
- `# nosec B603 B607` suppressions must include a justification (e.g. `hardcoded command`).

```python
result = subprocess.run(
    ["nginx", "-s", "reload"],  # nosec B603 B607 - hardcoded command
    capture_output=True,
    text=True,
    check=True,
    timeout=5,
)
```

### SQL

- Always use parameterized queries for values; never use string formatting or concatenation for SQL values.
- Table/column names that can't be parameterized must be validated against an allowlist, with a `# nosec B608` comment documenting the validation.

```python
query = "DELETE FROM table_name WHERE created_at < ?"
cursor.execute(query, (cutoff,))
```

## Development Workflow

Recommended tools: **Ruff** (lint + format), **Bandit** (security), **MyPy** (types), **Pytest** (tests).

Prefer automated pre-commit hooks; otherwise run these before committing:

```bash
uv run ruff check --fix . && uv run ruff format . && uv run bandit -r src/ && uv run mypy src/ && uv run pytest
```

Ruff config targets Python 3.10+ (100-char lines) and auto-modernizes type hints (PEP 604/585) and imports.

### Mandatory Security Gate (`security-check` skill)

Run the `security-check` skill (the Cipher security-engineer persona) as a required gate:

- **Before every commit and before opening or updating a PR.**
- **Whenever a new enhancement, feature, or refactor is added** — both before writing security-sensitive code (to know the rules) and after implementing it (to catch regressions).

The skill reviews the pending diff against a catalog of real-world security anti-patterns (SSRF, broken access control, weak/default secrets, token trust boundaries, missing CSRF, injection, secret/PII log leakage, dependency CVEs, LLM agent execution safety, timing oracles, proxy body integrity), reports findings in the Cipher format, and **fixes any problems it finds**. Do not commit while the verdict is NEEDS REVISION; resolve every blocker first. This gate is in addition to the Bandit scan above, not a replacement for it. See [.claude/skills/security-check/SKILL.md](.claude/skills/security-check/SKILL.md).

## Dependency Management

- Always specify `requires-python` in `pyproject.toml`.
- Pin exact versions for critical dependencies; use ranges for stable libraries.
- Separate dev dependencies from runtime dependencies and document why any version is pinned.

## Project Structure

Standard `src/` layout:

```
project_name/
├── src/project_name/
│   ├── main.py
│   ├── models/
│   ├── services/
│   ├── api/
│   └── utils/
├── tests/
│   ├── unit/
│   └── integration/
├── pyproject.toml
├── README.md
└── .env.example
```

- Keep related functionality together; use clear module names; avoid circular imports.
- Keep a comprehensive `.gitignore` (Python caches, virtualenvs, lint/test caches, IDE/OS files, secrets).

## Environment Configuration

- Use Pydantic Settings for type-safe configuration loaded from environment variables.
- Provide a `.env.example` with all required variables; never commit `.env`. Use sensible defaults where appropriate.

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    app_name: str = "MyApp"
    debug: bool = False
    database_url: str

    class Config:
        env_file = ".env"
```

## Platform Naming

- Always refer to the service as "Amazon Bedrock" (never "AWS Bedrock").

## GitHub Commit and Pull Request Guidelines

- Keep commit messages clean and professional.
- Do not include auto-generated attribution such as "Generated with Claude Code" or "Co-Authored-By: Claude".
- PR descriptions should be professional and focus on the technical changes.

## Documentation Guidelines

- Never add emojis to source code, comments, docstrings, documentation files, log messages, or shell scripts — plain text only. Emojis cause encoding issues, reduce accessibility, and render inconsistently.
- **Do not hard-wrap prose in Markdown files.** Write each paragraph or sentence as a single line and let the editor/renderer soft-wrap it. Hard wrapping creates noisy diffs and breaks tables, lists, and links. Tables, fenced code blocks, and list structure are unaffected.
- A good README includes prerequisites, links to external resources, clear command examples (with env-var variants), a development-workflow section, and performance warnings for time-intensive operations.

## Docker Build and Deployment

When building and pushing containers, use a script with `set -e`, environment-variable configuration with sensible defaults, ECR login, repository creation if missing, and clear progress messages (no emojis). Save the resulting image URI to a file for other scripts.

```bash
#!/bin/bash
set -e
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
AWS_REGION="${AWS_REGION:-us-east-1}"
ECR_REPO_NAME="your_app_name"
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_REPO_URI="$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPO_NAME"

aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"
aws ecr describe-repositories --repository-names "$ECR_REPO_NAME" --region "$AWS_REGION" \
  || aws ecr create-repository --repository-name "$ECR_REPO_NAME" --region "$AWS_REGION"
docker build -t "$ECR_REPO_NAME" .
docker tag "$ECR_REPO_NAME":latest "$ECR_REPO_URI":latest
docker push "$ECR_REPO_URI":latest
```

For ARM64 builds, add QEMU setup with `multiarch/qemu-user-static`.

## GitHub Issue Management

- Check available labels first with `gh label list`, and apply only labels that already exist.
- If a new label would help, suggest it in the issue description or a comment rather than trying to create it during issue creation.

## Scratchpad for Planning & Design

- Keep a `.scratchpad/` folder (added to `.gitignore`) for temporary planning documents — design sketches, task status, analysis notes, and drafts.
- These files are temporary, local-only, and not suitable for long-term documentation.
- Naming: `design-feature-name.md`, `plan-feature-name.md`, `analysis-YYYY-MM-DD.md`, `session-notes-YYYY-MM-DD.md`.

## Summary

- **Simplicity first**: write code an entry-level developer can maintain.
- **Modern Python**: use 3.10+ features (PEP 604/585 type hints).
- **Automated quality**: use pre-commit hooks for consistent formatting.
- **Security**: follow the input-validation, secrets, subprocess, and SQL patterns.
- **Type safety**: clear type annotations with modern syntax.

Always prioritize simplicity and clarity over cleverness.
