from fastapi import APIRouter, Header, HTTPException

import db
from config import settings

router = APIRouter()


def _check_admin_key(x_api_key: str | None) -> None:
    if x_api_key != settings.ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/stats")
async def stats(x_api_key: str | None = Header(default=None)):
    _check_admin_key(x_api_key)
    return await db.get_stats()


@router.get("/users/{user_id}")
async def get_user(user_id: int, x_api_key: str | None = Header(default=None)):
    _check_admin_key(x_api_key)
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user["_id"] = str(user["_id"])
    return user


@router.post("/users/{user_id}/premium")
async def grant_premium(user_id: int, x_api_key: str | None = Header(default=None)):
    _check_admin_key(x_api_key)
    await db.set_premium(user_id, True, None)
    return {"ok": True}
