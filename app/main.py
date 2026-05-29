from __future__ import annotations

import logging

from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from app.config import Settings, mask_secret
from app.handlers.auth import restricted
from app.handlers.bot import (
    add_account,
    callback_router,
    cancel,
    check_oci,
    delete_account,
    handle_document,
    handle_text,
    help_command,
    list_accounts_command,
    list_instances,
    sniper_command,
    start,
    sync_dns,
    use_account,
)
from app.services.account_store import AccountStore


def build_application(settings: Settings) -> Application:
    app = Application.builder().token(settings.bot_token).build()
    app.bot_data["settings"] = settings
    app.bot_data["account_store"] = AccountStore(settings.accounts_dir)

    app.add_handler(CommandHandler("start", restricted(settings)(start)))
    app.add_handler(CommandHandler("help", restricted(settings)(help_command)))
    app.add_handler(CommandHandler("check", restricted(settings)(check_oci)))
    app.add_handler(CommandHandler("instances", restricted(settings)(list_instances)))
    app.add_handler(CommandHandler("sniper", restricted(settings)(sniper_command)))
    app.add_handler(CommandHandler("accounts", restricted(settings)(list_accounts_command)))
    app.add_handler(CommandHandler("add_account", restricted(settings)(add_account)))
    app.add_handler(CommandHandler("use_account", restricted(settings)(use_account)))
    app.add_handler(CommandHandler("delete_account", restricted(settings)(delete_account)))
    app.add_handler(CommandHandler("cancel", restricted(settings)(cancel)))
    app.add_handler(CommandHandler("sync_dns", restricted(settings)(sync_dns)))
    app.add_handler(MessageHandler(filters.Document.ALL, restricted(settings)(handle_document)))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, restricted(settings)(handle_text)))
    app.add_handler(CallbackQueryHandler(restricted(settings)(callback_router)))
    return app


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=logging.INFO,
    )
    settings = Settings.from_env()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "oci").mkdir(parents=True, exist_ok=True)
    settings.accounts_dir.mkdir(parents=True, exist_ok=True)
    logging.info(
        "Starting OCI Telegram Manager. token=%s allowed_users=%s cloudflare=%s",
        mask_secret(settings.bot_token),
        sorted(settings.allowed_user_ids),
        settings.cloudflare_enabled,
    )
    build_application(settings).run_polling(allowed_updates=None)


if __name__ == "__main__":
    main()
