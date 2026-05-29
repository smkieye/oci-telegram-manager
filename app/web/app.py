from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import parse, request as urlrequest

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import Settings
from app.services.account_store import Account, AccountStore
from app.services.oci_config import validate_uploaded_oci_files
from app.services.oci_service import OCIService

settings = Settings.from_env()
store = AccountStore(settings.accounts_dir)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
app = FastAPI(title="OCI Telegram Manager Web")

COOKIE_NAME = "oci_web_session"
PENDING_COOKIE_NAME = "oci_web_pending"
AUTH_FILE = settings.data_dir / "web_auth.json"
PENDING_LOGINS: dict[str, dict[str, object]] = {}
PASSWORD_HASH_ITERATIONS = 260_000
OTP_TTL_SECONDS = 300


@dataclass
class WebSniperTask:
    account_id: str
    started_at: datetime
    attempts: int = 0
    running: bool = True
    stop_requested: bool = False
    success: bool = False
    message: str = ""
    last_error: str = ""
    launched: int = 0


tasks: dict[str, WebSniperTask] = {}


def _hash_password(password: str, salt_hex: str | None = None) -> dict[str, object]:
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_HASH_ITERATIONS)
    return {"salt": salt.hex(), "hash": digest.hex(), "iterations": PASSWORD_HASH_ITERATIONS}


def _write_auth_record(record: dict[str, object]) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    AUTH_FILE.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    AUTH_FILE.chmod(0o600)


