from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📋 实例列表", callback_data="instances:list")],
            [
                InlineKeyboardButton("📤 上传 OCI 配置", callback_data="oci:upload_help"),
                InlineKeyboardButton("✅ 配置检查", callback_data="oci:check"),
            ],
            [InlineKeyboardButton("🌐 Cloudflare DNS", callback_data="cf:help")],
            [InlineKeyboardButton("ℹ️ 帮助", callback_data="help")],
        ]
    )


def instance_actions(instance_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("启动", callback_data=f"instance:START:{instance_id}"),
                InlineKeyboardButton("停止", callback_data=f"instance:STOP:{instance_id}"),
                InlineKeyboardButton("重启", callback_data=f"instance:SOFTRESET:{instance_id}"),
            ],
            [InlineKeyboardButton("返回", callback_data="instances:list")],
        ]
    )
