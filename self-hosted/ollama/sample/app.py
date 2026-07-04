"""
FastAPI application entry point.

Note: For production deployments, add CORSMiddleware with restricted origins
and security headers (X-Content-Type-Options, X-Frame-Options,
Strict-Transport-Security, etc.).
"""

from fastapi import FastAPI

from .api.routes import router

app = FastAPI(title="Sample App", version="1.0.0")
app.include_router(router)


@app.get("/")
def root():
    """Root endpoint."""
    return {"message": "Sample app is running"}