def _auth_record() -> dict[str, object] | None:
    if AUTH_FILE.exists():
        try:
            return json.loads(AUTH_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
    if settings.web_admin_password:
        record = _hash_password(settings.web_admin_password)
        record["created_at"] = datetime.now().isoformat()
        _write_auth_record(record)
        return record
    return None


def _set_web_password(password: str) -> None:
    record = _hash_password(password)
    record["updated_at"] = datetime.now().isoformat()
    _write_auth_record(record)


def _verify_password(password: str) -> bool:
    record = _auth_record()
    if not record:
        return False
    salt = str(record.get("salt", ""))
    expected = str(record.get("hash", ""))
    candidate = _hash_password(password, salt).get("hash", "")
    return hmac.compare_digest(str(candidate), expected)


def _token() -> str:
    secret = settings.web_session_secret or settings.bot_token
    record = _auth_record() or {}
    password_hash = str(record.get("hash", ""))
    return hmac.new(secret.encode(), password_hash.encode(), hashlib.sha256).hexdigest()


def _cookie_secure(request: Request) -> bool:
    return request.headers.get("x-forwarded-proto", request.url.scheme) == "https"


def _is_authed(request: Request) -> bool:
    expected = _token()
    supplied = request.cookies.get(COOKIE_NAME, "")
    return bool(_auth_record()) and hmac.compare_digest(supplied, expected)


def _send_telegram_otp(code: str) -> None:
    text = f"🔐 OCI Web 登录验证码：{code}\n5 分钟内有效。如果不是你本人操作，请立即修改 Web 密码。"
    for chat_id in settings.allowed_user_ids:
        data = parse.urlencode({"chat_id": str(chat_id), "text": text}).encode("utf-8")
        req = urlrequest.Request(f"https://api.telegram.org/bot{settings.bot_token}/sendMessage", data=data, method="POST")
        with urlrequest.urlopen(req, timeout=10) as response:  # noqa: S310 - Telegram Bot API endpoint
            response.read()


def _create_pending_login() -> str:
    pending_id = secrets.token_urlsafe(24)
    code = f"{secrets.randbelow(1_000_000):06d}"
    PENDING_LOGINS[pending_id] = {"code": code, "expires_at": time.time() + OTP_TTL_SECONDS, "attempts": 0}
    _send_telegram_otp(code)
    return pending_id


def require_auth(request: Request) -> None:
    if not _is_authed(request):
        raise HTTPException(status_code=303, headers={"Location": "/login"})


def _current_account() -> Account | None:
    return store.get_current()


def _sniper_template_path(account: Account) -> Path:
    return account.path / "sniper_template.json"


def _default_sniper_template() -> dict[str, Any]:
    return {
        "count": 1,
        "interval_seconds": 60,
        "cpu": 1,
        "memory_gb": 6,
        "disk_gb": 50,
        "arch": "arm",
        "os_type": "ubuntu",
        "root_password": "random",
        "display_name": "free-arm",
        "assign_public_ip": True,
    }


def _load_sniper_template(account: Account) -> dict[str, Any]:
    path = _sniper_template_path(account)
    if not path.exists():
        template = _default_sniper_template()
        _save_sniper_template(account, template)
        return template
    data = json.loads(path.read_text(encoding="utf-8"))
    template = _default_sniper_template()
    template.update(data)
    return template


def _save_sniper_template(account: Account, template: dict[str, Any]) -> None:
    path = _sniper_template_path(account)
    path.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
    path.chmod(0o600)


def _format_number(value) -> str:
    if value is None or value == "":
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number.is_integer() else str(number)


def _format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{days}天{hours}小时{minutes}分钟{secs}秒"


def _instance_view(account: Account, item) -> dict[str, str]:
    return {
        "id": item.id,
        "name": item.display_name,
        "account": account.name,
        "state": item.lifecycle_state,
        "region": item.region or account.region or "-",
        "arch": item.arch or "-",
        "cpu": _format_number(item.cpu),
        "memory_gb": _format_number(item.memory_gb),
        "disk_gb": _format_number(item.disk_gb),
        "shape": item.shape or "-",
        "public_ip": item.public_ip or "-",
    }


def _success_message(account: Account, instance, template: dict[str, Any], attempts: int, started_at: datetime) -> str:
    now = datetime.now()
    duration = _format_duration((now - started_at).total_seconds())
    return (
        f"🎉 用户：[{account.name}] 开机成功 🎉\n"
        f"时间： {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"Region： {instance.region or account.region or '-'}\n"
        f"CPU类型： {str(template.get('arch', 'arm')).upper()}\n"
        f"CPU： {template.get('cpu', '-')}\n"
        f"内存（GB）： {template.get('memory_gb', '-')}\n"
        f"磁盘大小（GB）： {template.get('disk_gb', '-')}\n"
        f"Shape： {instance.shape or template.get('shape') or '-'}\n"
        f"公网IP： {instance.public_ip or '获取中/暂未分配'}\n"
        f"root密码： {template.get('root_password', '')}\n"
        f"开机次数：{attempts}\n"
        f"开机时长：{duration}"
    )


async def _launch_batch(account: Account, template: dict[str, Any], attempts: int, started_at: datetime) -> tuple[int, str]:
    count = max(1, int(template.get("count", 1)))
    interval = max(1, int(template.get("interval_seconds", 60)))
    service = OCIService(account.config_path)
    launched = 0
    last_message = ""
    for idx in range(count):
        instance = await asyncio.to_thread(service.launch_instance, template)
        launched += 1
        last_message = _success_message(account, instance, template, attempts, started_at)
        if idx < count - 1:
            await asyncio.sleep(interval)
    return launched, last_message


async def _sniper_loop(task_key: str) -> None:
    task = tasks[task_key]
    try:
        account = store.get_account(task.account_id)
        template = _load_sniper_template(account)
        if template.get("root_password") == "random":
            template["root_password"] = OCIService.generate_root_password()
            _save_sniper_template(account, template)
        interval = max(1, int(template.get("interval_seconds", 60)))
        while not task.stop_requested:
            task.attempts += 1
            try:
                launched, message = await _launch_batch(account, template, task.attempts, task.started_at)
                task.launched = launched
                task.message = message
                task.success = True
                task.running = False
                return
            except Exception as exc:  # noqa: BLE001 - show provider error in status
                task.last_error = str(exc)[:500]
                await asyncio.sleep(interval)
        task.message = f"已手动停止，尝试 {task.attempts} 轮。"
    finally:
        task.running = False


async def _sniper_once(task_key: str) -> None:
    task = tasks[task_key]
    try:
        account = store.get_account(task.account_id)
        template = _load_sniper_template(account)
        if template.get("root_password") == "random":
            template["root_password"] = OCIService.generate_root_password()
            _save_sniper_template(account, template)
        task.attempts = 1
        launched, message = await _launch_batch(account, template, task.attempts, task.started_at)
        task.launched = launched
        task.message = message
        task.success = True
    except Exception as exc:  # noqa: BLE001
        task.last_error = str(exc)[:500]
        task.message = f"本次抢机未成功：{task.last_error}"
    finally:
        task.running = False


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "web_enabled": bool(_auth_record())})


