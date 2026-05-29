from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📋 实例列表", callback_data="instances:list")],
            [InlineKeyboardButton("⚡ 抢机", callback_data="sniper:menu")],
            [InlineKeyboardButton("👥 OCI 账号管理", callback_data="accounts:list")],
            [
                InlineKeyboardButton("📤 新增 OCI 账号", callback_data="accounts:add"),
                InlineKeyboardButton("✅ 当前账号检查", callback_data="oci:check"),
            ],
            [InlineKeyboardButton("🌐 Cloudflare DNS", callback_data="cf:help")],
            [InlineKeyboardButton("ℹ️ 帮助", callback_data="help")],
        ]
    )


def accounts_menu(accounts: list[tuple[str, str]], current_id: str | None = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for account_id, name in accounts:
        prefix = "✅ " if account_id == current_id else ""
        rows.append([InlineKeyboardButton(f"{prefix}{name}", callback_data=f"accounts:use:{account_id}")])
    rows.append([InlineKeyboardButton("➕ 新增账号", callback_data="accounts:add")])
    rows.append([InlineKeyboardButton("返回主菜单", callback_data="help")])
    return InlineKeyboardMarkup(rows)


def account_actions(account_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("设为当前", callback_data=f"accounts:use:{account_id}"),
                InlineKeyboardButton("检查", callback_data=f"accounts:check:{account_id}"),
            ],
            [InlineKeyboardButton("删除", callback_data=f"accounts:delete:{account_id}")],
            [InlineKeyboardButton("账号列表", callback_data="accounts:list")],
        ]
    )


def instance_actions(instance_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("启动", callback_data=f"instance:START:{instance_key}"),
                InlineKeyboardButton("停止", callback_data=f"instance:STOP:{instance_key}"),
                InlineKeyboardButton("重启", callback_data=f"instance:SOFTRESET:{instance_key}"),
            ],
            [InlineKeyboardButton("返回", callback_data="instances:list")],
        ]
    )


def sniper_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📌 抢机配置说明", callback_data="sniper:help")],
            [InlineKeyboardButton("📝 粘贴/更新抢机模板", callback_data="sniper:set_template")],
            [InlineKeyboardButton("👁 查看当前模板", callback_data="sniper:show_template")],
            [
                InlineKeyboardButton("🚀 抢一次", callback_data="sniper:launch_once"),
                InlineKeyboardButton("🔁 连续抢机", callback_data="sniper:start_loop"),
            ],
            [InlineKeyboardButton("⏹ 停止连续抢机", callback_data="sniper:stop_loop")],
            [InlineKeyboardButton("返回主菜单", callback_data="help")],
        ]
    )
