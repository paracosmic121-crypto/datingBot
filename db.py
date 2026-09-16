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
    await db.users.create_index("gender")
    await db.users.create_index("is_ai")
    await db.likes.create_index([("from_id", 1), ("to_id", 1)], unique=True)
    await db.matches.create_index([("user_a", 1), ("user_b", 1)], unique=True)
    await db.messages.create_index([("from_id", 1), ("to_id", 1), ("created_at", 1)])


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
                "gender": None,
                "looking_for": None,
                "is_premium": False,
                "is_ai": False,
                "likes_given_today": 0,
                "likes_reset_at": dt.datetime.utcnow(),
                "views_this_hour": 0,
                "views_window_start": dt.datetime.utcnow(),
            },
        },
        upsert=True,
    )


async def update_profile_field(user_id: int, field: str, value: Any) -> None:
    allowed = {"gender", "looking_for", "name", "age", "location", "description", "photo_file_id"}
    if field not in allowed:
        raise ValueError(f"Cannot update field '{field}'")
    
    update_data = {field: value, "last_seen": dt.datetime.utcnow()}
    if field == "gender":
        # Automatically set looking_for to the opposite gender if not already set
        update_data["looking_for"] = "female" if value == "male" else "male"

    await get_db().users.update_one(
        {"user_id": user_id},
        {
            "$set": update_data,
            "$setOnInsert": {
                "user_id": user_id,
                "created_at": dt.datetime.utcnow(),
                "profile_complete": False,
                "is_premium": False,
                "is_ai": False,
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
                "is_ai": False,
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
                "is_ai": False,
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
# AI Personas / Fake Profiles
# --------------------------------------------------------------------------- #

async def upsert_ai_profile(profile_dict: dict) -> None:
    """Inserts or updates an AI fake profile in MongoDB."""
    user_id = profile_dict["user_id"]
    profile_dict["profile_complete"] = True
    profile_dict["is_ai"] = True
    profile_dict["last_seen"] = dt.datetime.utcnow()
    await get_db().users.update_one(
        {"user_id": user_id},
        {"$set": profile_dict},
        upsert=True,
    )


async def is_ai_user(user_id: int) -> bool:
    """Returns True if user is an AI persona."""
    user = await get_user(user_id)
    return bool(user and user.get("is_ai"))


# --------------------------------------------------------------------------- #
# Discovery / swiping
# --------------------------------------------------------------------------- #

async def get_next_profile(user_id: int) -> Optional[dict]:
    """
    Returns next candidate profile matching user's preferred gender,
    with HIGH PRIORITY for premium users.
    """
    db = get_db()
    current_user = await get_user(user_id)
    if not current_user:
        return None

    # Determine desired gender (male -> female, female -> male)
    user_gender = current_user.get("gender", "male")
    target_gender = current_user.get("looking_for") or ("female" if user_gender == "male" else "male")

    swiped_docs = await db.likes.find(
        {"from_id": user_id}, {"to_id": 1, "_id": 0}
    ).to_list(2000)
    already_swiped = [d["to_id"] for d in swiped_docs]
    now = dt.datetime.utcnow()

    # 1. High priority: Premium profiles matching target gender
    query_prem = {
        "user_id": {"$ne": user_id, "$nin": already_swiped},
        "profile_complete": True,
        "gender": target_gender,
        "is_premium": True,
        "$or": [
            {"premium_until": None},
            {"premium_until": {"$gte": now}},
        ],
    }
    prem_candidates = await db.users.find(query_prem).limit(10).to_list(10)
    if prem_candidates:
        return random.choice(prem_candidates)

    # 2. Standard profiles matching target gender
    query_std = {
        "user_id": {"$ne": user_id, "$nin": already_swiped},
        "profile_complete": True,
        "gender": target_gender,
    }
    std_candidates = await db.users.find(query_std).limit(10).to_list(10)
    if std_candidates:
        return random.choice(std_candidates)

    # Fallback if no target gender matches found: any complete profile
    query_fallback = {
        "user_id": {"$ne": user_id, "$nin": already_swiped},
        "profile_complete": True,
    }
    fallback_candidates = await db.users.find(query_fallback).limit(5).to_list(5)
    if fallback_candidates:
        return random.choice(fallback_candidates)

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
        await get_db().users.update_one(
            {"user_id": user_id},
            {"$set": {"views_this_hour": 0, "views_window_start": now}},
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
    """Increment profile view counter for non-premium users."""
    user = await get_user(user_id)
    if is_user_premium(user):
        return

    now = dt.datetime.utcnow()
    await get_db().users.update_one(
        {"user_id": user_id},
        {
            "$inc": {"views_this_hour": 1},
            "$setOnInsert": {"views_window_start": now},
        },
        upsert=True,
    )


async def record_swipe(from_id: int, to_id: int, liked: bool) -> bool:
    """
    Stores the swipe. Returns True if this swipe created a mutual match.
    If target is an AI profile and liked is True, auto-creates mutual like.
    """
    db = get_db()
    await db.likes.update_one(
        {"from_id": from_id, "to_id": to_id},
        {"$set": {"liked": liked, "at": dt.datetime.utcnow()}},
        upsert=True,
    )
    if not liked:
        return False

    # Check if target is an AI persona -> AI personas automatically like back!
    target_user = await get_user(to_id)
    if target_user and target_user.get("is_ai"):
        # Auto-record reciprocal like from AI
        await db.likes.update_one(
            {"from_id": to_id, "to_id": from_id},
            {"$set": {"liked": True, "at": dt.datetime.utcnow()}},
            upsert=True,
        )
        user_a, user_b = sorted([from_id, to_id])
        await db.matches.update_one(
            {"user_a": user_a, "user_b": user_b},
            {"$setOnInsert": {"user_a": user_a, "user_b": user_b, "at": dt.datetime.utcnow()}},
            upsert=True,
        )
        return True

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
        await get_db().users.update_one(
            {"user_id": user_id},
            {"$set": {"likes_given_today": 0, "likes_reset_at": now}},
        )
        return True

    return user.get("likes_given_today", 0) < settings.FREE_LIKES_PER_DAY


async def increment_like_counter(user_id: int) -> None:
    await get_db().users.update_one(
        {"user_id": user_id}, {"$inc": {"likes_given_today": 1}}
    )


# --------------------------------------------------------------------------- #
# In-Bot Chat & Messages
# --------------------------------------------------------------------------- #

async def save_chat_message(from_id: int, to_id: int, text: str) -> dict:
    """Stores a 1-on-1 chat message."""
    db = get_db()
    msg = {
        "from_id": from_id,
        "to_id": to_id,
        "text": text,
        "created_at": dt.datetime.utcnow(),
    }
    await db.messages.insert_one(msg)
    return msg


async def get_chat_history(user_a: int, user_b: int, limit: int = 15) -> list[dict]:
    """Retrieves recent conversation history between user_a and user_b."""
    db = get_db()
    cursor = db.messages.find({
        "$or": [
            {"from_id": user_a, "to_id": user_b},
            {"from_id": user_b, "to_id": user_a},
        ]
    }).sort("created_at", -1).limit(limit)
    history = await cursor.to_list(limit)
    history.reverse()  # chronological order
    return history


async def get_user_matches(user_id: int) -> list[dict]:
    """Returns list of user profile dicts that user_id has mutual matches with."""
    db = get_db()
    matches_docs = await db.matches.find({
        "$or": [{"user_a": user_id}, {"user_b": user_id}]
    }).sort("at", -1).to_list(50)

    matched_partner_ids = []
    for m in matches_docs:
        partner_id = m["user_b"] if m["user_a"] == user_id else m["user_a"]
        matched_partner_ids.append(partner_id)

    if not matched_partner_ids:
        return []

    partners = await db.users.find({"user_id": {"$in": matched_partner_ids}}).to_list(len(matched_partner_ids))
    return partners


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
        "total_messages": await db.messages.count_documents({}),
    }