@app.post("/login")
async def login(request: Request, password: str = Form(...)):
    if not _verify_password(password):
        return RedirectResponse("/login?error=1", status_code=303)
    try:
        pending_id = _create_pending_login()
    except Exception:  # noqa: BLE001
        return RedirectResponse("/login?otp_error=1", status_code=303)
    response = RedirectResponse("/verify", status_code=303)
    response.set_cookie(PENDING_COOKIE_NAME, pending_id, httponly=True, secure=_cookie_secure(request), samesite="lax", max_age=OTP_TTL_SECONDS)
    return response


@app.get("/verify", response_class=HTMLResponse)
async def verify_page(request: Request):
    return templates.TemplateResponse("verify.html", {"request": request})


@app.post("/verify")
async def verify(request: Request, code: str = Form(...)):
    pending_id = request.cookies.get(PENDING_COOKIE_NAME, "")
    pending = PENDING_LOGINS.get(pending_id)
    if not pending or float(pending.get("expires_at", 0)) < time.time():
        PENDING_LOGINS.pop(pending_id, None)
        return RedirectResponse("/login?expired=1", status_code=303)
    pending["attempts"] = int(pending.get("attempts", 0)) + 1
    if int(pending["attempts"]) > 5 or not hmac.compare_digest(str(pending.get("code", "")), code.strip()):
        if int(pending["attempts"]) > 5:
            PENDING_LOGINS.pop(pending_id, None)
            return RedirectResponse("/login?expired=1", status_code=303)
        return RedirectResponse("/verify?error=1", status_code=303)
    PENDING_LOGINS.pop(pending_id, None)
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(PENDING_COOKIE_NAME)
    response.set_cookie(COOKIE_NAME, _token(), httponly=True, secure=_cookie_secure(request), samesite="lax", max_age=7 * 86400)
    return response


@app.post("/logout")
async def logout(_: None = Depends(require_auth)):
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    response.delete_cookie(PENDING_COOKIE_NAME)
    return response


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, _: None = Depends(require_auth)):
    return templates.TemplateResponse("settings.html", {"request": request})


@app.post("/settings/password")
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    _: None = Depends(require_auth),
):
    if not _verify_password(current_password):
        return RedirectResponse("/settings?error=current", status_code=303)
    if len(new_password) < 12 or new_password != confirm_password:
        return RedirectResponse("/settings?error=new", status_code=303)
    _set_web_password(new_password)
    response = RedirectResponse("/login?changed=1", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, _: None = Depends(require_auth)):
    accounts = store.list_accounts()
    current = _current_account()
    return templates.TemplateResponse("dashboard.html", {"request": request, "accounts": accounts, "current": current, "tasks": tasks})


@app.get("/accounts", response_class=HTMLResponse)
async def accounts_page(request: Request, _: None = Depends(require_auth)):
    return templates.TemplateResponse("accounts.html", {"request": request, "accounts": store.list_accounts(), "current_id": store.get_current_id()})


