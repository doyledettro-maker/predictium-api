"""Health check endpoint."""

from fastapi import APIRouter

router = APIRouter(tags=["Health"])


@router.get("/health")
async def health_check():
    """
    Health check endpoint.
    
    Returns a simple status indicator for load balancers
    and monitoring systems.
    
    Returns:
        Dict with status "healthy".
    """
    return {"status": "healthy"}
