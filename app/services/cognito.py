"""
Cognito JWT validation service.

Fetches JWKS from Cognito and validates JWT tokens.
Uses caching for JWKS to avoid repeated network calls.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import httpx
from cachetools import TTLCache
from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError

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
        except JWTError as e:
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
            # Verify the signature, issuer and expiry exactly as before. The
            # audience is checked by hand below, because Cognito puts the app
            # client in `aud` on an ID token and in `client_id` on an access
            # token, and more than one app client is allowed.
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=["RS256"],
                issuer=self.settings.cognito_issuer,
                options={
                    "verify_signature": True,
                    "verify_aud": False,
                    "verify_iss": True,
                    "verify_exp": True,
                    "require_exp": True,
                    "require_sub": True,
                },
            )

            allowed = self.settings.cognito_client_id_allowlist
            token_use = claims.get("token_use")
            if token_use == "id":
                aud = claims.get("aud")
                audiences = aud if isinstance(aud, list) else [aud]
                if not any(isinstance(a, str) and a in allowed for a in audiences):
                    raise ValueError("Token audience is not an allowed client")
            elif token_use == "access":
                if claims.get("client_id") not in allowed:
                    raise ValueError("Token client_id is not an allowed client")
            else:
                raise ValueError(f"Invalid token_use: {token_use}")

            logger.debug(f"Successfully validated token for user: {claims.get('sub')}")
            return claims
            
        except ExpiredSignatureError:
            logger.warning("Token has expired")
            raise ValueError("Token has expired")
        except JWTError as e:
            logger.warning(f"JWT validation failed: {e}")
            raise ValueError(f"Token validation failed: {e}")

    async def get_user_info(self, token: str) -> Dict[str, str]:
        """
        Extract user info ('sub' and 'email') from a validated token.

        Raises:
            ValueError: If token validation fails or required claims are missing.
        """
        return user_info_from_claims(await self.validate_token(token))

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()


def user_info_from_claims(claims: Dict[str, Any]) -> Dict[str, str]:
    """The 'sub' and 'email' of verified claims (email falls back to the username)."""
    sub = claims.get("sub")
    if not sub:
        raise ValueError("Token missing 'sub' claim")
    email = claims.get("email") or claims.get("cognito:username", "")
    return {"sub": sub, "email": email}


# Global service instance
cognito_service = CognitoService()
