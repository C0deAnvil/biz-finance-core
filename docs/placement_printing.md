# 销售单和置货单打印

销售订单提交后，销售单和置货单可在页面下载 A5 横向 PDF 或打开打印对话框。销售单包含客户、价格、金额及收款账户；置货单只包含置货与发货作业信息，不含客户公司、单价、金额。草稿及作废单不能打印正式单据。

## 部署

后端需安装新增依赖 `reportlab`，然后运行 `alembic upgrade head`，重启后端并重新构建前端。迁移创建打印设备、打印任务和三天保留的调用审计表。项目随附的 Noto Sans SC 字体会嵌入 PDF，部署时必须保留 `assets/fonts`。部署前按现有流程备份数据库。

## 自动打印设备

1. 系统管理员打开“系统管理 → 打印设备与记录”，生成设备认证码。认证码仅显示一次；服务端只存 SHA-256 摘要。设备丢失或认证码疑似泄露时，立即在此页停用并生成新码。
2. 在打印机旁的可信电脑安装 Python 3，并确认其系统命令可无弹窗打印 PDF。Windows 可使用已安装的 SumatraPDF 命令行打印；Linux 可使用 `lp`。打印驱动和纸张尺寸由设备本地配置。
3. 通过设备本地安全环境变量配置以下值，并启动 `python scripts/placement_print_agent.py`。可用操作系统服务或任务计划程序保持常驻。

```text
YECAI_SERVER_URL=https://your-server.example
YECAI_PRINT_AGENT_TOKEN=管理员生成的认证码
YECAI_PRINT_POLL_SECONDS=60
YECAI_PRINT_COMMAND_JSON=["lp","-d","warehouse-printer","{file}"]
```

Windows 的命令示例：`["C:\\Program Files\\SumatraPDF\\SumatraPDF.exe","-print-to","打印机名称","-silent","{file}"]`。环境变量值为 JSON 数组；实际可执行文件路径与打印机名称以设备为准。不要将认证码写入仓库、日志或命令行参数。

设备每分钟 `POST /api/printing/agent/jobs/claim` 领取任务，然后用 `GET /api/printing/agent/jobs/{id}/pdf` 下载 PDF，并通过 `POST /api/printing/agent/jobs/{id}/complete` 回报结果。所有设备接口使用 `X-Print-Agent-Token` 请求头，PDF 下载还需 `X-Print-Lease-Token`。设备程序只接受 HTTPS，正式启用前应先给网站配置有效 TLS 证书。同一置货队列只有一个自动打印任务；失败最多自动重试五次，之后管理员可在页面手动重排。领取租约五分钟超时后可重新领取，因此打印机已接收作业但回报失败的极端情况下可能重复出纸；管理员应结合页面记录核对。

管理员页可查看设备、任务、认证失败、调用时间、服务器看到的来源 IP 和 User-Agent。来源 IP 取直连地址；若服务部署在反向代理后，需在代理和服务器层配置可信来源 IP，不应直接相信设备提交的 `X-Forwarded-For`。调用日志由后端每小时清理一次，保留时间约三天。
