from __future__ import annotations

import hashlib
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from app.config import Settings
from app.keyboards.menu import account_actions, accounts_menu, instance_actions, main_menu
from app.services.account_store import Account, AccountStore
from app.services.cloudflare_service import CloudflareService
from app.services.oci_config import save_uploaded_oci_config, save_uploaded_oci_key, validate_uploaded_oci_files
from app.services.oci_service import OCIService


WELCOME = """🤖 OCI Telegram Manager 已启动

可用功能：
- 多 OCI API 账号管理
- 查看当前账号可访问的 OCI 实例
- 启动 / 停止 / 重启实例
- Telegram 内新增账号：配置名称 + OCI config 内容 + .pem 私钥
- 可选同步 Cloudflare A 记录

快速开始：
1. 发送 /add_account 新增 OCI 账号
2. 按提示输入配置名称、粘贴 config、上传 .pem 私钥
3. 发送 /accounts 切换账号
4. 发送 /instances 查看实例
"""


ADD_ACCOUNT_HELP = """请按步骤新增 OCI 账号：

1. 先输入配置名称，例如：首尔账号
2. 再粘贴 OCI config 内容，例如：
[DEFAULT]
user=ocid1.user.oc1..xxx
fingerprint=xx:xx:xx
tenancy=ocid1.tenancy.oc1..xxx
region=ap-seoul-1

3. 最后上传 .pem 私钥文件

如需取消，发送 /cancel。"""


