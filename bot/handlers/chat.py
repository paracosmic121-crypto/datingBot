"""
In-Bot 1-on-1 Chat Handler.
Enables direct messaging between real users and interactive Grok AI personas.
"""
from __future__ import annotations

import asyncio
import html
import logging
import time

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import ContextTypes

import db
from bot.keyboards import chat_room_kb, main_menu_kb, matches_list_kb, start_chat_kb
from bot.services.grok import generate_grok_reply
from bot.handlers.start import MAIN_MENU_TEXT

logger = logging.getLogger(__name__)


async def start_chat_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Entry callback when a user taps [ 💬 Start Chat ]."""
    query = update.callback_query
    if query:
        await query.answer()

    target_id_str = query.data.split(":")[1] if query and query.data else ""
    if not target_id_str:
        return

    target_id = int(target_id_str)
    user_id = update.effective_user.id

    partner = await db.get_user(target_id)
    if not partner:
        target_msg = query.message if query else update.message
        await target_msg.reply_text("This profile is no longer available.", reply_markup=main_menu_kb())
        return

    # Set active chat in user_data session
    context.user_data["active_chat_with"] = target_id

    partner_name = html.escape(str(partner.get("name", "Partner")))
    partner_age = partner.get("age", "—")
    partner_location = html.escape(str(partner.get("location", "—")))

    intro_text = (
        f"🐼 <b>Chat Connected!</b>\n\n"
        f"You are now chatting with <b>{partner_name}</b> ({partner_age}, {partner_location}) ✨\n\n"
        f"<i>Say hello to start the conversation... 👋</i>\n\n"
        f"Type /end or tap <b>🚪 Stop Chat</b> anytime to return to the main menu."
    )

    target_msg = query.message if query else update.message
    await target_msg.reply_text(intro_text, parse_mode=ParseMode.HTML, reply_markup=chat_room_kb())

    # If partner is an AI persona, check if an opening greeting should be sent
    if partner.get("is_ai"):
        history = await db.get_chat_history(user_id, target_id, limit=2)
        if not history:
            opening_line = partner.get("opening_line", "Heyy! 😊")
            await context.bot.send_chat_action(chat_id=user_id, action=ChatAction.TYPING)
            await asyncio.sleep(5)
            await db.save_chat_message(target_id, user_id, opening_line)
            await target_msg.reply_text(
                f"<b>{partner_name}</b>:\n{html.escape(opening_line)}",
                parse_mode=ParseMode.HTML,
                reply_markup=chat_room_kb(),
            )


async def exit_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Exits the active chat session."""
    active_with = context.user_data.pop("active_chat_with", None)
    msg = update.message if update.message else (update.callback_query.message if update.callback_query else None)
    if update.callback_query:
        await update.callback_query.answer()

    if msg:
        await msg.reply_text(
            "🚫 <b>Chat Ended.</b>\n\n"
            "Use <b>1 🚀</b> to discover more profiles or /chats to view your matches.",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb(),
        )


async def list_chats_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shows all mutual matches to easily resume chats."""
    query = update.callback_query
    if query:
        await query.answer()

    user_id = update.effective_user.id
    matches = await db.get_user_matches(user_id)

    target_msg = query.message if query else update.message
    if not matches:
        await target_msg.reply_text(
            "You don't have any matches yet!\n"
            "Tap <b>1 🚀</b> to view profiles and start matching.",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb(),
        )
        return

    await target_msg.reply_text(
        "💬 <b>Your Matches & Chats</b>\n"
        "Tap anyone below to open the chat room:",
        parse_mode=ParseMode.HTML,
        reply_markup=matches_list_kb(matches),
    )


async def in_chat_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Handles messages when user is currently in a 1-on-1 chat session.
    Returns True if handled, False otherwise.
    """
    if not update.message or not update.message.text:
        return False

    target_id = context.user_data.get("active_chat_with")
    if not target_id:
        return False

    text = update.message.text.strip()
    user_id = update.effective_user.id

    # Check for exit / next triggers
    if text in {"/end", "/exit", "🚪 Stop Chat", "Stop Chat", "stop"}:
        await exit_chat_handler(update, context)
        return True

    if text in {"/next", "next", "Next"}:
        context.user_data.pop("active_chat_with", None)
        from bot.handlers.discover import discover
        await discover(update, context)
        return True

    user = await db.get_user(user_id)
    partner = await db.get_user(target_id)
    if not partner:
        context.user_data.pop("active_chat_with", None)
        await update.message.reply_text(
            "This user is no longer available. Chat closed.",
            reply_markup=main_menu_kb(),
        )
        return True

    # Save user message
    await db.save_chat_message(user_id, target_id, text)

    # 1. AI Persona response via Grok
    if partner.get("is_ai"):
        # Per-user rate limit: no more than one AI reply every 2 seconds
        last_reply_at: float = context.user_data.get("last_ai_reply_at", 0.0)
        if time.monotonic() - last_reply_at < 2.0:
            # Too fast — silently ignore this message to avoid burning API credits
            return True
        context.user_data["last_ai_reply_at"] = time.monotonic()

        partner_name = html.escape(str(partner.get("name", "Partner")))

        await context.bot.send_chat_action(chat_id=user_id, action=ChatAction.TYPING)
        history = await db.get_chat_history(user_id, target_id, limit=12)
        ai_reply_task = asyncio.create_task(generate_grok_reply(partner, user, history, text))
        await asyncio.sleep(5)
        ai_reply = await ai_reply_task

        # Save AI reply
        await db.save_chat_message(target_id, user_id, ai_reply)

        await update.message.reply_text(
            f"<b>{partner_name}</b>:\n{html.escape(ai_reply)}",
            parse_mode=ParseMode.HTML,
            reply_markup=chat_room_kb(),
        )
        return True

    # 2. Real User relay
    sender_name = html.escape(str(user.get("name", update.effective_user.first_name or "Someone") if user else "Someone"))
    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=f"💬 <b>{sender_name}</b>:\n{html.escape(text)}",
            parse_mode=ParseMode.HTML,
            reply_markup=start_chat_kb(user_id),
        )
    except Exception as exc:
        logger.warning("Could not deliver chat message to real user %s: %s", target_id, exc)

    return True
