"""Prediction endpoints."""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status

from app.dependencies import get_current_user, get_current_user_with_db
from app.models.user import User
from app.services.prediction_service import prediction_service

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
    # Log access for auditing
    prediction_service.log_prediction_access(
        user_id=str(user.id),
        game_id=game_id,
        endpoint="game_detail",
    )
    
    game_detail = await prediction_service.get_game_detail(game_id)
    
    if not game_detail:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Game not found: {game_id}",
        )
    
    # Filter data based on subscription plan
    if user.subscription and not user.subscription.has_pro_access:
        # Free tier: return limited data
        game_detail = _filter_for_free_tier(game_detail)
    elif user.subscription and not user.subscription.has_elite_access:
        # Pro tier: return most data, but not elite-only features
        game_detail = _filter_for_pro_tier(game_detail)
    
    return game_detail


def _filter_for_free_tier(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Filter game detail data for free tier users.
    
    Free tier gets basic predictions only.
    """
    return {
        "prediction_id": data.get("prediction_id"),
        "game_id": data.get("game_id"),
        "prediction_timestamp": data.get("prediction_timestamp"),
        "teams": data.get("teams"),
        "predictions": {
            "final_spread": data.get("predictions", {}).get("final_spread"),
            "final_total": data.get("predictions", {}).get("final_total"),
            "final_home_win_prob": data.get("predictions", {}).get("final_home_win_prob"),
            "confidence": data.get("predictions", {}).get("confidence"),
        },
        "context": data.get("context"),
    }


def _filter_for_pro_tier(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Filter game detail data for Pro tier users.
    
    Pro tier gets most data except elite-only features.
    """
    # Pro gets everything except prediction_history (elite only)
    filtered = dict(data)
    filtered.pop("prediction_history", None)
    return filtered
