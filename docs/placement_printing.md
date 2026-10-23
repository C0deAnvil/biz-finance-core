# 销售单和置货单打印

销售订单提交后，销售单和置货单可在页面下载 A5 横向 PDF 或打开打印对话框。销售单包含客户、价格、金额及收款账户；置货单只包含置货与发货作业信息，不含客户公司、单价、金额。草稿及作废单不能打印正式单据。

## 部署

后端及打印电脑的项目虚拟环境需安装 `requirements.txt` 中的依赖（包括 `reportlab` 和浏览器兼容 HTTPS 客户端 `curl_cffi`），然后运行 `alembic upgrade head`，重启后端并重新构建前端。迁移创建打印设备、打印任务和三天保留的调用审计表。项目随附的 Noto Sans SC 字体会嵌入 PDF，部署时必须保留 `assets/fonts`。部署前按现有流程备份数据库。

## 自动打印设备

1. 系统管理员打开“系统管理 → 打印设备与记录”，生成设备认证码。认证码仅显示一次；服务端只存 SHA-256 摘要。设备丢失或认证码疑似泄露时，立即在此页停用并生成新码。
2. 在打印机旁的可信电脑安装 Python 3，并确认其系统命令可无弹窗打印 PDF。Windows 可使用已安装的 SumatraPDF 命令行打印；Linux 可使用 `lp`。打印驱动和纸张尺寸由设备本地配置。
3. 通过设备本地安全环境变量配置以下值，并启动 `python scripts/placement_print_agent.py`。可用操作系统服务或任务计划程序保持常驻。

```text
YECAI_SERVER_URL=https://www.cphwchem.xyz:8001
YECAI_PRINT_AGENT_TOKEN=管理员生成的认证码
YECAI_PRINT_POLL_SECONDS=60
YECAI_PRINT_COMMAND_JSON=["lp","-d","warehouse-printer","{file}"]
```

Windows 的命令示例：`["C:\\Program Files\\SumatraPDF\\SumatraPDF.exe","-print-to","打印机名称","-silent","{file}"]`。环境变量值为 JSON 数组；实际可执行文件路径与打印机名称以设备为准。蓝牙打印机需要先在 Windows 中配对并安装驱动，使其出现在系统打印机列表里；打印客户端不直接调用蓝牙 SDK。不要将认证码写入仓库、日志或命令行参数。

### Windows 本地试打印

有界面的本地客户端适合在仓库电脑试运行。在项目根目录启动：

```powershell
venv\Scripts\pythonw.exe scripts\placement_print_desktop.py
```

也可以构建给普通使用人员双击运行的单文件程序：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_placement_print_exe.ps1
```

构建结果为 `dist\业财置货自动打印.exe`。将它复制到仓库电脑后可以直接双击，不需要在该电脑安装 Python 或复制项目源码。仓库电脑仍需安装目标打印机的 Windows 驱动和 SumatraPDF；打印机、SumatraPDF 路径、服务器、认证码、轮询周期等配置继续保存在当前 Windows 用户的 `%LOCALAPPDATA%\YecaiPlacementPrinter\settings.json`，旧版脚本保存过的配置也会自动沿用。勾选开机启动后，注册表只记录该 exe 的路径和 `--autostart`，不会写入认证码。因此应先把 exe 放在最终固定目录，再启用开机启动；移动 exe 后需重新保存一次配置。

界面默认选择 `EPSONB8696A (L3250 Series)`（或上次保存的打印机），并从 Windows 打印后台读取已经安装的打印机。点击“Windows 打印机”右侧箭头可查看全部；如需筛选，在独立的“搜索打印机”框输入关键词。点击“刷新全部”会清空关键词并重新获取完整列表。蓝牙、USB、有线网络或无线网络打印机都一样：先在 Windows 中安装，再从列表选择。若未检测到打印程序，点击“官方下载”安装 SumatraPDF，或者使用“选择程序”定位便携版的 `SumatraPDF.exe`。先点击“试打印示例单”：程序会生成匿名 A5 横向置货单，使用 `-print-to` 和 `paper=A5,landscape,fit` 提交给指定打印机。纸张和纸盒仍需在打印机/驱动中正确配置。

注意不要把下载的安装包复制并改名为项目根目录的 `SumatraPDF.exe`：缺少同目录的打印引擎 DLL 时，它只会启动安装界面，即使进程返回成功也没有打印。客户端会优先寻找真正安装目录中的程序，并拒绝这种错误文件；若旧配置指向该文件，下次打开会自动改选已安装程序，并提示重新保存。

实际出纸正常后，服务器地址使用 `https://www.cphwchem.xyz:8001`，再填写“打印设备与记录”创建时显示的认证码并点击“保存配置”。程序在当前用户的 `%LOCALAPPDATA%\YecaiPlacementPrinter\settings.json` 保存打印机、程序位置、服务器和轮询间隔；认证码使用 Windows DPAPI 绑定当前用户加密保存，配置文件中不含明文认证码。换电脑或换 Windows 用户后需要重新输入认证码。**已创建的认证码无法从管理页再次查看**，若未保存，需停用旧设备并创建新设备认证码。旧版保存的 `http://8.140.196.85` 会自动迁移到当前 HTTPS 地址，并提示重新保存；本地匿名试打印不受此限制。

