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


def _now() -> dt.datetime:
    """Returns current UTC time as a timezone-aware datetime (Python 3.12+ safe)."""
    return dt.datetime.now(dt.timezone.utc)


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
    await db.likes.create_index([("from_id", 1), ("at", -1)])  # for undo
    await db.matches.create_index([("user_a", 1), ("user_b", 1)], unique=True)
    await db.messages.create_index([("from_id", 1), ("to_id", 1), ("created_at", 1)])


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #

async def get_user(user_id: int) -> Optional[dict]:
    return await get_db().users.find_one({"user_id": user_id})


async def upsert_user_basic(user_id: int, username: str | None, first_name: str | None) -> None:
    """Called on every /start so we always have a stub record + fresh username."""
    now = _now()
    await get_db().users.update_one(
        {"user_id": user_id},
        {
            "$set": {
                "username": username,
                "first_name": first_name,
                "last_seen": now,
            },
            "$setOnInsert": {
                "user_id": user_id,
                "created_at": now,
                "profile_complete": False,
                "gender": None,
                "looking_for": None,
                "is_premium": False,
                "is_ai": False,
                "likes_given_today": 0,
                "likes_reset_at": now,
                "views_this_hour": 0,
                "views_window_start": now,
            },
        },
        upsert=True,
    )


async def update_profile_field(user_id: int, field: str, value: Any) -> None:
    allowed = {"gender", "looking_for", "name", "age", "location", "description", "photo_file_id"}
    if field not in allowed:
        raise ValueError(f"Cannot update field '{field}'")

    now = _now()
    update_data = {field: value, "last_seen": now}

    await get_db().users.update_one(
        {"user_id": user_id},
        {
            "$set": update_data,
            "$setOnInsert": {
                "user_id": user_id,
                "created_at": now,
                "profile_complete": False,
                "is_premium": False,
                "is_ai": False,
                "likes_given_today": 0,
                "likes_reset_at": now,
                "views_this_hour": 0,
                "views_window_start": now,
            },
        },
        upsert=True,
    )

    # If gender was updated, set a sensible default for looking_for ONLY if it
    # hasn't been explicitly chosen yet — avoids overwriting a custom preference.
    if field == "gender":
        default_lf = "female" if value == "male" else "male"
        await get_db().users.update_one(
            {
                "user_id": user_id,
                "$or": [{"looking_for": None}, {"looking_for": {"$exists": False}}],
            },
            {"$set": {"looking_for": default_lf}},
        )


async def mark_profile_complete(user_id: int) -> None:
    now = _now()
    await get_db().users.update_one(
        {"user_id": user_id},
        {
            "$set": {"profile_complete": True, "last_seen": now},
            "$setOnInsert": {
                "user_id": user_id,
                "created_at": now,
                "is_premium": False,
                "is_ai": False,
                "likes_given_today": 0,
                "likes_reset_at": now,
                "views_this_hour": 0,
                "views_window_start": now,
            },
        },
        upsert=True,
    )


async def set_premium(user_id: int, is_premium: bool, until: dt.datetime | None = None) -> None:
    now = _now()
    await get_db().users.update_one(
        {"user_id": user_id},
        {
            "$set": {"is_premium": is_premium, "premium_until": until, "last_seen": now},
            "$setOnInsert": {
                "user_id": user_id,
                "created_at": now,
                "profile_complete": False,
                "is_ai": False,
                "likes_given_today": 0,
                "likes_reset_at": now,
                "views_this_hour": 0,
                "views_window_start": now,
            },
        },
        upsert=True,
    )


def is_user_premium(user: dict | None) -> bool:
    """Returns True if the user has an active premium subscription."""
    if not user or not user.get("is_premium"):
        return False
    until = user.get("premium_until")
    if until and until < _now():
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
    profile_dict["last_seen"] = _now()
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
    ).to_list(10_000)
    already_swiped = [d["to_id"] for d in swiped_docs]
    now = _now()

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

    now = _now()
    window_start = user.get("views_window_start", now)
    # Make window_start timezone-aware if stored as naive
    if window_start.tzinfo is None:
        window_start = window_start.replace(tzinfo=dt.timezone.utc)
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

    await get_db().users.update_one(
        {"user_id": user_id},
        {"$inc": {"views_this_hour": 1}},
    )


async def record_swipe(from_id: int, to_id: int, liked: bool, message: str | None = None) -> bool:
    """
    Stores the swipe. If message is provided, saves it in the like record.
    Returns True if this swipe created a mutual match.
    """
    db = get_db()
    now = _now()
    data: dict = {"liked": liked, "at": now}
    if message:
        data["message"] = message
    await db.likes.update_one(
        {"from_id": from_id, "to_id": to_id},
        {"$set": data},
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
        {"$setOnInsert": {"user_a": user_a, "user_b": user_b, "at": now}},
        upsert=True,
    )
    return True


