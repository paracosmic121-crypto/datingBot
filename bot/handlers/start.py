import logging

from telegram import Update
from telegram.ext import ContextTypes

import db
from bot.keyboards import main_menu_kb

logger = logging.getLogger(__name__)

MAIN_MENU_TEXT = (
    "1. View profiles.\n"
    "2. Edit my profile.\n"
    "3. Change my photo/video.\n"
    "4. Change profile text.\n\n"
    "5. Activate Premium — stay at the top ⭐."
)

WELCOME = (
    "👋 Welcome to *Match and Meet*!\n\n"
    "Find people nearby, like profiles you're into, and get matched the "
    "moment someone likes you back.\n\n"
    "Let's get your profile set up so people can start seeing you."
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await db.upsert_user_basic(user.id, user.username, user.first_name)
    user_doc = await db.get_user(user.id)
    if not user_doc or not user_doc.get("profile_complete"):
        await update.message.reply_text(WELCOME, parse_mode="Markdown")
        await update.message.reply_text(
            "Let's create your profile!\nUse /editprofile to get started."
        )
        return

    await update.message.reply_text(MAIN_MENU_TEXT, reply_markup=main_menu_kb())


async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target = update.callback_query if update.callback_query else update
    if update.callback_query:
        await update.callback_query.answer()
        await target.message.reply_text(MAIN_MENU_TEXT, reply_markup=main_menu_kb())
    else:
        await target.message.reply_text(MAIN_MENU_TEXT, reply_markup=main_menu_kb())


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 *Match and Meet Commands & Shortcuts*:\n\n"
        "Tap the buttons at the bottom:\n"
        "• `1 🚀` - View profiles\n"
        "• `2` - Edit my profile\n"
        "• `3` - Change photo/video\n"
        "• `4` - Change profile text\n"
        "• `5` - Activate Premium\n\n"
        "Or use commands:\n"
        "/start - Main menu\n"
        "/discover - View profiles\n"
        "/chats - View your matches & active chats\n"
        "/myprofile - View your profile\n"
        "/editprofile - Setup full profile\n"
        "/premium - Premium info\n"
        "/end - Stop active chat\n"
        "/cancel - Cancel current action",
        parse_mode="Markdown",
        reply_markup=main_menu_kb(),
    )