点击“单次领取并打印”验证，再点击“开始自动打印”持续轮询。点窗口关闭按钮时，程序询问是否继续后台打印：选择“是”会最小化到任务栏并继续轮询（点击任务栏图标恢复），选择“否”会停止轮询并退出，选择“取消”保留窗口。若尚未启动轮询，选择“是”会先尝试启动；配置无效时不会悄悄隐藏。可勾选“登录 Windows 后自动启动并开始打印”，**再点击“保存配置”**；仅写入当前用户的 Windows 登录启动项，不需要管理员权限，不会在登录前启动。开机自动启动失败时保留可见窗口；后台轮询意外退出时会恢复窗口并提醒。修改已保存的配置后，若轮询正在运行，请先停止再重新开始以应用新值。

也可不使用界面，直接运行以下命令试打印：

先用 PowerShell 的 `Get-Printer | Select-Object Name` 查询打印机名称，并确认纸张设置为 A5 横向。安装支持无弹窗命令行打印 PDF 的程序（例如 SumatraPDF）；使用实际安装路径和实际打印机名称配置本机环境变量。可先运行 `venv\Scripts\python.exe -m scripts.preview_order_printing` 生成不含真实客户信息的示例 PDF，再试打印：

```powershell
$env:YECAI_PRINT_COMMAND_JSON = '["C:\\Program Files\\SumatraPDF\\SumatraPDF.exe","-print-to","你的打印机名称","-silent","{file}"]'
venv\Scripts\python.exe -m scripts.placement_print_agent --test-print .\output\pdf\sample_placement_order.pdf
```

此试打印不连接服务器，也不需要设备认证码。命令正常退出仅表示已提交给本地打印程序，仍须目视确认出纸和版式。之后管理员在“打印设备与记录”创建认证码，在当前 PowerShell 会话中设置 `YECAI_PRINT_AGENT_TOKEN` 和 `YECAI_SERVER_URL=https://www.cphwchem.xyz:8001`，再运行 `venv\Scripts\python.exe -m scripts.placement_print_agent --once` 试领一次任务；确认后不带 `--once` 常驻轮询。正式服务器必须使用 HTTPS；仅本机 `http://127.0.0.1` 或 `http://[::1]` 可用于不经过公网的本地调试。

设备每分钟 `POST /api/printing/agent/jobs/claim` 领取任务，然后用 `GET /api/printing/agent/jobs/{id}/pdf` 下载 PDF，并通过 `POST /api/printing/agent/jobs/{id}/complete` 回报结果。所有设备接口使用 `X-Print-Agent-Token` 请求头，PDF 下载还需 `X-Print-Lease-Token`。设备程序只接受 HTTPS，正式启用前应先给网站配置有效 TLS 证书。同一置货队列只有一个自动打印任务。客户端启动时会恢复此前达到重试上限但仍未打印的任务，并按创建顺序处理“第一台打印设备启用后产生”的所有积压置货单；启用设备之前的历史单据不会突然批量重打。

Windows 客户端每次只向打印机提交一张，随后读取 Windows 打印后台。打印任务仍在队列中时不会提交下一张；若后台报告缺纸、离线、卡纸、暂停或需要人工干预，本张不记为已打印，并停止本轮积压处理。只有观察到本次任务完成并离开 Windows 队列，服务器才自动记为“已打印”。如果驱动只接受任务、但无法提供可靠的完成状态，系统只记为“已提交待确认”，停止继续提交后续单据，并要求库管在待置货列表亲眼确认出纸后点击“确认出纸”。这可以避免把“Sumatra 命令正常退出”误当成实际打印成功，也能减少打印机缓存被大量任务塞满的风险。

普通 Windows 驱动最多只能证明打印后台/驱动已完成任务，无法百分之百感知纸张是否真的落入出纸托盘；打印机自身在任务离开 Windows 队列之后才缺纸、设备断电或驱动不回传硬件状态，仍需要人工核对。若业务必须获得绝对的物理出纸证明，需要打印机厂商状态 SDK、SNMP 状态或外部传感器。领取租约五分钟超时后可重新领取，因此打印机已接收作业但客户端未回报的极端情况下仍可能重复出纸，管理员应结合页面记录核对。

待置货列表会显示“未打印、打印中、已提交待确认、打印异常、已打印”、打印方式、最后打印时间和累计打印次数。自动客户端确认 Windows 队列完成后写入“自动打印”；驱动状态不明确时必须由操作员点击“确认出纸”。页面点击“打印”后也需要由操作员确认实际出纸，确认后写入“手动打印”。仅下载 PDF 不会改变打印状态。服务端只会给仍处于待置货且未记录打印成功的单据创建自动打印任务。

管理员页可查看设备、任务、认证失败、调用时间、服务器看到的来源 IP 和 User-Agent。来源 IP 取直连地址；若服务部署在反向代理后，需在代理和服务器层配置可信来源 IP，不应直接相信设备提交的 `X-Forwarded-For`。调用日志由后端每小时清理一次，保留时间约三天。