@app.post("/accounts/current")
async def set_current(account_id: str = Form(...), _: None = Depends(require_auth)):
    store.set_current(account_id)
    return RedirectResponse("/accounts", status_code=303)


@app.post("/accounts/add")
async def add_account(
    name: str = Form(...),
    config_text: str = Form(...),
    key_file: UploadFile = File(...),
    _: None = Depends(require_auth),
):
    raw_key = await key_file.read()
    store.create_account(name, config_text, raw_key)
    return RedirectResponse("/accounts", status_code=303)


@app.get("/instances", response_class=HTMLResponse)
async def instances_page(request: Request, _: None = Depends(require_auth)):
    account = _current_account()
    instances = []
    error = ""
    if account:
        ok, message = validate_uploaded_oci_files(account.path)
        if not ok:
            error = message
        else:
            try:
                instances = [_instance_view(account, item) for item in OCIService(account.config_path).list_instances()]
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
    return templates.TemplateResponse("instances.html", {"request": request, "account": account, "instances": instances, "error": error})


@app.post("/instances/action")
async def instance_action(instance_id: str = Form(...), action: str = Form(...), _: None = Depends(require_auth)):
    account = _current_account()
    if not account:
        return RedirectResponse("/instances", status_code=303)
    OCIService(account.config_path).instance_action(instance_id, action)
    return RedirectResponse("/instances", status_code=303)


@app.get("/sniper", response_class=HTMLResponse)
async def sniper_page(request: Request, _: None = Depends(require_auth)):
    account = _current_account()
    template = _load_sniper_template(account) if account else _default_sniper_template()
    task = tasks.get(account.id) if account else None
    if task and task.running:
        task.message = task.message or f"运行中，已尝试 {task.attempts} 轮。"
    return templates.TemplateResponse("sniper.html", {"request": request, "account": account, "template": template, "task": task})


@app.post("/sniper/save")
async def sniper_save(
    count: int = Form(...),
    interval_seconds: int = Form(...),
    cpu: int = Form(...),
    memory_gb: int = Form(...),
    disk_gb: int = Form(...),
    arch: str = Form(...),
    os_type: str = Form(...),
    root_password: str = Form("random"),
    display_name: str = Form("free-arm"),
    _: None = Depends(require_auth),
):
    account = _current_account()
    if account:
        template = _load_sniper_template(account)
        template.update({
            "count": count,
            "interval_seconds": interval_seconds,
            "cpu": cpu,
            "memory_gb": memory_gb,
            "disk_gb": disk_gb,
            "arch": arch,
            "os_type": os_type,
            "root_password": root_password.strip() or "random",
            "display_name": display_name.strip() or f"free-{arch}",
            "assign_public_ip": True,
        })
        _save_sniper_template(account, template)
    return RedirectResponse("/sniper", status_code=303)


@app.post("/sniper/start")
async def sniper_start(background: BackgroundTasks, _: None = Depends(require_auth)):
    account = _current_account()
    if account and not (tasks.get(account.id) and tasks[account.id].running):
        tasks[account.id] = WebSniperTask(account_id=account.id, started_at=datetime.now())
        background.add_task(_sniper_loop, account.id)
    return RedirectResponse("/sniper", status_code=303)


@app.post("/sniper/stop")
async def sniper_stop(_: None = Depends(require_auth)):
    account = _current_account()
    if account and account.id in tasks:
        tasks[account.id].stop_requested = True
    return RedirectResponse("/sniper", status_code=303)


@app.post("/sniper/once")
async def sniper_once(background: BackgroundTasks, _: None = Depends(require_auth)):
    account = _current_account()
    if account:
        key = f"{account.id}:once:{time.time()}"
        tasks[key] = WebSniperTask(account_id=account.id, started_at=datetime.now())
        background.add_task(_sniper_once, key)
    return RedirectResponse("/sniper", status_code=303)
