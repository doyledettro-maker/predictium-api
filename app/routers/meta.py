"""Model metadata endpoint."""

from typing import Any, Dict, List

from fastapi import APIRouter
from pydantic import BaseModel

from app.services.prediction_service import prediction_service

router = APIRouter(tags=["Meta"])


class MetaResponse(BaseModel):
    """Response model for /meta endpoint."""
    
    model_version: str
    last_run: str
    odds_updated: str
    feature_count: int = 0
    training_games: int = 0
    training_seasons: List[str] = []
    api_version: str = "1.0.0"


@router.get("/meta", response_model=MetaResponse)
async def get_model_meta() -> Dict[str, Any]:
    """
    Get model metadata.
    
    Returns information about the prediction model including
    version, last run time, and odds update status.
    
    No authentication required.
    
    Returns:
        MetaResponse with model information.
    """
    return await prediction_service.get_meta()
