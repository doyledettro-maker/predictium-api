"""Prediction endpoints."""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status

from app.dependencies import get_current_user, get_current_user_with_db
from app.models.user import User
from app.services.game_detail_serializer import serialize_game_detail
from app.services.prediction_service import is_valid_game_id, prediction_service

router = APIRouter(prefix="/predictions", tags=["Predictions"])


@router.get("/latest")
async def get_latest_predictions(
    user_info: Dict[str, str] = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Get the latest predictions for today and tomorrow.
    
    Returns the full prediction response including:
    - Today's games with predictions
    - Tomorrow's games with predictions
    - Model metadata
    - Summary statistics
    
    Requires authentication.
    
    Returns:
        BackendPredictionResponse structure.
        
    Raises:
        HTTPException(503): If predictions are unavailable.
    """
    # Log access for auditing
    prediction_service.log_prediction_access(
        user_id=user_info["sub"],
        endpoint="latest",
    )
    
    predictions = await prediction_service.get_latest_predictions()
    
    if not predictions:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Predictions are currently unavailable",
        )
    
    return predictions


@router.get("/games/{game_id}")
async def get_game_detail(
    game_id: str,
    user: User = Depends(get_current_user_with_db),
) -> Dict[str, Any]:
    """
    Get detailed predictions for a specific game.
    
    Returns extended game information including:
    - Full player adjustments breakdown
    - Scenario analysis
    - Player impact details
    - Recent form data
    - Prediction history
    
    Requires authentication.
    Pro/Elite plans get additional detailed data.
    
    Args:
        game_id: The game identifier (e.g., "PHI@MEM_2025-12-30").
        
    Returns:
        BackendGameDetailFile structure.
        
    Raises:
        HTTPException(404): If game not found.
        HTTPException(403): If user lacks required plan for detailed data.
    """
    # Log access for auditing. A refused id is logged as a quoted, truncated
    # repr so it cannot inject lines into the audit log.
    prediction_service.log_prediction_access(
        user_id=str(user.id),
        game_id=game_id if is_valid_game_id(game_id) else f"invalid:{game_id[:64]!r}",
        endpoint="game_detail",
    )
    
    game_detail = await prediction_service.get_game_detail(game_id)
    
    if not game_detail:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Game not found: {game_id}",
        )
    
    # Every response is built from an allowlist (game_detail_serializer):
    # premium users get the model detail, free users the basic prediction,
    # and neither ever gets a market line, a book or a field nobody named.
    premium = bool(user.subscription and user.subscription.has_premium_access)
    return serialize_game_detail(game_detail, premium=premium)
