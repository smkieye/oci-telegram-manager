# OCI Telegram Manager

一个轻量的 Telegram Bot，用来管理 Oracle Cloud Infrastructure（OCI）实例，并可选同步 Cloudflare DNS。

## 功能

- Telegram 白名单，只允许指定用户操作
- 上传并保存多个 OCI API 账号配置（config + .pem 私钥）
- 在 Telegram 内新增、查看、切换、检查、删除 OCI 账号
- 查看当前 OCI 账号可访问的实例列表
- 显示实例状态、规格、公网 IP、私网 IP、区域
- 启动 / 停止 / 软重启实例
- 可选 Cloudflare A 记录更新：`/sync_dns node.example.com 1.2.3.4`
- Docker Compose 部署
- 一键安装脚本
- 管理命令：`oci-manager`

## 一键安装

推荐先下载再执行：

```bash
curl -fsSL https://raw.githubusercontent.com/你的用户名/oci-telegram-manager/main/install.sh -o install.sh
sudo REPO_URL="https://github.com/你的用户名/oci-telegram-manager.git" bash install.sh
```

如果脚本已经在项目目录内，也可以：

```bash
sudo bash install.sh
```

安装脚本会询问：

- Telegram Bot Token
- 允许访问的 Telegram 用户 ID
- 是否启用 Cloudflare DNS 管理
- Cloudflare API Token / Zone ID，可选

## Telegram Bot 准备

1. 找 `@BotFather`
2. 发送 `/newbot`
3. 按提示创建 Bot
4. 保存 Bot Token

获取自己的 Telegram ID：可使用 `@userinfobot`。

## OCI 多账号配置

启动后，在 Telegram 给 Bot 发送：

```text
/add_account
```

然后按提示完成三步：

1. 输入配置名称，例如：`首尔账号`
2. 粘贴 OCI `config` 内容
3. 上传对应的 `.pem` 私钥文件

Bot 会把每个账号独立保存到：

```text
/app/data/accounts/<账号ID>/config
/app/data/accounts/<账号ID>/oci_api_key.pem
/app/data/accounts/<账号ID>/meta.json
```

`config` 里原来的 `key_file` 会自动改写为容器路径，例如：

```ini
key_file=/app/data/accounts/seoul/oci_api_key.pem
```

常用命令：

```text
/accounts                 查看账号列表并通过按钮切换
/add_account              新增 OCI 账号
/use_account <账号ID>     切换当前账号
/delete_account <账号ID>  删除账号
/check                    检查当前账号 config 和私钥
/instances                查看当前账号实例
/cancel                   取消正在进行的账号录入
```

仍保留旧版单账号文件上传兼容模式：直接上传文件名为 `config` 和 `oci_api_key.pem` 的文件，会保存到 `/app/data/oci/`。新部署建议使用 `/add_account`。

## 管理命令

```bash
oci-manager status
oci-manager logs
oci-manager restart
oci-manager update
oci-manager uninstall
```

## 手动 Docker 部署

```bash
cp .env.example .env
nano .env
docker compose build
docker compose up -d
docker compose logs -f
```

## 安全建议

- 只把自己的 Telegram ID 加入白名单
- Cloudflare Token 只给 Zone Read + DNS Edit，并限定具体域名
- OCI API 用户只授予必要权限
- 不要把 `.env`、`data/oci/config`、`data/oci/oci_api_key.pem` 提交到 Git
- 删除/重启实例前确认实例名称和 OCID

## 当前 MVP 限制

- 支持 Telegram 内多账号管理；暂未提供截图那种 Web 表单 UI
- 成本分析、自动巡检、抢机任务属于后续扩展模块
- 对免费资源的判断依赖后续接入 OCI Usage/Budget API
