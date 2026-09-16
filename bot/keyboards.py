from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from config import settings


def main_menu_kb() -> ReplyKeyboardMarkup:
    """Bottom reply keyboard with compact number shortcuts."""
    return ReplyKeyboardMarkup(
        [["1 🚀", "2", "3", "4", "5"]],
        resize_keyboard=True,
    )


def swipe_reply_kb() -> ReplyKeyboardMarkup:
    """Bottom reply keyboard for swiping candidates with love letter, undo, and sleep buttons."""
    return ReplyKeyboardMarkup(
        [["❤️", "💌", "👎", "↩️", "💤"]],
        resize_keyboard=True,
    )


def gender_selection_kb() -> ReplyKeyboardMarkup:
    """Keyboard for selecting gender."""
    return ReplyKeyboardMarkup(
        [["👨 Male", "👩 Female"], ["❌ Cancel"]],
        resize_keyboard=True,
    )


def chat_room_kb() -> ReplyKeyboardMarkup:
    """Keyboard while chatting inside the bot."""
    return ReplyKeyboardMarkup(
        [["🚪 Stop Chat"]],
        resize_keyboard=True,
    )


def start_chat_kb(target_user_id: int) -> InlineKeyboardMarkup:
    """Inline buttons to start or open a chat with a fake AI match, or continue browsing."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Start Chat", callback_data=f"start_chat:{target_user_id}")],
        [InlineKeyboardButton("🚀 Keep Browsing", callback_data="discover")],
    ])


def real_match_kb(target_user: dict) -> InlineKeyboardMarkup:
    """Inline buttons for real user match with direct Telegram DM link."""
    username = target_user.get("username")
    uid = target_user.get("user_id")
    if username:
        chat_url = f"https://t.me/{username}"
    else:
        chat_url = f"tg://user?id={uid}"

    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Open Telegram Chat", url=chat_url)],
        [InlineKeyboardButton("🚀 Keep Browsing", callback_data="discover")],
    ])


def matches_list_kb(matches: list[dict]) -> InlineKeyboardMarkup:
    """List of matches for easy chat switching."""
    rows = []
    for m in matches:
        name = m.get("name", "Match")
        uid = m.get("user_id")
        rows.append([InlineKeyboardButton(f"💬 {name}", callback_data=f"start_chat:{uid}")])
    rows.append([InlineKeyboardButton("⬅️ Back to Menu", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(rows)


def cancel_kb() -> ReplyKeyboardMarkup:
    """Bottom reply keyboard during editing."""
    return ReplyKeyboardMarkup(
        [["❌ Cancel"]],
        resize_keyboard=True,
    )


def edit_profile_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("Gender", callback_data="edit_gender")],
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
    """Inline keyboard for premium purchase — reads live star amounts from settings."""
    rows = [
        [InlineKeyboardButton(
            f"⭐ 15 Days — {settings.PREMIUM_15D_STARS} Stars",
            callback_data="buy_premium:15",
        )],
        [InlineKeyboardButton(
            f"⭐ 30 Days — {settings.PREMIUM_30D_STARS} Stars",
            callback_data="buy_premium:30",
        )],
    ]
    return InlineKeyboardMarkup(rows)
