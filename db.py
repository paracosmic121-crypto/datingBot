"""
Async MongoDB access layer (Motor).

Collections
-----------
users        : one document per Telegram user (profile + settings)
likes        : every swipe (like/dislike) ever made, for match detection + analytics
matches      : created when two users like each other
stats        : lightweight daily counters (messages, swipes, matches) for the admin API
"""
from __future__ import annotations

import asyncio
import datetime as dt
import random
from typing import Any, Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from config import settings

_client: Optional[AsyncIOMotorClient] = None
_db: Optional[AsyncIOMotorDatabase] = None


def get_db() -> AsyncIOMotorDatabase:
    global _client, _db
    if _db is None:
        _client = AsyncIOMotorClient(settings.MONGO_URI)
        _db = _client[settings.MONGO_DB_NAME]
    return _db


async def ensure_indexes() -> None:
    db = get_db()
    await db.users.create_index("user_id", unique=True)
    await db.users.create_index("is_premium")
    await db.likes.create_index([("from_id", 1), ("to_id", 1)], unique=True)
    await db.matches.create_index([("user_a", 1), ("user_b", 1)], unique=True)


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #

async def get_user(user_id: int) -> Optional[dict]:
    return await get_db().users.find_one({"user_id": user_id})


async def upsert_user_basic(user_id: int, username: str | None, first_name: str | None) -> None:
    """Called on every /start so we always have a stub record + fresh username."""
    await get_db().users.update_one(
        {"user_id": user_id},
        {
            "$set": {
                "username": username,
                "first_name": first_name,
                "last_seen": dt.datetime.utcnow(),
            },
            "$setOnInsert": {
                "user_id": user_id,
                "created_at": dt.datetime.utcnow(),
                "profile_complete": False,
                "is_premium": False,
                "likes_given_today": 0,
                "likes_reset_at": dt.datetime.utcnow(),
                "views_this_hour": 0,
                "views_window_start": dt.datetime.utcnow(),
            },
        },
        upsert=True,
    )


async def update_profile_field(user_id: int, field: str, value: Any) -> None:
    allowed = {"name", "age", "location", "description", "photo_file_id"}
    if field not in allowed:
        raise ValueError(f"Cannot update field '{field}'")
    await get_db().users.update_one(
        {"user_id": user_id},
        {
            "$set": {field: value, "last_seen": dt.datetime.utcnow()},
            "$setOnInsert": {
                "user_id": user_id,
                "created_at": dt.datetime.utcnow(),
                "profile_complete": False,
                "is_premium": False,
                "likes_given_today": 0,
                "likes_reset_at": dt.datetime.utcnow(),
                "views_this_hour": 0,
                "views_window_start": dt.datetime.utcnow(),
            },
        },
        upsert=True,
    )


async def mark_profile_complete(user_id: int) -> None:
    await get_db().users.update_one(
        {"user_id": user_id},
        {
            "$set": {"profile_complete": True, "last_seen": dt.datetime.utcnow()},
            "$setOnInsert": {
                "user_id": user_id,
                "created_at": dt.datetime.utcnow(),
                "is_premium": False,
                "likes_given_today": 0,
                "likes_reset_at": dt.datetime.utcnow(),
                "views_this_hour": 0,
                "views_window_start": dt.datetime.utcnow(),
            },
        },
        upsert=True,
    )


async def set_premium(user_id: int, is_premium: bool, until: dt.datetime | None = None) -> None:
    await get_db().users.update_one(
        {"user_id": user_id},
        {
            "$set": {"is_premium": is_premium, "premium_until": until, "last_seen": dt.datetime.utcnow()},
            "$setOnInsert": {
                "user_id": user_id,
                "created_at": dt.datetime.utcnow(),
                "profile_complete": False,
                "likes_given_today": 0,
                "likes_reset_at": dt.datetime.utcnow(),
                "views_this_hour": 0,
                "views_window_start": dt.datetime.utcnow(),
            },
        },
        upsert=True,
    )


def is_user_premium(user: dict | None) -> bool:
    """Returns True if the user has an active premium subscription."""
    if not user or not user.get("is_premium"):
        return False
    until = user.get("premium_until")
    if until and until < dt.datetime.utcnow():
        return False
    return True


# --------------------------------------------------------------------------- #
# Discovery / swiping
# --------------------------------------------------------------------------- #