def _settings(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    return context.application.bot_data["settings"]


def _account_store(context: ContextTypes.DEFAULT_TYPE) -> AccountStore:
    settings = _settings(context)
    store = context.application.bot_data.get("account_store")
    if store is None:
        store = AccountStore(settings.accounts_dir)
        context.application.bot_data["account_store"] = store
    return store


def _current_account_or_none(context: ContextTypes.DEFAULT_TYPE) -> Account | None:
    return _account_store(context).get_current()


def _remember_instance_id(context: ContextTypes.DEFAULT_TYPE, account_id: str, instance_id: str) -> str:
    key = hashlib.sha1(f"{account_id}:{instance_id}".encode("utf-8")).hexdigest()[:16]
    mapping = context.application.bot_data.setdefault("instance_ids", {})
    mapping[key] = {"account_id": account_id, "instance_id": instance_id}
    return key


def _resolve_instance_id(context: ContextTypes.DEFAULT_TYPE, key: str) -> tuple[str | None, str]:
    mapping = context.application.bot_data.get("instance_ids", {})
    item = mapping.get(key)
    if item:
        return item.get("account_id"), item["instance_id"]
    # Backward compatibility with old callback data containing full OCID.
    return None, key


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(WELCOME, reply_markup=main_menu())


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "命令：\n"
        "/start - 主菜单\n"
        "/accounts - 查看 / 切换 OCI 账号\n"
        "/add_account - 新增 OCI 账号\n"
        "/cancel - 取消当前录入\n"
        "/instances - 查看当前账号实例\n"
        "/check - 检查当前账号 OCI 文件\n"
        "/use_account <账号ID> - 切换当前账号\n"
        "/delete_account <账号ID> - 删除账号\n"
        "/sync_dns <域名> <实例公网IP> - 更新 Cloudflare A 记录\n\n"
        "危险操作会通过按钮触发，请确认实例名称后再操作。",
        reply_markup=main_menu(),
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("add_account", None)
    await update.effective_message.reply_text("已取消当前录入。", reply_markup=main_menu())


async def add_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["add_account"] = {"step": "name"}
    await update.effective_message.reply_text(ADD_ACCOUNT_HELP)
    await update.effective_message.reply_text("请输入配置名称：")


async def list_accounts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply_accounts(update, context)


async def _reply_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    store = _account_store(context)
    accounts = store.list_accounts()
    current_id = store.get_current_id()
    if not accounts:
        await update.effective_message.reply_text("当前还没有 OCI 账号。发送 /add_account 新增。", reply_markup=main_menu())
        return

    lines = ["👥 OCI 账号列表："]
    for account in accounts:
        marker = "✅ 当前" if account.id == current_id else ""
        lines.append(f"- {account.name} / ID: {account.id} / 区域: {account.region or '-'} {marker}")
    await update.effective_message.reply_text(
        "\n".join(lines),
        reply_markup=accounts_menu([(item.id, item.name) for item in accounts], current_id),
    )


async def use_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.effective_message.reply_text("用法：/use_account <账号ID>。可先发送 /accounts 查看账号ID。")
        return
    try:
        account = _account_store(context).set_current(context.args[0])
    except ValueError as exc:
        await update.effective_message.reply_text(f"⚠️ {exc}")
        return
    await update.effective_message.reply_text(f"✅ 当前账号已切换为：{account.name}（{account.id}）", reply_markup=main_menu())


async def delete_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.effective_message.reply_text("用法：/delete_account <账号ID>。可先发送 /accounts 查看账号ID。")
        return
    try:
        _account_store(context).delete_account(context.args[0])
    except ValueError as exc:
        await update.effective_message.reply_text(f"⚠️ {exc}")
        return
    await update.effective_message.reply_text("✅ 账号已删除。", reply_markup=main_menu())


async def check_oci(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    account = _current_account_or_none(context)
    if account is None:
        await update.effective_message.reply_text("⚠️ 当前没有 OCI 账号。发送 /add_account 新增。")
        return
    ok, message = validate_uploaded_oci_files(account.path)
    prefix = "✅" if ok else "⚠️"
    await update.effective_message.reply_text(f"{prefix} {account.name}：{message}")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    flow = context.user_data.get("add_account")
    if not flow:
        return

    text = (update.effective_message.text or "").strip()
    if flow.get("step") == "name":
        if not text:
            await update.effective_message.reply_text("配置名称不能为空，请重新输入：")
            return
        flow["name"] = text
        flow["step"] = "config"
        await update.effective_message.reply_text("请粘贴 OCI config 内容：")
        return

    if flow.get("step") == "config":
        if "[DEFAULT]" not in text or "tenancy=" not in text or "user=" not in text:
            await update.effective_message.reply_text("⚠️ 这不像完整的 OCI config，请重新粘贴包含 [DEFAULT]、user、tenancy、fingerprint、region 的内容。")
            return
        flow["config"] = text
        flow["step"] = "key"
        await update.effective_message.reply_text("请上传这个账号对应的 .pem 私钥文件。")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = _settings(context)
    document = update.effective_message.document
    if not document:
        return
    filename = Path(document.file_name or "").name

    flow = context.user_data.get("add_account")
    if flow and flow.get("step") == "key":
        if not filename.endswith(".pem"):
            await update.effective_message.reply_text("请上传 .pem 私钥文件。")
            return
        tg_file = await document.get_file()
        blob = bytes(await tg_file.download_as_bytearray())
        if b"BEGIN" not in blob:
            await update.effective_message.reply_text("⚠️ 这个文件不像 PEM 私钥，请确认后重新上传。")
            return
        try:
            account = _account_store(context).create_account(str(flow["name"]), str(flow["config"]), blob)
        except Exception as exc:
            await update.effective_message.reply_text(f"❌ 保存账号失败：{exc}")
            return
        context.user_data.pop("add_account", None)
        await update.effective_message.reply_text(
            f"✅ 已新增 OCI 账号：{account.name}\n账号ID：{account.id}\n区域：{account.region or '-'}\n已设为当前账号。",
            reply_markup=account_actions(account.id),
        )
        return

    # Backward-compatible single-account upload path for old deployments.
    if filename not in {"config", "oci_api_key.pem"}:
        await update.effective_message.reply_text("如需新增多账号，请发送 /add_account；兼容旧模式只接受文件名为 config 或 oci_api_key.pem 的文件。")
        return

    tg_file = await document.get_file()
    blob = await tg_file.download_as_bytearray()
    if filename == "config":
        save_uploaded_oci_config(bytes(blob).decode("utf-8"), settings.data_dir / "oci")
        await update.effective_message.reply_text("✅ 已按旧单账号模式保存 OCI config。建议后续使用 /add_account 管理多账号。")
    else:
        save_uploaded_oci_key(bytes(blob), settings.data_dir / "oci")
        await update.effective_message.reply_text("✅ 已按旧单账号模式保存 oci_api_key.pem。建议后续使用 /add_account 管理多账号。")

    ok, message = validate_uploaded_oci_files(settings.data_dir / "oci")
    if ok:
        await update.effective_message.reply_text(f"✅ {message}，现在可以点 实例列表。", reply_markup=main_menu())


async def list_instances(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    account = _current_account_or_none(context)
    if account is None:
        await update.effective_message.reply_text("⚠️ 当前没有 OCI 账号。发送 /add_account 新增。")
        return
    ok, message = validate_uploaded_oci_files(account.path)
    if not ok:
        await update.effective_message.reply_text(f"⚠️ {account.name}：{message}")
        return
    await update.effective_message.reply_text(f"正在读取 OCI 实例列表，请稍候……\n当前账号：{account.name}")
    service = OCIService(account.config_path)
    try:
        instances = service.list_instances()
    except Exception as exc:
        await update.effective_message.reply_text(f"❌ OCI API 调用失败：{exc}")
        return
    if not instances:
        await update.effective_message.reply_text("当前账号没有发现可访问实例。")
        return
    for item in instances:
        text = (
            f"🖥 {item.display_name}\n"
            f"账号：{account.name}\n"
            f"状态：{item.lifecycle_state}\n"
            f"区域：{item.region or account.region or '-'}\n"
            f"规格：{item.shape or '-'}\n"
            f"公网 IP：{item.public_ip or '-'}\n"
            f"私网 IP：{item.private_ip or '-'}\n"
            f"ID：`{item.id}`"
        )
        instance_key = _remember_instance_id(context, account.id, item.id)
        await update.effective_message.reply_text(text, reply_markup=instance_actions(instance_key), parse_mode="Markdown")


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    settings = _settings(context)
    store = _account_store(context)

    if data == "instances:list":
        await list_instances(update, context)
    elif data == "accounts:list":
        accounts = store.list_accounts()
        if not accounts:
            await query.message.reply_text("当前还没有 OCI 账号。点击新增或发送 /add_account。", reply_markup=main_menu())
        else:
            current_id = store.get_current_id()
            lines = ["👥 OCI 账号列表："]
            for account in accounts:
                marker = "✅ 当前" if account.id == current_id else ""
                lines.append(f"- {account.name} / ID: {account.id} / 区域: {account.region or '-'} {marker}")
            await query.message.reply_text(
                "\n".join(lines),
                reply_markup=accounts_menu([(item.id, item.name) for item in accounts], current_id),
            )
    elif data == "accounts:add":
        context.user_data["add_account"] = {"step": "name"}
        await query.message.reply_text(ADD_ACCOUNT_HELP)
        await query.message.reply_text("请输入配置名称：")
    elif data.startswith("accounts:use:"):
        account_id = data.split(":", 2)[2]
        try:
            account = store.set_current(account_id)
            await query.message.reply_text(f"✅ 当前账号已切换为：{account.name}（{account.id}）", reply_markup=main_menu())
        except ValueError as exc:
            await query.message.reply_text(f"⚠️ {exc}")
    elif data.startswith("accounts:check:"):
        account_id = data.split(":", 2)[2]
        try:
            account = store.get_account(account_id)
            ok, message = store.validate_account(account_id)
            await query.message.reply_text(("✅ " if ok else "⚠️ ") + f"{account.name}：{message}")
        except ValueError as exc:
            await query.message.reply_text(f"⚠️ {exc}")
    elif data.startswith("accounts:delete:"):
        account_id = data.split(":", 2)[2]
        try:
            store.delete_account(account_id)
            await query.message.reply_text("✅ 账号已删除。", reply_markup=main_menu())
        except ValueError as exc:
            await query.message.reply_text(f"⚠️ {exc}")
    elif data == "oci:upload_help":
        await query.message.reply_text("多账号模式请点击 新增 OCI 账号，或发送 /add_account。")
    elif data == "oci:check":
        account = store.get_current()
        if account is None:
            await query.message.reply_text("⚠️ 当前没有 OCI 账号。发送 /add_account 新增。")
        else:
            ok, message = validate_uploaded_oci_files(account.path)
            await query.message.reply_text(("✅ " if ok else "⚠️ ") + f"{account.name}：{message}")
    elif data == "cf:help":
        await query.message.reply_text("Cloudflare 同步命令：/sync_dns <域名> <IP>。需在 .env 中配置 CLOUDFLARE_API_TOKEN 和 CLOUDFLARE_ZONE_ID。")
    elif data == "help":
        await query.message.reply_text(WELCOME, reply_markup=main_menu())
    elif data.startswith("instance:"):
        _, action, instance_key = data.split(":", 2)
        mapped_account_id, instance_id = _resolve_instance_id(context, instance_key)
        try:
            account = store.get_account(mapped_account_id) if mapped_account_id else store.get_current()
        except ValueError:
            account = None
        if account is None:
            await query.message.reply_text("⚠️ 当前没有 OCI 账号，或按钮对应账号已删除。发送 /accounts 选择账号。")
            return
        await query.message.reply_text(f"正在通过账号 {account.name} 执行 {action}：{instance_id[:24]}…")
        try:
            state = OCIService(account.config_path).instance_action(instance_id, action)
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
