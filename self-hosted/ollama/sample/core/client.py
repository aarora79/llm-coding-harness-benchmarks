"""
HTTP client for external API calls.

Note: In production, configure CORS middleware and security headers
on the FastAPI application that consumes this client.
"""

import os
import re

import httpx

BASE_URL = os.environ.get("API_BASE_URL", "https://api.example.com")

# Default timeout (seconds) for all HTTP requests — prevents indefinite hangs
DEFAULT_TIMEOUT = httpx.Timeout(30.0)

# Pattern for allowed endpoint characters: alphanumeric, hyphens, slashes, dots, underscores
_SAFE_ENDPOINT_RE = re.compile(r"^[a-zA-Z0-9/_.\-]+$")


def _validate_endpoint(endpoint: str) -> str:
    """Validate and sanitize an API endpoint path.

    Args:
        endpoint: API endpoint path (e.g., 'users/123')

    Returns:
        Sanitized endpoint string

    Raises:
        ValueError: If the endpoint contains unsafe characters or patterns
    """
    if not endpoint or not isinstance(endpoint, str):
        raise ValueError("endpoint must be a non-empty string")

    # Strip leading/trailing whitespace and slashes
    endpoint = endpoint.strip().strip("/")

    if not endpoint:
        raise ValueError("endpoint cannot be empty")

    # Block path traversal
    if ".." in endpoint:
        raise ValueError("endpoint must not contain path traversal sequences (..)")

    # Block absolute URLs / scheme injection
    if "://" in endpoint:
        raise ValueError("endpoint must be a relative path, not an absolute URL")

    # Allow only safe characters
    if not _SAFE_ENDPOINT_RE.match(endpoint):
        raise ValueError(
            f"endpoint contains invalid characters: {endpoint!r}. "
            "Only alphanumeric, hyphens, slashes, dots, and underscores are allowed."
        )

    return endpoint


def fetch_data(endpoint: str) -> dict:
    """Fetch data from the external API synchronously.

    Args:
        endpoint: API endpoint path (e.g., 'users/123')

    Returns:
        Parsed JSON response as a dict

    Raises:
        ValueError: If the endpoint is invalid
        httpx.HTTPStatusError: If the response status indicates an error
    """
    safe_endpoint = _validate_endpoint(endpoint)
    with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
        response = client.get(f"{BASE_URL}/{safe_endpoint}")
        response.raise_for_status()
        return response.json()


async def fetch_data_async(endpoint: str) -> dict:
    """Fetch data from the external API asynchronously.

    Args:
        endpoint: API endpoint path

    Returns:
        Parsed JSON response as a dict

    Raises:
        ValueError: If the endpoint is invalid
        httpx.HTTPStatusError: If the response status indicates an error
    """
    safe_endpoint = _validate_endpoint(endpoint)
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        response = await client.get(f"{BASE_URL}/{safe_endpoint}")
        response.raise_for_status()
        return response.json()
