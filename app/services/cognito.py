"""
Cognito JWT validation service.

Fetches JWKS from Cognito and validates JWT tokens.
Uses caching for JWKS to avoid repeated network calls.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import httpx
import jwt
from cachetools import TTLCache
from jwt import ExpiredSignatureError, PyJWTError

from app.config import get_settings

logger = logging.getLogger(__name__)


class CognitoService:
    """
    Service for validating Cognito JWT tokens.
    
    Handles:
    - Fetching and caching JWKS (JSON Web Key Set)
    - Validating JWT signature, expiration, audience, and issuer
    - Extracting user claims from validated tokens
    """

    def __init__(self):
        self.settings = get_settings()
        # Cache JWKS for 1 hour
        self._jwks_cache: TTLCache = TTLCache(maxsize=1, ttl=3600)
        self._http_client: Optional[httpx.AsyncClient] = None

    async def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=10.0)
        return self._http_client

    async def _fetch_jwks(self) -> Dict[str, Any]:
        """
        Fetch JWKS from Cognito.
        
        Returns:
            Dict containing the JWKS keys.
            
        Raises:
            RuntimeError: If JWKS cannot be fetched.
        """
        cache_key = "jwks"
        
        # Check cache first
        if cache_key in self._jwks_cache:
            return self._jwks_cache[cache_key]
        
        try:
            client = await self._get_http_client()
            response = await client.get(self.settings.cognito_jwks_url)
            response.raise_for_status()
            jwks = response.json()
            
            # Cache the JWKS
            self._jwks_cache[cache_key] = jwks
            logger.info("Fetched and cached Cognito JWKS")
            
            return jwks
        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch JWKS: {e}")
            raise RuntimeError(f"Failed to fetch JWKS from Cognito: {e}")

    def _get_signing_key(self, jwks: Dict[str, Any], token: str) -> Optional[Dict[str, Any]]:
        """
        Get the signing key from JWKS that matches the token's kid.
        
        Args:
            jwks: The JWKS containing all keys.
            token: The JWT token to find the key for.
            
        Returns:
            The matching key dict or None if not found.
        """
        try:
            # Get the key ID from the token header
            unverified_header = jwt.get_unverified_header(token)
            kid = unverified_header.get("kid")
            
            if not kid:
                logger.warning("Token missing 'kid' header")
                return None
            
            # Find the matching key
            for key in jwks.get("keys", []):
                if key.get("kid") == kid:
                    return key
            
            logger.warning(f"No matching key found for kid: {kid}")
            return None
        except PyJWTError as e:
            logger.error(f"Error parsing token header: {e}")
            return None

    async def validate_token(self, token: str) -> Dict[str, Any]:
        """
        Validate a Cognito JWT token.
        
        Args:
            token: The JWT token to validate.
            
        Returns:
            Dict containing the validated token claims including:
            - sub: User ID (Cognito subject)
            - email: User's email address
            - Other standard JWT claims
            
        Raises:
            ValueError: If the token is invalid, expired, or verification fails.
        """
        # Fetch JWKS
        jwks = await self._fetch_jwks()
        
        # Get the signing key
        signing_key = self._get_signing_key(jwks, token)
        if not signing_key:
            raise ValueError("Unable to find appropriate signing key")
        
        try:
            # Verify and decode the token. The algorithm is pinned to the
            # JWK's own RS256; the audience is checked below because Cognito
            # puts it in different claims for ID and access tokens.
            claims = jwt.decode(
                token,
                jwt.PyJWK(signing_key, algorithm="RS256"),
                algorithms=["RS256"],
                issuer=self.settings.cognito_issuer,
                options={
                    "verify_signature": True,
                    "verify_aud": False,
                    "verify_iss": True,
                    "verify_exp": True,
                    "require": ["exp", "iss", "sub", "token_use"],
                },
            )
        except ExpiredSignatureError:
            logger.warning("Token has expired")
            raise ValueError("Token has expired")
        except PyJWTError as e:
            logger.warning(f"JWT validation failed: {e}")
            raise ValueError(f"Token validation failed: {e}")

        # ID tokens carry the app client in `aud`, access tokens in
        # `client_id`. python-jose skipped the audience check when `aud` was
        # absent, so an access token minted for any client of the pool passed.
        token_use = claims.get("token_use")
        if token_use == "id":
            client = claims.get("aud")
        elif token_use == "access":
            client = claims.get("client_id")
        else:
            raise ValueError(f"Invalid token_use: {token_use}")
        if client != self.settings.cognito_client_id:
            raise ValueError("Token was not issued for this app client")

        logger.debug(f"Successfully validated token for user: {claims.get('sub')}")
        return claims

    async def get_user_info(self, token: str) -> Dict[str, str]:
        """
        Extract user info from a validated token.
        
        Args:
            token: The JWT token to extract info from.
            
        Returns:
            Dict with 'sub' (user ID) and 'email' keys.
            
        Raises:
            ValueError: If token validation fails or required claims are missing.
        """
        claims = await self.validate_token(token)
        
        sub = claims.get("sub")
        email = claims.get("email")
        
        if not sub:
            raise ValueError("Token missing 'sub' claim")
        if not email:
            # Try to get email from cognito:username if email isn't present
            email = claims.get("cognito:username", "")
        
        return {
            "sub": sub,
            "email": email,
        }

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()


# Global service instance
cognito_service = CognitoService()