async def get_next_profile(user_id: int) -> Optional[dict]:
    """
    Returns next candidate profile with HIGH PRIORITY for premium users.
    """
    db = get_db()
    swiped_docs = await db.likes.find(
        {"from_id": user_id}, {"to_id": 1, "_id": 0}
    ).to_list(2000)
    already_swiped = [d["to_id"] for d in swiped_docs]
    now = dt.datetime.utcnow()

    # 1. High priority: Premium profiles
    query_prem = {
        "user_id": {"$ne": user_id, "$nin": already_swiped},
        "profile_complete": True,
        "is_premium": True,
        "$or": [
            {"premium_until": None},
            {"premium_until": {"$gte": now}},
        ],
    }
    prem_candidates = await db.users.find(query_prem).limit(10).to_list(10)
    if prem_candidates:
        return random.choice(prem_candidates)

    # 2. Standard profiles
    query_std = {
        "user_id": {"$ne": user_id, "$nin": already_swiped},
        "profile_complete": True,
    }
    std_candidates = await db.users.find(query_std).limit(10).to_list(10)
    if std_candidates:
        return random.choice(std_candidates)

    return None


async def can_view_profile_now(user_id: int) -> tuple[bool, int, int]:
    """
    Checks if a user can view another profile.
    - Premium: unlimited (returns True, 999999, 0)
    - Free: max FREE_VIEWS_PER_HOUR (10) in a rolling 1-hour window.
    Returns: (can_view, views_left, minutes_until_reset)
    """
    user = await get_user(user_id)
    if not user:
        return True, settings.FREE_VIEWS_PER_HOUR, 0
    if is_user_premium(user):
        return True, 999999, 0

    now = dt.datetime.utcnow()
    window_start = user.get("views_window_start", now)
    views_this_hour = user.get("views_this_hour", 0)

    # If more than 1 hour passed since window start, reset window
    if (now - window_start) > dt.timedelta(hours=1):
        asyncio.create_task(
            get_db().users.update_one(
                {"user_id": user_id},
                {"$set": {"views_this_hour": 0, "views_window_start": now}},
            )
        )
        return True, settings.FREE_VIEWS_PER_HOUR, 0

    if views_this_hour < settings.FREE_VIEWS_PER_HOUR:
        views_left = settings.FREE_VIEWS_PER_HOUR - views_this_hour
        return True, views_left, 0

    elapsed_seconds = (now - window_start).total_seconds()
    remaining_seconds = max(0, 3600 - elapsed_seconds)
    minutes_until_reset = max(1, int(remaining_seconds // 60))
    return False, 0, minutes_until_reset


async def increment_profile_view(user_id: int) -> None:
    """Non-blocking increment for profile views."""
    user = await get_user(user_id)
    if is_user_premium(user):
        return

    now = dt.datetime.utcnow()
    asyncio.create_task(
        get_db().users.update_one(
            {"user_id": user_id},
            {
                "$inc": {"views_this_hour": 1},
                "$setOnInsert": {"views_window_start": now},
            },
            upsert=True,
        )
    )


async def record_swipe(from_id: int, to_id: int, liked: bool) -> bool:
    """
    Stores the swipe. Returns True if this swipe created a mutual match.
    """
    db = get_db()
    await db.likes.update_one(
        {"from_id": from_id, "to_id": to_id},
        {"$set": {"liked": liked, "at": dt.datetime.utcnow()}},
        upsert=True,
    )
    if not liked:
        return False

    mutual = await db.likes.find_one({"from_id": to_id, "to_id": from_id, "liked": True})
    if not mutual:
        return False

    user_a, user_b = sorted([from_id, to_id])
    await db.matches.update_one(
        {"user_a": user_a, "user_b": user_b},
        {"$setOnInsert": {"user_a": user_a, "user_b": user_b, "at": dt.datetime.utcnow()}},
        upsert=True,
    )
    return True


async def can_like_today(user_id: int) -> bool:
    """Free users are rate-limited; premium users are unlimited."""
    user = await get_user(user_id)
    if not user:
        return True
    if is_user_premium(user):
        return True

    reset_at = user.get("likes_reset_at", dt.datetime.utcnow())
    now = dt.datetime.utcnow()
    if (now - reset_at) > dt.timedelta(hours=24):
        asyncio.create_task(
            get_db().users.update_one(
                {"user_id": user_id},
                {"$set": {"likes_given_today": 0, "likes_reset_at": now}},
            )
        )
        return True

    return user.get("likes_given_today", 0) < settings.FREE_LIKES_PER_DAY


async def increment_like_counter(user_id: int) -> None:
    asyncio.create_task(
        get_db().users.update_one(
            {"user_id": user_id}, {"$inc": {"likes_given_today": 1}}
        )
    )


# --------------------------------------------------------------------------- #
# Stats (used by the FastAPI admin endpoints)
# --------------------------------------------------------------------------- #

async def get_stats() -> dict:
    db = get_db()
    return {
        "total_users": await db.users.count_documents({}),
        "complete_profiles": await db.users.count_documents({"profile_complete": True}),
        "premium_users": await db.users.count_documents({"is_premium": True}),
        "total_matches": await db.matches.count_documents({}),
        "total_likes": await db.likes.count_documents({"liked": True}),
    }
