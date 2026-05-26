from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from app.config import Settings


def is_allowed(settings: Settings, user_id: int | None) -> bool:
    return bool(user_id and user_id in settings.allowed_user_ids)


def restricted(settings: Settings):
    def decorator(func):
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            user_id = update.effective_user.id if update.effective_user else None
            if not is_allowed(settings, user_id):
                if update.effective_message:
                    await update.effective_message.reply_text("⛔ 未授权用户。")
                return None
            return await func(update, context)

        return wrapper

    return decorator