async def get_pending_likes_count(user_id: int) -> int:
    """Returns count of pending unswiped likes targeting this user, using aggregation."""
    db = get_db()
    pipeline = [
        # All like records where this user was the target
        {"$match": {"to_id": user_id, "liked": True}},
        # Join with records where this user already swiped the sender
        {
            "$lookup": {
                "from": "likes",
                "let": {"sender": "$from_id"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$from_id", user_id]},
                                    {"$eq": ["$to_id", "$$sender"]},
                                ]
                            }
                        }
                    }
                ],
                "as": "already_swiped_by_me",
            }
        },
        # Keep only those where user hasn't swiped back yet
        {"$match": {"already_swiped_by_me": {"$size": 0}}},
        {"$count": "total"},
    ]
    result = await db.likes.aggregate(pipeline).to_list(1)
    return result[0]["total"] if result else 0


async def create_mutual_match(user_a_id: int, user_b_id: int) -> None:
    """Explicitly records reciprocal likes and creates a match between two users (e.g. for delayed AI matches)."""
    db = get_db()
    now = _now()
    await db.likes.update_one(
        {"from_id": user_a_id, "to_id": user_b_id},
        {"$set": {"liked": True, "at": now}},
        upsert=True,
    )
    await db.likes.update_one(
        {"from_id": user_b_id, "to_id": user_a_id},
        {"$set": {"liked": True, "at": now}},
        upsert=True,
    )
    ua, ub = sorted([user_a_id, user_b_id])
    await db.matches.update_one(
        {"user_a": ua, "user_b": ub},
        {"$setOnInsert": {"user_a": ua, "user_b": ub, "at": now}},
        upsert=True,
    )


async def schedule_next_ai_match_delay(user_id: int) -> int:
    """
    Calculates the delay in seconds for the next fake AI match.
    - 1st match: initial natural delay (30-60s).
    - Subsequent matches: spaced out by 4 to 5 hours (14,400 - 18,000s) from the last match/scheduled slot.
    Updates `next_ai_match_at` on the user record.
    """
    user = await get_user(user_id)
    now = _now()
    last_scheduled = user.get("next_ai_match_at") if user else None

    # Make timezone-aware if stored as naive
    if last_scheduled and last_scheduled.tzinfo is None:
        last_scheduled = last_scheduled.replace(tzinfo=dt.timezone.utc)

    if last_scheduled and last_scheduled > now:
        # Another match is already pending in the future -> space this one 4-5 hours after it
        interval = dt.timedelta(hours=random.uniform(4.0, 5.0))
        next_time = last_scheduled + interval
    elif last_scheduled and (now - last_scheduled) < dt.timedelta(hours=4):
        # A match was delivered recently (less than 4 hours ago) -> delay by 4-5 hours from then
        interval = dt.timedelta(hours=random.uniform(4.0, 5.0))
        next_time = last_scheduled + interval
        if next_time <= now:
            next_time = now + dt.timedelta(seconds=random.randint(30, 60))
    else:
        # First match or more than 4 hours passed -> natural short delay (30-60s)
        next_time = now + dt.timedelta(seconds=random.randint(30, 60))

    await get_db().users.update_one(
        {"user_id": user_id},
        {"$set": {"next_ai_match_at": next_time}},
    )

    delay_seconds = int((next_time - now).total_seconds())
    return max(delay_seconds, 20)


async def can_like_today(user_id: int) -> bool:
    """Free users are rate-limited; premium users are unlimited."""
    user = await get_user(user_id)
    if not user:
        return True
    if is_user_premium(user):
        return True

    reset_at = user.get("likes_reset_at", _now())
    now = _now()
    # Make timezone-aware if stored as naive
    if reset_at.tzinfo is None:
        reset_at = reset_at.replace(tzinfo=dt.timezone.utc)
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
# Undo Swipe
# --------------------------------------------------------------------------- #

async def get_last_swipe(user_id: int) -> Optional[dict]:
    """Returns the most recent swipe record made by user_id, or None."""
    return await get_db().likes.find_one(
        {"from_id": user_id},
        sort=[("at", -1)],
    )


async def undo_last_swipe(user_id: int) -> Optional[dict]:
    """
    Deletes the most recent swipe for a user (premium only, enforced in handler).
    Returns the deleted swipe document so the caller can re-show that profile.
    """
    last = await get_last_swipe(user_id)
    if not last:
        return None
    await get_db().likes.delete_one({"_id": last["_id"]})
    return last


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
        "created_at": _now(),
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
    """Returns list of user profile dicts that user_id has mutual matches with, most-recent first."""
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

    # Fetch partner profiles, then restore the match-sorted order
    raw_partners = await db.users.find(
        {"user_id": {"$in": matched_partner_ids}}
    ).to_list(len(matched_partner_ids))
    partners_by_id = {p["user_id"]: p for p in raw_partners}
    return [partners_by_id[pid] for pid in matched_partner_ids if pid in partners_by_id]


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
