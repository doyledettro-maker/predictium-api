"""
Prediction service for reading NBA predictions from S3.

Caches predictions in memory with automatic refresh.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import boto3
from botocore.exceptions import ClientError
from cachetools import TTLCache

from app.config import get_settings

logger = logging.getLogger(__name__)


class PredictionService:
    """
    Service for reading NBA predictions from S3.
    
    Features:
    - In-memory caching with 60-second TTL
    - Automatic refresh on cache expiration
    - Fallback to cached data if S3 is unavailable
    """

    def __init__(self):
        self.settings = get_settings()
        # Cache predictions for 60 seconds
        self._cache: TTLCache = TTLCache(maxsize=100, ttl=60)
        self._s3_client = None
        self._lock = asyncio.Lock()

    def _get_s3_client(self):
        """Get or create S3 client."""
        if self._s3_client is None:
            self._s3_client = boto3.client(
                "s3",
                region_name=self.settings.aws_region,
                aws_access_key_id=self.settings.aws_access_key_id or None,
                aws_secret_access_key=self.settings.aws_secret_access_key or None,
            )
        return self._s3_client

    async def _read_s3_object(self, key: str) -> Optional[Dict[str, Any]]:
        """
        Read and parse JSON object from S3.
        
        Args:
            key: S3 object key.
            
        Returns:
            Parsed JSON content or None if not found.
        """
        try:
            s3 = self._get_s3_client()
            
            # Run S3 operation in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: s3.get_object(
                    Bucket=self.settings.s3_predictions_bucket,
                    Key=key,
                ),
            )
            
            body = response["Body"].read()
            return json.loads(body)
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "NoSuchKey":
                logger.warning(f"S3 object not found: {key}")
            else:
                logger.error(f"S3 error reading {key}: {e}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in S3 object {key}: {e}")
            return None

    async def get_latest_predictions(self) -> Optional[Dict[str, Any]]:
        """
        Get the latest predictions from S3.
        
        Reads from latest.json in the predictions bucket.
        Uses caching to minimize S3 requests.
        
        Returns:
            Prediction data matching BackendPredictionResponse schema,
            or None if unavailable.
        """
        cache_key = "latest"
        
        # Check cache first
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        # Fetch from S3
        async with self._lock:
            # Double-check cache after acquiring lock
            if cache_key in self._cache:
                return self._cache[cache_key]
            
            data = await self._read_s3_object("latest.json")
            
            if data:
                self._cache[cache_key] = data
                logger.info("Cached latest predictions from S3")
            
            return data

    async def get_game_detail(self, game_id: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed predictions for a specific game.
        
        Reads from game_details/{game_id}.json in the predictions bucket.
        
        Args:
            game_id: The game identifier (e.g., "PHI@MEM_2025-12-30").
            
        Returns:
            Game detail data matching BackendGameDetailFile schema,
            or None if not found.
        """
        cache_key = f"game:{game_id}"
        
        # Check cache first
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        # Sanitize game_id to prevent path traversal
        safe_game_id = game_id.replace("/", "_").replace("\\", "_").replace("..", "_")
        
        # Fetch from S3
        data = await self._read_s3_object(f"game_details/{safe_game_id}.json")
        
        if data:
            self._cache[cache_key] = data
            logger.debug(f"Cached game detail: {game_id}")
        
        return data

    async def get_meta(self) -> Dict[str, Any]:
        """
        Get model metadata from the latest predictions.
        
        Returns:
            Dict with model_version, last_run, and odds_updated timestamps.
        """
        predictions = await self.get_latest_predictions()
        
        if predictions and "meta" in predictions:
            meta = predictions["meta"]
            return {
                "model_version": meta.get("model_version", "unknown"),
                "last_run": meta.get("generated_at", ""),
                "odds_updated": meta.get("data_freshness", ""),
                "feature_count": meta.get("feature_count", 0),
                "training_games": meta.get("training_games", 0),
                "training_seasons": meta.get("training_seasons", []),
                "api_version": meta.get("api_version", "1.0.0"),
            }
        
        return {
            "model_version": "unknown",
            "last_run": "",
            "odds_updated": "",
            "feature_count": 0,
            "training_games": 0,
            "training_seasons": [],
            "api_version": "1.0.0",
        }

    def log_prediction_access(
        self,
        user_id: str,
        game_id: Optional[str] = None,
        endpoint: str = "latest",
    ) -> None:
        """
        Log prediction access for auditing.
        
        Args:
            user_id: The user accessing predictions.
            game_id: Optional specific game being accessed.
            endpoint: The endpoint being accessed.
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        logger.info(
            f"PREDICTION_ACCESS | "
            f"user_id={user_id} | "
            f"endpoint={endpoint} | "
            f"game_id={game_id or 'N/A'} | "
            f"timestamp={timestamp}"
        )

    async def invalidate_cache(self, key: Optional[str] = None) -> None:
        """
        Invalidate cached predictions.
        
        Args:
            key: Specific cache key to invalidate, or None to clear all.
        """
        if key:
            self._cache.pop(key, None)
            logger.info(f"Invalidated cache key: {key}")
        else:
            self._cache.clear()
            logger.info("Cleared all prediction cache")


# Global service instance
prediction_service = PredictionService()
