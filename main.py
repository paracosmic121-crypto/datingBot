import asyncio
import logging

import uvicorn
from telegram.request import HTTPXRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)

import db
from api.app import app as fastapi_app
from bot.handlers import discover, premium, profile, start
from config import settings

import warnings
from telegram.warnings import PTBUserWarning

warnings.filterwarnings("ignore", category=PTBUserWarning)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors caused by Updates gracefully."""
    logger.warning("Error encountered during update handling: %s", context.error)


def build_application() -> Application:
    request = HTTPXRequest(
        connection_pool_size=8,
        connect_timeout=settings.REQUEST_TIMEOUT,
        read_timeout=settings.REQUEST_TIMEOUT,
        write_timeout=settings.REQUEST_TIMEOUT,
        pool_timeout=settings.REQUEST_TIMEOUT,
        proxy=settings.PROXY_URL,
    )
    application = Application.builder().token(settings.BOT_TOKEN).request(request).build()

    # --- Profile edit wizard (ConversationHandler must be registered before general text handlers) ---
    application.add_handler(profile.build_edit_profile_conversation())

    # --- Basic commands ---
    application.add_handler(CommandHandler("start", start.start))
    application.add_handler(CommandHandler("help", start.help_command))
    application.add_handler(CommandHandler("menu", start.show_main_menu))
    application.add_handler(CallbackQueryHandler(start.show_main_menu, pattern=r"^back_to_menu$"))

    # --- Profile commands ---
    application.add_handler(CommandHandler("myprofile", profile.myprofile))
    application.add_handler(CallbackQueryHandler(profile.myprofile, pattern=r"^myprofile$"))
    application.add_handler(
        CallbackQueryHandler(profile.edit_profile_entry, pattern=r"^edit_profile$")
    )

    # --- Discovery / swiping ---
    application.add_handler(CommandHandler("discover", discover.discover))
    application.add_handler(CallbackQueryHandler(discover.discover, pattern=r"^discover$"))
    application.add_handler(
        CallbackQueryHandler(discover.handle_swipe, pattern=r"^(like|dislike):\d+$")
    )
    application.add_handler(
        CallbackQueryHandler(discover.handle_like_back, pattern=r"^like_back:\d+$")
    )
    application.add_handler(
        CallbackQueryHandler(discover.handle_pass_like, pattern=r"^pass_like:\d+$")
    )

    # --- Swiping ReplyKeyboard triggers (bottom buttons) ---
    application.add_handler(MessageHandler(filters.Regex(r"^(❤️|👍|Like)"), discover.swipe_like_handler))
    application.add_handler(MessageHandler(filters.Regex(r"^(👎|Dislike)"), discover.swipe_dislike_handler))
    application.add_handler(MessageHandler(filters.Regex(r"^(💤|Sleep|Menu|Back)"), discover.swipe_sleep_handler))

    # --- Main Menu ReplyKeyboard triggers (bottom 1 🚀, 2, 3, 4, 5) ---
    application.add_handler(MessageHandler(filters.Regex(r"^(1\s*🚀|1)$"), discover.discover))
    application.add_handler(MessageHandler(filters.Regex(r"^2$"), profile.myprofile))
    application.add_handler(MessageHandler(filters.Regex(r"^3$"), profile.photo_edit_shortcut))
    application.add_handler(MessageHandler(filters.Regex(r"^4$"), profile.text_edit_shortcut))
    application.add_handler(MessageHandler(filters.Regex(r"^5$"), premium.premium_info))

    # --- Premium ---
    application.add_handler(CommandHandler("premium", premium.premium_info))
    application.add_handler(CallbackQueryHandler(premium.premium_info, pattern=r"^premium$"))
    application.add_handler(
        CallbackQueryHandler(premium.buy_premium, pattern=r"^buy_premium(:.*)?$")
    )
    application.add_handler(PreCheckoutQueryHandler(premium.precheckout_callback))
    application.add_handler(
        MessageHandler(filters.SUCCESSFUL_PAYMENT, premium.successful_payment_callback)
    )

    # --- Error Handler ---
    application.add_error_handler(global_error_handler)

    return application


async def main() -> None:
    settings.validate()
    await db.ensure_indexes()

    application = build_application()

    uv_config = uvicorn.Config(
        fastapi_app, host=settings.API_HOST, port=settings.API_PORT, log_level="info"
    )
    server = uvicorn.Server(uv_config)

    async with application:
        await application.start()
        await application.updater.start_polling(drop_pending_updates=True)
        logger.info("Bot polling started, admin API on %s:%s", settings.API_HOST, settings.API_PORT)
        try:
            await server.serve()
        finally:
            await application.updater.stop()
            await application.stop()


if __name__ == "__main__":
    asyncio.run(main())
