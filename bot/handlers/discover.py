import asyncio
import html
import logging
import os
import random

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import db
from bot.keyboards import (
    cancel_kb,
    like_received_kb,
    main_menu_kb,
    real_match_kb,
    start_chat_kb,
    swipe_reply_kb,
)
from bot.handlers.start import MAIN_MENU_TEXT

logger = logging.getLogger(__name__)

_LOVE_LETTER_MAX_LEN = 500  # Maximum characters for a direct message / love letter


def _format_match_text(matched_user: dict) -> str:
    name = html.escape(str(matched_user.get("name", "Someone")))
    age = matched_user.get("age", "—")
    location = html.escape(str(matched_user.get("location", "—")))
    is_ai = bool(matched_user.get("is_ai"))

    if is_ai:
        return (
            f"🎉 <b>IT'S A MATCH!</b>\n\n"
            f"You and <b>{name}</b> ({age}, {location}) liked each other! ✨\n\n"
            f"<i>Tap below to start chatting directly in the bot: 👇</i>"
        )
    else:
        username = matched_user.get("username")
        uid = matched_user.get("user_id")
        if username:
            contact_str = f"@{html.escape(username)}"
        else:
            contact_str = f'<a href="tg://user?id={uid}">{name}</a>'

        return (
            f"🎉 <b>IT'S A MATCH!</b>\n\n"
            f"You and <b>{name}</b> ({age}, {location}) liked each other! ✨\n\n"
            f"💬 <b>Telegram:</b> {contact_str}\n\n"
            f"<i>Tap below to open their Telegram chat directly and start talking: 👇</i>"
        )


def _get_match_kb(matched_user: dict):
    """Returns in-bot chat keyboard for AI fake profiles, and Telegram DM link keyboard for real users."""
    if matched_user.get("is_ai"):
        return start_chat_kb(matched_user.get("user_id"))
    return real_match_kb(matched_user)


async def _delayed_ai_match(user_id: int, ai_id: int, bot, delay_seconds: int = 25) -> None:
    """Delays the AI reciprocal match and match announcement so it feels natural and realistic."""
    try:
        await asyncio.sleep(delay_seconds)
        user = await db.get_user(user_id)
        ai_user = await db.get_user(ai_id)
        if not user or not ai_user:
            return

        # Ensure match is saved in DB
        await db.create_mutual_match(user_id, ai_id)

        await bot.send_message(
            chat_id=user_id,
            text=_format_match_text(ai_user),
            parse_mode=ParseMode.HTML,
            reply_markup=start_chat_kb(ai_id),
        )
    except Exception as exc:
        logger.warning("Error in delayed AI match for user %s and AI %s: %s", user_id, ai_id, exc)


def _schedule_ai_match_task(user_id: int, ai_id: int, bot, delay: int, bot_data: dict) -> None:
    """
    Creates a tracked asyncio task for the delayed AI match.
    Stores the task in bot_data['_ai_tasks'] so it won't be GC'd and can be
    cleanly cancelled on graceful shutdown.
    """
    task = asyncio.create_task(_delayed_ai_match(user_id, ai_id, bot, delay))
    ai_tasks: set = bot_data.setdefault("_ai_tasks", set())
    ai_tasks.add(task)
    task.add_done_callback(ai_tasks.discard)


async def _show_profile(target_msg, profile: dict) -> None:
    """Renders a single profile (photo + caption + swipe keyboard)."""
    name = profile.get("name", "—")
    age = profile.get("age", "—")
    location = profile.get("location", "—")
    description = profile.get("description", "")
    is_prem = db.is_user_premium(profile)
    badge = " ⭐" if is_prem else ""
    caption = f"{name}{badge}, {age} — {location}\n\n{description}"
    photo = profile.get("photo_file_id")
    kb = swipe_reply_kb()

    if photo:
        try:
            if isinstance(photo, str) and os.path.exists(photo):
                with open(photo, "rb") as f:
                    await target_msg.reply_photo(photo=f, caption=caption, reply_markup=kb)
            else:
                await target_msg.reply_photo(photo=photo, caption=caption, reply_markup=kb)
        except Exception as exc:
            logger.warning("Could not reply with photo: %s", exc)
            await target_msg.reply_text(caption, reply_markup=kb)
    else:
        await target_msg.reply_text(caption, reply_markup=kb)


