"""DELETE /me: account deletion (Apple 5.1.1(v)).

Contract: predictium-app docs/API_PROPOSAL.md section 5. The app's
src/account/delete-account.ts reads any 2xx as deleted, 401 as signed out,
and anything else (here 429 or 503) as failed, which it lets the person
retry. Steps and their order are in app/services/account_deletion.py.
"""

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.dependencies import get_token_claims
from app.services import account_deletion
from app.services.rate_limit import delete_me_limiter, per_sub_limit

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Account"])


@router.delete("/me", status_code=status.HTTP_202_ACCEPTED)
async def delete_me(
    _: None = Depends(per_sub_limit(delete_me_limiter)),
    claims: Dict[str, Any] = Depends(get_token_claims),
    db: AsyncSession = Depends(get_db),
):
    """Deletes the caller's account. Idempotent: a repeat call after a full
    or partial run finishes what is left and returns 202 again.

    The token is verified on this request (signature, issuer, expiry and
    the app client allowlist) before anything runs. This route never creates
    a user row, so a retry after the data step cannot bring the user back.
    """
    try:
        await account_deletion.delete_account(db, claims)
    except account_deletion.DeletionStepFailed as e:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": "Account deletion did not finish. It is safe to try again.",
                "step": e.step,
            },
            headers={"Retry-After": "30"},
        )
    return {"status": "deleted"}
