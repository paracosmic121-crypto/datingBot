"""
Premium / monetization using Telegram Stars.
"""
import datetime as dt
import logging

from telegram import LabeledPrice, Update
from telegram.ext import ContextTypes

import db
from bot.keyboards import main_menu_kb, premium_kb
from bot.handlers.start import MAIN_MENU_TEXT
from config import settings

logger = logging.getLogger(__name__)

PREMIUM_PERKS = (
    "⭐ *Match & Meet Premium*\n\n"
    "• 🚀 *High Priority Profile* — displayed first at top in discovery\n"
    "• 👀 *Unlimited Profile Views* (Free users: 10 views / 1 hr)\n"
    "• ❤️ *Unlimited Daily Likes* (Free users: 20 likes / day)\n"
    "• ↩️ *Undo your last swipe*\n\n"
    "Choose your plan:"
)


async def premium_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    target = query if query else update
    target_msg = target.message if hasattr(target, "message") else target
    if query:
        await query.answer()

    u = update.effective_user
    if u:
        await db.upsert_user_basic(u.id, u.username, u.first_name)

    user = await db.get_user(update.effective_user.id)
    if user and db.is_user_premium(user):
        until = user.get("premium_until")
        note = f"\n\n✨ *Your Premium is active*" + (f" until {until:%Y-%m-%d}." if until else ".")
        await target_msg.reply_text(
            PREMIUM_PERKS + note + "\n\n" + MAIN_MENU_TEXT,
            parse_mode="Markdown",
            reply_markup=main_menu_kb(),
        )
        return

    await target_msg.reply_text(
        PREMIUM_PERKS,
        parse_mode="Markdown",
        reply_markup=premium_kb(),
    )


async def buy_premium(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    plan_days = 30
    if query.data and ":" in query.data:
        try:
            plan_days = int(query.data.split(":")[1])
        except (ValueError, IndexError):
            plan_days = 30

    if plan_days == 15:
        title = "Premium — 15 Days"
        stars_amount = settings.PREMIUM_15D_STARS
        payload = f"premium_15d:{update.effective_user.id}"
    else:
        title = "Premium — 30 Days"
        stars_amount = settings.PREMIUM_30D_STARS
        payload = f"premium_30d:{update.effective_user.id}"

    await context.bot.send_invoice(
        chat_id=update.effective_chat.id,
        title=title,
        description="High priority profile, unlimited profile views & unlimited likes.",
        payload=payload,
        provider_token="",  # empty for Telegram Stars
        currency="XTR",
        prices=[LabeledPrice(title, stars_amount)],
    )


async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.pre_checkout_query
    await query.answer(ok=True)


async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    payload = update.message.successful_payment.invoice_payload or ""

    days = 15 if "premium_15d" in payload else 30
    user = await db.get_user(user_id)
    now = dt.datetime.utcnow()

    current_until = user.get("premium_until") if user else None
    if current_until and current_until > now:
        until = current_until + dt.timedelta(days=days)
    else:
        until = now + dt.timedelta(days=days)

    await db.set_premium(user_id, True, until)
    await update.message.reply_text(
        f"🎉 *Premium activated for {days} days!*\n"
        f"Valid until: *{until:%Y-%m-%d}*.\n\n"
        "Enjoy high priority placement, unlimited profile views, and unlimited likes!\n\n"
        + MAIN_MENU_TEXT,
        parse_mode="Markdown",
        reply_markup=main_menu_kb(),
    )
