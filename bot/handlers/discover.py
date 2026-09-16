import html
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import db
from bot.keyboards import like_received_kb, main_menu_kb, start_chat_kb, swipe_reply_kb
from bot.handlers.start import MAIN_MENU_TEXT

logger = logging.getLogger(__name__)


def _format_match_text(matched_user: dict) -> str:
    name = html.escape(str(matched_user.get("name", "Someone")))
    age = matched_user.get("age", "—")
    location = html.escape(str(matched_user.get("location", "—")))
    return (
        f"🎉 <b>IT'S A MATCH!</b>\n\n"
        f"You and <b>{name}</b> ({age}, {location}) liked each other!\n\n"
        f"Tap below to start chatting directly in the bot: 👇"
    )


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
        await target_msg.reply_photo(photo=photo, caption=caption, reply_markup=kb)
    else:
        await target_msg.reply_text(caption, reply_markup=kb)


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


async def _notify_like_received(from_id: int, to_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send profile of User A to User B WITHOUT username/chat link."""
    from_user = await db.get_user(from_id)
    if not from_user:
        return

    name = html.escape(str(from_user.get("name", "Someone")))
    age = html.escape(str(from_user.get("age", "—")))
    location = html.escape(str(from_user.get("location", "—")))
    description = html.escape(str(from_user.get("description", "")))
    caption = (
        f"💌 <b>Someone liked your profile!</b>\n\n"
        f"{name}, {age} — {location}\n\n"
        f"{description}"
    )
    photo = from_user.get("photo_file_id")
    kb = like_received_kb(from_id)

    try:
        if photo:
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


async def _process_swipe(user_id: int, target_id: int, liked: bool, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

    is_match = await db.record_swipe(user_id, target_id, liked)
    if liked:
        await db.increment_like_counter(user_id)

    if is_match:
        from_user = await db.get_user(user_id)
        target_user = await db.get_user(target_id)

        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=_format_match_text(target_user),
                parse_mode=ParseMode.HTML,
                reply_markup=start_chat_kb(target_id),
            )
        except Exception as exc:
            logger.warning("Failed to send match message to user %s: %s", user_id, exc)

        try:
            await context.bot.send_message(
                chat_id=target_id,
                text=_format_match_text(from_user),
                parse_mode=ParseMode.HTML,
                reply_markup=start_chat_kb(user_id),
            )
        except Exception as exc:
            logger.warning("Failed to send match message to user %s: %s", target_id, exc)
    elif liked:
        # User A liked User B (not yet a match) -> notify User B without username/link
        await _notify_like_received(user_id, target_id, context)

    await _show_next_profile(update, user_id, context)


async def swipe_like_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    target_id = context.user_data.get("current_candidate_id")
    if not target_id:
        await discover(update, context)
        return
    await _process_swipe(user_id, target_id, True, update, context)


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
        from_user = await db.get_user(sender_id)
        target_user = await db.get_user(user_b_id)

        if from_user:
            try:
                await context.bot.send_message(
                    chat_id=sender_id,
                    text=_format_match_text(target_user),
                    parse_mode=ParseMode.HTML,
                    reply_markup=start_chat_kb(user_b_id),
                )
            except Exception as exc:
                logger.warning("Failed to send match message to user %s: %s", sender_id, exc)

        if target_user:
            try:
                await context.bot.send_message(
                    chat_id=user_b_id,
                    text=_format_match_text(from_user),
                    parse_mode=ParseMode.HTML,
                    reply_markup=start_chat_kb(sender_id),
                )
            except Exception as exc:
                logger.warning("Failed to send match message to user %s: %s", user_b_id, exc)


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
        from_user = await db.get_user(from_id)
        target_user = await db.get_user(target_id)

        try:
            await context.bot.send_message(
                chat_id=from_id,
                text=_format_match_text(target_user),
                parse_mode=ParseMode.HTML,
                reply_markup=start_chat_kb(target_id),
            )
        except Exception as exc:
            logger.warning("Failed to send match message to user %s: %s", from_id, exc)

        try:
            await context.bot.send_message(
                chat_id=target_id,
                text=_format_match_text(from_user),
                parse_mode=ParseMode.HTML,
                reply_markup=start_chat_kb(from_id),
            )
        except Exception as exc:
            logger.warning("Failed to send match message to user %s: %s", target_id, exc)
    elif liked:
        await _notify_like_received(from_id, target_id, context)

    await _show_next_profile(query, from_id, context)
