"""
API routes for the sample application.
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health_check():
    """Health check endpoint."""
    return {"status": "ok"}


@router.get("/ping")
def ping():
    """Ping endpoint."""
    return {"message": "pong"}