async def _show_next_profile(update_or_query, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    target_msg = update_or_query.message if hasattr(update_or_query, "message") else update_or_query

    # 1. Check hourly view limits (unlimited for premium, 10/hr for free)
    can_view, views_left, minutes_until_reset = await db.can_view_profile_now(user_id)
    if not can_view:
        context.user_data.pop("current_candidate_id", None)
        await target_msg.reply_text(
            f"⏳ *Hourly Limit Reached!*\n\n"
            f"Free accounts can view up to 10 profiles every hour.\n"
            f"Please wait *{minutes_until_reset} minute(s)* or upgrade to ⭐ *Premium* for unlimited profile browsing!\n\n"
            + MAIN_MENU_TEXT,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu_kb(),
        )
        return

    profile = await db.get_next_profile(user_id)

    if not profile:
        context.user_data.pop("current_candidate_id", None)
        await target_msg.reply_text(
            "No new profiles right now — check back later!\n\n" + MAIN_MENU_TEXT,
            reply_markup=main_menu_kb(),
        )
        return

    # 2. Count profile view
    await db.increment_profile_view(user_id)

    context.user_data["current_candidate_id"] = profile["user_id"]
    await _show_profile(target_msg, profile)


async def discover(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await db.get_user(update.effective_user.id)
    target = update.callback_query if update.callback_query else update
    if update.callback_query:
        await update.callback_query.answer()

    if not user or not user.get("profile_complete"):
        target_msg = target.message if hasattr(target, "message") else target
        await target_msg.reply_text(
            "Finish setting up your profile first with /editprofile.",
            reply_markup=main_menu_kb(),
        )
        return

    await _show_next_profile(target, update.effective_user.id, context)


async def _notify_like_received(from_id: int, to_id: int, context: ContextTypes.DEFAULT_TYPE, message: str | None = None) -> None:
    """Send profile of User A to User B WITH optional direct message."""
    from_user = await db.get_user(from_id)
    if not from_user:
        return

    name = html.escape(str(from_user.get("name", "Someone")))
    age = html.escape(str(from_user.get("age", "—")))
    location = html.escape(str(from_user.get("location", "—")))
    description = html.escape(str(from_user.get("description", "")))

    pending_count = await db.get_pending_likes_count(to_id)
    more_text = f" (and {pending_count - 1} more)" if pending_count > 1 else ""

    caption_lines = [
        f"Someone liked your profile{more_text}\n",
        f"<b>{name}</b>, {age}, {location} – {description}\n",
    ]
    if message:
        caption_lines.append(f"A message for you💌: {html.escape(message)}")

    caption = "\n".join(caption_lines)
    photo = from_user.get("photo_file_id")
    kb = like_received_kb(from_id)

    try:
        if photo:
            if isinstance(photo, str) and os.path.exists(photo):
                with open(photo, "rb") as f:
                    await context.bot.send_photo(
                        chat_id=to_id,
                        photo=f,
                        caption=caption,
                        parse_mode=ParseMode.HTML,
                        reply_markup=kb,
                    )
            else:
                await context.bot.send_photo(
                    chat_id=to_id,
                    photo=photo,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                )
        else:
            await context.bot.send_message(
                chat_id=to_id,
                text=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
    except Exception as exc:
        logger.warning("Could not send like notification to user %s: %s", to_id, exc)


async def _process_swipe(
    user_id: int,
    target_id: int,
    liked: bool,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    direct_message: str | None = None,
) -> None:
    user = await db.get_user(user_id)
    if not user or not user.get("profile_complete"):
        context.user_data.pop("current_candidate_id", None)
        await update.message.reply_text(
            "Your profile was not found or is incomplete. Use /editprofile to set it up!",
            reply_markup=main_menu_kb(),
        )
        return

    if liked and not await db.can_like_today(user_id):
        context.user_data.pop("current_candidate_id", None)
        await update.message.reply_text(
            "You've hit your daily like limit. Get /premium for unlimited likes!\n\n" + MAIN_MENU_TEXT,
            reply_markup=main_menu_kb(),
        )
        return

    is_match = await db.record_swipe(user_id, target_id, liked, message=direct_message)
    if liked:
        await db.increment_like_counter(user_id)

    if is_match:
        context.user_data.pop("current_candidate_id", None)
        from_user = await db.get_user(user_id)
        target_user = await db.get_user(target_id)

        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=_format_match_text(target_user),
                parse_mode=ParseMode.HTML,
                reply_markup=_get_match_kb(target_user),
            )
        except Exception as exc:
            logger.warning("Failed to send match message to user %s: %s", user_id, exc)

        if target_user and not target_user.get("is_ai"):
            try:
                await context.bot.send_message(
                    chat_id=target_id,
                    text=_format_match_text(from_user),
                    parse_mode=ParseMode.HTML,
                    reply_markup=_get_match_kb(from_user),
                )
            except Exception as exc:
                logger.warning("Failed to send match message to user %s: %s", target_id, exc)
        return

    elif liked:
        target_user = await db.get_user(target_id)
        if target_user and target_user.get("is_ai"):
            # Enforce 1 match at a time and space multiple fake matches by 4-5 hours
            delay = await db.schedule_next_ai_match_delay(user_id)
            _schedule_ai_match_task(user_id, target_id, context.bot, delay, context.bot_data)
        else:
            # User A liked User B (not yet a match) -> notify User B with message
            await _notify_like_received(user_id, target_id, context, message=direct_message)

    await _show_next_profile(update, user_id, context)


async def swipe_like_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    target_id = context.user_data.get("current_candidate_id")
    if not target_id:
        await discover(update, context)
        return
    await _process_swipe(user_id, target_id, True, update, context)


async def swipe_love_letter_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to write a love letter / direct message to accompany their like."""
    user_id = update.effective_user.id
    target_id = context.user_data.get("current_candidate_id")
    if not target_id:
        await discover(update, context)
        return

    if not await db.can_like_today(user_id):
        await update.message.reply_text(
            "You've hit your daily like limit. Get /premium for unlimited likes!\n\n" + MAIN_MENU_TEXT,
            reply_markup=main_menu_kb(),
        )
        return

    target_user = await db.get_user(target_id)
    target_name = html.escape(str(target_user.get("name", "them"))) if target_user else "them"

    context.user_data["awaiting_love_letter_for"] = target_id
    await update.message.reply_text(
        f"💌 <b>Send a message to {target_name}</b>\n\n"
        f"Write a short message (max {_LOVE_LETTER_MAX_LEN} chars) that will be delivered directly with your like ✨\n\n"
        f"<i>Type your message below or tap ❌ Cancel:</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_kb(),
    )


async def handle_love_letter_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Receives the typed direct message and completes the like swipe."""
    if not update.message or not update.message.text:
        return False

    target_id = context.user_data.pop("awaiting_love_letter_for", None)
    if not target_id:
        return False

    text = update.message.text.strip()
    user_id = update.effective_user.id

    if text in {"❌ Cancel", "Cancel", "/cancel"}:
        await update.message.reply_text("Cancelled direct message.", reply_markup=swipe_reply_kb())
        await _show_next_profile(update, user_id, context)
        return True

    # Enforce love letter length cap
    if len(text) > _LOVE_LETTER_MAX_LEN:
        context.user_data["awaiting_love_letter_for"] = target_id  # restore state
        await update.message.reply_text(
            f"Message is too long ({len(text)} chars). Please keep it under {_LOVE_LETTER_MAX_LEN} characters:",
            reply_markup=cancel_kb(),
        )
        return True

    await update.message.reply_text("💌 <i>Delivering your message with your like...</i>", parse_mode=ParseMode.HTML)
    await _process_swipe(user_id, target_id, True, update, context, direct_message=text)
    return True


async def swipe_dislike_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    target_id = context.user_data.get("current_candidate_id")
    if not target_id:
        await discover(update, context)
        return
    await _process_swipe(user_id, target_id, False, update, context)


async def swipe_sleep_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("current_candidate_id", None)
    await update.message.reply_text(MAIN_MENU_TEXT, reply_markup=main_menu_kb())


async def undo_swipe_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Undoes the last swipe for premium users.
    Removes the swipe record from DB and re-displays that profile.
    """
    user_id = update.effective_user.id
    user = await db.get_user(user_id)

    if not db.is_user_premium(user):
        await update.message.reply_text(
            "↩️ Undo swipe is a ⭐ *Premium* feature.\n\nUpgrade with /premium for unlimited undos!",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu_kb(),
        )
        return

    last_swipe = await db.undo_last_swipe(user_id)
    if not last_swipe:
        await update.message.reply_text(
            "Nothing to undo — you haven't swiped anyone yet!",
            reply_markup=main_menu_kb(),
        )
        return

    restored_profile = await db.get_user(last_swipe["to_id"])
    if not restored_profile or not restored_profile.get("profile_complete"):
        await update.message.reply_text(
            "↩️ Swipe undone, but that profile is no longer available.",
            reply_markup=main_menu_kb(),
        )
        return

    context.user_data["current_candidate_id"] = restored_profile["user_id"]
    await update.message.reply_text("↩️ <i>Swipe undone — here they are again:</i>", parse_mode=ParseMode.HTML)
    await _show_profile(update.message, restored_profile)


async def handle_like_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    sender_id = int(query.data.split(":")[1])
    user_b_id = update.effective_user.id

    user_b = await db.get_user(user_b_id)
    if not user_b or not user_b.get("profile_complete"):
        await query.message.reply_text(
            "You need to create a profile first! Use /editprofile to get started.",
            reply_markup=main_menu_kb(),
        )
        return

    if not await db.can_like_today(user_b_id):
        await query.message.reply_text(
            "You've hit your daily like limit. Get /premium for unlimited likes!",
        )
        return

    is_match = await db.record_swipe(user_b_id, sender_id, liked=True)
    await db.increment_like_counter(user_b_id)

    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    if is_match:
        context.user_data.pop("current_candidate_id", None)
        from_user = await db.get_user(sender_id)
        target_user = await db.get_user(user_b_id)

        if from_user and not from_user.get("is_ai"):
            try:
                await context.bot.send_message(
                    chat_id=sender_id,
                    text=_format_match_text(target_user),
                    parse_mode=ParseMode.HTML,
                    reply_markup=_get_match_kb(target_user),
                )
            except Exception as exc:
                logger.warning("Failed to send match message to user %s: %s", sender_id, exc)

        if target_user:
            try:
                await context.bot.send_message(
                    chat_id=user_b_id,
                    text=_format_match_text(from_user),
                    parse_mode=ParseMode.HTML,
                    reply_markup=_get_match_kb(from_user),
                )
            except Exception as exc:
                logger.warning("Failed to send match message to user %s: %s", user_b_id, exc)
    else:
        # Like recorded but not yet mutual — confirm to the user
        await query.message.reply_text(
            "❤️ Liked! If they like you back, you'll get a match notification.",
            reply_markup=main_menu_kb(),
        )


async def handle_pass_like(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    sender_id = int(query.data.split(":")[1])
    user_b_id = update.effective_user.id

    await db.record_swipe(user_b_id, sender_id, liked=False)

    try:
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("Passed.")
    except Exception:
        pass


async def handle_swipe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    action, target_id_str = query.data.split(":")
    target_id = int(target_id_str)
    liked = action == "like"
    from_id = update.effective_user.id

    user = await db.get_user(from_id)
    if not user or not user.get("profile_complete"):
        await query.message.reply_text(
            "Your profile was not found or is incomplete. Use /editprofile to set it up!",
            reply_markup=main_menu_kb(),
        )
        return

    if liked and not await db.can_like_today(from_id):
        await query.message.reply_text(
            "You've hit your daily like limit. Get /premium for unlimited likes!\n\n" + MAIN_MENU_TEXT,
            reply_markup=main_menu_kb(),
        )
        return

    is_match = await db.record_swipe(from_id, target_id, liked)
    if liked:
        await db.increment_like_counter(from_id)

    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    if is_match:
        context.user_data.pop("current_candidate_id", None)
        from_user = await db.get_user(from_id)
        target_user = await db.get_user(target_id)

        try:
            await context.bot.send_message(
                chat_id=from_id,
                text=_format_match_text(target_user),
                parse_mode=ParseMode.HTML,
                reply_markup=_get_match_kb(target_user),
            )
        except Exception as exc:
            logger.warning("Failed to send match message to user %s: %s", from_id, exc)

        if target_user and not target_user.get("is_ai"):
            try:
                await context.bot.send_message(
                    chat_id=target_id,
                    text=_format_match_text(from_user),
                    parse_mode=ParseMode.HTML,
                    reply_markup=_get_match_kb(from_user),
                )
            except Exception as exc:
                logger.warning("Failed to send match message to user %s: %s", target_id, exc)
        return

    elif liked:
        target_user = await db.get_user(target_id)
        if target_user and target_user.get("is_ai"):
            delay = await db.schedule_next_ai_match_delay(from_id)
            _schedule_ai_match_task(from_id, target_id, context.bot, delay, context.bot_data)
        else:
            await _notify_like_received(from_id, target_id, context)

    await _show_next_profile(query, from_id, context)
