from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


def main_menu_kb() -> ReplyKeyboardMarkup:
    """Bottom reply keyboard with compact number shortcuts."""
    return ReplyKeyboardMarkup(
        [["1 🚀", "2", "3", "4", "5"]],
        resize_keyboard=True,
    )


def swipe_reply_kb() -> ReplyKeyboardMarkup:
    """Bottom reply keyboard for swiping candidates."""
    return ReplyKeyboardMarkup(
        [["❤️", "👎", "💤"]],
        resize_keyboard=True,
    )


def cancel_kb() -> ReplyKeyboardMarkup:
    """Bottom reply keyboard during editing."""
    return ReplyKeyboardMarkup(
        [["❌ Cancel"]],
        resize_keyboard=True,
    )


def edit_profile_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("Name", callback_data="edit_name")],
        [InlineKeyboardButton("Age", callback_data="edit_age")],
        [InlineKeyboardButton("Location", callback_data="edit_location")],
        [InlineKeyboardButton("Description", callback_data="edit_description")],
        [InlineKeyboardButton("Photo", callback_data="edit_photo")],
        [InlineKeyboardButton("⬅️ Back", callback_data="back_to_menu")],
    ]
    return InlineKeyboardMarkup(rows)


def swipe_kb(target_user_id: int) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("❌ Dislike", callback_data=f"dislike:{target_user_id}"),
            InlineKeyboardButton("❤️ Like", callback_data=f"like:{target_user_id}"),
        ],
    ]
    return InlineKeyboardMarkup(rows)


def like_received_kb(sender_user_id: int) -> InlineKeyboardMarkup:
    """Buttons on the 'Someone liked your profile' notification."""
    rows = [
        [
            InlineKeyboardButton("❤️ Like back", callback_data=f"like_back:{sender_user_id}"),
            InlineKeyboardButton("❌ Pass", callback_data=f"pass_like:{sender_user_id}"),
        ],
    ]
    return InlineKeyboardMarkup(rows)


def premium_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("⭐ 15 Days — 100 Stars", callback_data="buy_premium:15")],
        [InlineKeyboardButton("⭐ 30 Days — 200 Stars", callback_data="buy_premium:30")],
    ]
    return InlineKeyboardMarkup(rows)
