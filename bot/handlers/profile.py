import logging

from telegram import Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import db
from bot.keyboards import cancel_kb, main_menu_kb
from bot.states import AGE, DESCRIPTION, LOCATION, NAME, PHOTO
from bot.handlers.start import MAIN_MENU_TEXT
from config import settings

logger = logging.getLogger(__name__)


def _format_profile_caption(user: dict | None) -> str:
    if not user:
        return "Profile not found."
    name = user.get("name", "—")
    age = user.get("age", "—")
    location = user.get("location", "—")
    description = user.get("description", "")
    caption = f"{name}, {age} — {location}\n\n{description}"
    return caption


# --------------------------------------------------------------------------- #
# View profile
# --------------------------------------------------------------------------- #

async def _send_profile(update_or_query, user: dict | None, kb=None) -> None:
    target_msg = update_or_query.message if hasattr(update_or_query, "message") else update_or_query
    if not user or not user.get("profile_complete"):
        await target_msg.reply_text(
            "⚠️ Profile not found or incomplete. Use /editprofile to set it up.",
            reply_markup=kb or main_menu_kb(),
        )
        return

    caption = _format_profile_caption(user)
    photo = user.get("photo_file_id")
    if photo:
        try:
            await target_msg.reply_photo(photo=photo, caption=caption, reply_markup=kb)
        except Exception:
            await target_msg.reply_text(caption, reply_markup=kb)
    else:
        await target_msg.reply_text(
            caption + "\n\n(No photo yet — tap 3 to add one)", reply_markup=kb
        )


async def myprofile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    target = query if query else update
    target_msg = target.message if hasattr(target, "message") else target
    if query:
        await query.answer()

    u = update.effective_user
    if u:
        await db.upsert_user_basic(u.id, u.username, u.first_name)

    user = await db.get_user(update.effective_user.id)
    if not user or not user.get("profile_complete"):
        await target_msg.reply_text(
            "⚠️ Your profile is not set up \n\n"
            "Use /editprofile to create your profile now!",
            reply_markup=main_menu_kb(),
        )
        return

    await _send_profile(target, user)
    await target_msg.reply_text(MAIN_MENU_TEXT, reply_markup=main_menu_kb())


# --------------------------------------------------------------------------- #
# Edit profile — ConversationHandler & Shortcuts
# --------------------------------------------------------------------------- #

async def edit_profile_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    target_msg = query.message if query else update.message
    if query:
        await query.answer()

    u = update.effective_user
    if u:
        await db.upsert_user_basic(u.id, u.username, u.first_name)

    context.user_data.pop("single_field_edit", None)
    await target_msg.reply_text(
        "Let's set up your profile. What's your name?", reply_markup=cancel_kb()
    )
    return NAME


