from __future__ import annotations

from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from app.config import Settings
from app.keyboards.menu import instance_actions, main_menu
from app.services.cloudflare_service import CloudflareService
from app.services.oci_config import save_uploaded_oci_config, save_uploaded_oci_key, validate_uploaded_oci_files
from app.services.oci_service import OCIService


WELCOME = """🤖 OCI Telegram Manager 已启动

可用功能：
- 查看 OCI 实例
- 启动 / 停止 / 重启实例
- 上传 OCI config / oci_api_key.pem
- 可选同步 Cloudflare A 记录

请先上传 OCI 配置文件：
1. 发送文件名为 config 的 OCI 配置
2. 发送文件名为 oci_api_key.pem 的私钥
"""


def _settings(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    return context.application.bot_data["settings"]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(WELCOME, reply_markup=main_menu())


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "命令：\n"
        "/start - 主菜单\n"
        "/instances - 查看实例\n"
        "/check - 检查 OCI 文件\n"
        "/sync_dns <域名> <实例公网IP> - 更新 Cloudflare A 记录\n\n"
        "危险操作会通过按钮触发，请确认实例名称后再操作。",
        reply_markup=main_menu(),
    )


async def check_oci(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = _settings(context)
    ok, message = validate_uploaded_oci_files(settings.data_dir / "oci")
    prefix = "✅" if ok else "⚠️"
    await update.effective_message.reply_text(f"{prefix} {message}")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = _settings(context)
    document = update.effective_message.document
    if not document:
        return
    filename = Path(document.file_name or "").name
    if filename not in {"config", "oci_api_key.pem"}:
        await update.effective_message.reply_text("只接受文件名为 config 或 oci_api_key.pem 的文件。")
        return

    tg_file = await document.get_file()
    blob = await tg_file.download_as_bytearray()
    if filename == "config":
        save_uploaded_oci_config(bytes(blob).decode("utf-8"), settings.data_dir / "oci")
        await update.effective_message.reply_text("✅ 已保存 OCI config，并自动改写 key_file 为容器内路径。")
    else:
        save_uploaded_oci_key(bytes(blob), settings.data_dir / "oci")
        await update.effective_message.reply_text("✅ 已保存 oci_api_key.pem。")

    ok, message = validate_uploaded_oci_files(settings.data_dir / "oci")
    if ok:
        await update.effective_message.reply_text(f"✅ {message}，现在可以点 实例列表。", reply_markup=main_menu())


async def list_instances(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = _settings(context)
    ok, message = validate_uploaded_oci_files(settings.data_dir / "oci")
    if not ok:
        await update.effective_message.reply_text(f"⚠️ {message}")
        return
    await update.effective_message.reply_text("正在读取 OCI 实例列表，请稍候……")
    service = OCIService(settings.oci_config_path)
    try:
        instances = service.list_instances()
    except Exception as exc:
        await update.effective_message.reply_text(f"❌ OCI API 调用失败：{exc}")
        return
    if not instances:
        await update.effective_message.reply_text("当前没有发现可访问实例。")
        return
    for item in instances:
        text = (
            f"🖥 {item.display_name}\n"
            f"状态：{item.lifecycle_state}\n"
            f"区域：{item.region or '-'}\n"
            f"规格：{item.shape or '-'}\n"
            f"公网 IP：{item.public_ip or '-'}\n"
            f"私网 IP：{item.private_ip or '-'}\n"
            f"ID：`{item.id}`"
        )
        await update.effective_message.reply_text(text, reply_markup=instance_actions(item.id), parse_mode="Markdown")


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    settings = _settings(context)

    if data == "instances:list":
        await list_instances(update, context)
    elif data == "oci:upload_help":
        await query.message.reply_text("请直接向机器人发送两个文件：config 和 oci_api_key.pem。")
    elif data == "oci:check":
        ok, message = validate_uploaded_oci_files(settings.data_dir / "oci")
        await query.message.reply_text(("✅ " if ok else "⚠️ ") + message)
    elif data == "cf:help":
        await query.message.reply_text("Cloudflare 同步命令：/sync_dns <域名> <IP>。需在 .env 中配置 Token 和 Zone ID。")
    elif data == "help":
        await query.message.reply_text(WELCOME, reply_markup=main_menu())
    elif data.startswith("instance:"):
        _, action, instance_id = data.split(":", 2)
        await query.message.reply_text(f"正在执行 {action}：{instance_id[:24]}…")
        try:
            state = OCIService(settings.oci_config_path).instance_action(instance_id, action)
            await query.message.reply_text(f"✅ 操作已提交，当前状态：{state}")
        except Exception as exc:
            await query.message.reply_text(f"❌ 操作失败：{exc}")


async def sync_dns(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = _settings(context)
    if not settings.cloudflare_enabled:
        await update.effective_message.reply_text("⚠️ 未启用 Cloudflare。请在 .env 配置 CLOUDFLARE_API_TOKEN 和 CLOUDFLARE_ZONE_ID。")
        return
    if len(context.args) != 2:
        await update.effective_message.reply_text("用法：/sync_dns node.example.com 1.2.3.4")
        return
    name, ip = context.args
    try:
        result = CloudflareService(settings.cloudflare_api_token, settings.cloudflare_zone_id).upsert_record(name, ip)
        await update.effective_message.reply_text(f"✅ DNS 已更新：{name} -> {result.get('content', ip)}")
    except Exception as exc:
        await update.effective_message.reply_text(f"❌ Cloudflare 更新失败：{exc}")
