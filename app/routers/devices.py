"""Push device registration for the mobile app.

Contract: predictium-app docs/API_PROPOSAL.md section 1.

The Expo push token arrives only in the POST /devices body. It is never put
in a URL, a log line or an error message. DELETE uses the row id that POST
returns.
"""

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.dependencies import get_current_user_with_db
from app.models.push_device import PushDevice
from app.models.user import User
from app.services.rate_limit import devices_limiter, per_sub_limit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/devices", tags=["Devices"])

TOKEN_RE = re.compile(r"^ExponentPushToken\[.+\]$")
PLATFORMS = {"ios", "android"}
STALE_AFTER = timedelta(days=30)

_limit = per_sub_limit(devices_limiter)


class DeviceRegistration(BaseModel):
    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)

    expo_push_token: str
    platform: str
    app_version: Optional[str] = None

    def __repr__(self) -> str:  # never include the token
        return f"DeviceRegistration(platform={self.platform!r})"

    __str__ = __repr__


def _unprocessable(message: str) -> HTTPException:
    return HTTPException(status_code=422, detail=message)


def _insert_for(db: AsyncSession):
    if db.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:  # tests run on SQLite, whose insert has the same upsert API
        from sqlalchemy.dialects.sqlite import insert
    return insert


@router.post("", status_code=status.HTTP_201_CREATED)
async def register_device(
    body: DeviceRegistration,
    _: None = Depends(_limit),
    user: User = Depends(get_current_user_with_db),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, str]:
    """Registers this phone for alerts, or refreshes its row.

    Upserts on the token: a token already held by another account (a shared
    phone) moves to the caller. Every call bumps last_seen_at.
    """
    token = body.expo_push_token.strip()
    if len(token) > 512 or not TOKEN_RE.match(token):
        raise _unprocessable("expo_push_token is not an Expo push token")
    if body.platform not in PLATFORMS:
        raise _unprocessable("platform must be ios or android")
    app_version = body.app_version
    if app_version is not None and len(app_version) > 64:
        raise _unprocessable("app_version is too long")

    insert = _insert_for(db)
    now = func.now()
    stmt = insert(PushDevice).values(
        id=uuid.uuid4(),
        user_id=user.id,
        expo_push_token=token,
        platform=body.platform,
        app_version=app_version,
        created_at=now,
        last_seen_at=now,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[PushDevice.expo_push_token],
        set_={
            "user_id": user.id,
            "platform": body.platform,
            "app_version": app_version,
            "last_seen_at": now,
        },
    ).returning(PushDevice.id)
    device_id = (await db.execute(stmt)).scalar_one()
    await db.commit()
    logger.info("Registered push device %s (%s)", device_id, body.platform)
    return {"device_id": str(device_id)}


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unregister_device(
    device_id: uuid.UUID,
    _: None = Depends(_limit),
    user: User = Depends(get_current_user_with_db),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Deletes the caller's own row. 204 whether or not it existed, and
    whether or not the id belongs to someone else (that row is untouched)."""
    await db.execute(
        delete(PushDevice).where(PushDevice.id == device_id, PushDevice.user_id == user.id)
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def prune_stale_devices(db: AsyncSession, now: Optional[datetime] = None) -> int:
    """Deletes rows not seen for 30 days (an offline sign-out never called DELETE)."""
    cutoff = (now or datetime.now(timezone.utc)) - STALE_AFTER
    result = await db.execute(delete(PushDevice).where(PushDevice.last_seen_at < cutoff))
    await db.commit()
    return result.rowcount or 0