async def photo_edit_shortcut(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    target_msg = query.message if query else update.message
    if query:
        await query.answer()

    user_id = update.effective_user.id
    user = await db.get_user(user_id)
    if not user or not user.get("profile_complete"):
        u = update.effective_user
        if u:
            await db.upsert_user_basic(u.id, u.username, u.first_name)
        context.user_data.pop("single_field_edit", None)
        await target_msg.reply_text(
            "You don't have a complete profile yet! Let's set it up from the beginning.\nWhat's your name?",
            reply_markup=cancel_kb(),
        )
        return NAME

    context.user_data["single_field_edit"] = "photo"
    await target_msg.reply_text(
        "Send a new profile photo 📸 (or /cancel to return):", reply_markup=cancel_kb()
    )
    return PHOTO


async def text_edit_shortcut(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    target_msg = query.message if query else update.message
    if query:
        await query.answer()

    user_id = update.effective_user.id
    user = await db.get_user(user_id)
    if not user or not user.get("profile_complete"):
        u = update.effective_user
        if u:
            await db.upsert_user_basic(u.id, u.username, u.first_name)
        context.user_data.pop("single_field_edit", None)
        await target_msg.reply_text(
            "You don't have a complete profile yet! Let's set it up from the beginning.\nWhat's your name?",
            reply_markup=cancel_kb(),
        )
        return NAME

    context.user_data["single_field_edit"] = "description"
    await target_msg.reply_text(
        "Send your new profile bio / text 📝 (or /cancel to return):", reply_markup=cancel_kb()
    )
    return DESCRIPTION


async def edit_field_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point when user taps one specific field callback."""
    query = update.callback_query
    await query.answer()
    field = query.data.replace("edit_", "")
    context.user_data["single_field_edit"] = field
    prompts = {
        "name": ("What's your name?", NAME),
        "age": ("How old are you?", AGE),
        "location": ("Which city are you in?", LOCATION),
        "description": ("Write a short bio about yourself.", DESCRIPTION),
        "photo": ("Send a new profile photo.", PHOTO),
    }
    text, state = prompts[field]
    await query.message.reply_text(text, reply_markup=cancel_kb())
    return state


async def _maybe_end_single_field_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int | None:
    """If this was a single-field edit, finish here."""
    if context.user_data.get("single_field_edit"):
        context.user_data.pop("single_field_edit", None)
        await update.message.reply_text("✅ Profile updated!\n\n" + MAIN_MENU_TEXT, reply_markup=main_menu_kb())
        return ConversationHandler.END
    return None


async def receive_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    if not (1 <= len(name) <= 50):
        await update.message.reply_text("Name should be 1-50 characters. Try again:")
        return NAME
    await db.update_profile_field(update.effective_user.id, "name", name)

    ended = await _maybe_end_single_field_edit(update, context)
    if ended is not None:
        return ended
    await update.message.reply_text("How old are you?", reply_markup=cancel_kb())
    return AGE


async def receive_age(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text.strip()
    if not raw.isdigit() or not (settings.MIN_AGE <= int(raw) <= settings.MAX_AGE):
        await update.message.reply_text(
            f"Please enter a valid age ({settings.MIN_AGE}-{settings.MAX_AGE}):"
        )
        return AGE
    await db.update_profile_field(update.effective_user.id, "age", int(raw))

    ended = await _maybe_end_single_field_edit(update, context)
    if ended is not None:
        return ended
    await update.message.reply_text("Which city are you in?", reply_markup=cancel_kb())
    return LOCATION


async def receive_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    location = update.message.text.strip()
    if not (1 <= len(location) <= 50):
        await update.message.reply_text("Location should be 1-50 characters. Try again:")
        return LOCATION
    await db.update_profile_field(update.effective_user.id, "location", location)

    ended = await _maybe_end_single_field_edit(update, context)
    if ended is not None:
        return ended
    await update.message.reply_text("Write a short bio about yourself.", reply_markup=cancel_kb())
    return DESCRIPTION


async def receive_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    description = update.message.text.strip()
    if len(description) > 500:
        await update.message.reply_text("Keep your bio under 500 characters. Try again:")
        return DESCRIPTION
    await db.update_profile_field(update.effective_user.id, "description", description)

    ended = await _maybe_end_single_field_edit(update, context)
    if ended is not None:
        return ended
    await update.message.reply_text("Last step — send a profile photo.", reply_markup=cancel_kb())
    return PHOTO


async def receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.photo:
        await update.message.reply_text("That doesn't look like a photo. Please send an image:")
        return PHOTO
    file_id = update.message.photo[-1].file_id
    user_id = update.effective_user.id
    await db.update_profile_field(user_id, "photo_file_id", file_id)
    await db.mark_profile_complete(user_id)

    ended = await _maybe_end_single_field_edit(update, context)
    if ended is not None:
        return ended

    await update.message.reply_text(
        "🎉 Your profile is complete!\n\n" + MAIN_MENU_TEXT,
        reply_markup=main_menu_kb(),
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("single_field_edit", None)
    await update.message.reply_text("Cancelled.\n\n" + MAIN_MENU_TEXT, reply_markup=main_menu_kb())
    return ConversationHandler.END


def build_edit_profile_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("editprofile", edit_profile_entry),
            MessageHandler(filters.Regex(r"^(2|Edit my profile|edit profile)$"), edit_profile_entry),
            MessageHandler(filters.Regex(r"^(3|Change my photo/video|change photo)$"), photo_edit_shortcut),
            MessageHandler(filters.Regex(r"^(4|Change profile text|change text)$"), text_edit_shortcut),
            CallbackQueryHandler(
                edit_field_entry, pattern=r"^edit_(name|age|location|description|photo)$"
            ),
        ],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(r"^(❌ Cancel|cancel|/cancel)$"), receive_name)],
            AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(r"^(❌ Cancel|cancel|/cancel)$"), receive_age)],
            LOCATION: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(r"^(❌ Cancel|cancel|/cancel)$"), receive_location)],
            DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(r"^(❌ Cancel|cancel|/cancel)$"), receive_description)],
            PHOTO: [MessageHandler(filters.PHOTO, receive_photo)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(filters.Regex(r"^(❌ Cancel|cancel|/cancel)$"), cancel),
        ],
        name="edit_profile_conversation",
        persistent=False,
        per_message=False,
        per_chat=True,
        per_user=True,
    )
