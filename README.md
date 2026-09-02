# 业财一体化系统

这是一个基于 `FastAPI + SQLAlchemy + Alembic + MySQL + Vue 3 + Element Plus` 构建的业财一体化系统项目，当前仓库已包含后端接口、数据库模型、前端业务页面、自动迁移启动逻辑，以及一版可运行的核心业务闭环实现。

## 项目特性

- 启动时自动执行 `Alembic upgrade head`
- 全部 ORM 模型统一集中在 `models.py`
- 认证采用 `PyJWT + HttpOnly Cookie + user_sessions` 单端登录方案
- 支持主数据管理：部门、角色、仓库、客户、供应商、产品、原料
- 支持销售、库存、采购、生产、质检、财务、系统管理等核心模块
- 附件采用本地文件存储，元数据写入数据库
- 前端支持按角色预热页面代码与首屏接口数据

## 技术栈

### 后端

- `FastAPI`
- `SQLAlchemy 2.x`
- `Alembic`
- `Pydantic v2`
- `MySQL`
- `PyJWT`

### 前端

- `Vue 3`
- `Vite`
- `Pinia`
- `Vue Router`
- `Element Plus`

## 目录结构

- `main.py`：后端入口，负责应用启动、自动迁移、初始化种子数据
- `config.py`：项目配置与环境变量读取
- `security.py`：密码处理、JWT 编解码
- `dependencies.py`：登录态与权限依赖
- `models.py`：全部数据库 ORM 模型
- `seed.py`：基础主数据初始化
- `database.py`：数据库连接与会话工厂
- `api/`：后端路由层
  - `api/routers/inventory/`：库存模块拆包（demands/transfers/queues/stocks + _common）
- `schemas/`：Pydantic 请求与响应模型
- `services/`：通用能力层与领域服务层
  - `services/sales_service/`：销售拆包（orders/payments/returns + _common）
  - `services/purchase_service/`：采购拆包（orders/payments/receipts + _common）
- `alembic/`：数据库迁移脚本
- `tests/`：pytest 测试套件（独立测试库 yecai_test）
- `scripts/`：运维脚本（run_tests.py 等）
- `uploads/`：本地附件目录
- `frontend/`：Vue 前端项目
- `frontend/src/shared/route-manifest.json`：前后端共享的菜单、路由、预热配置源
- `frontend/src/router/route-config.js`：基于共享清单生成前端路由配置
- `frontend/src/stores/auth.js`：认证状态存储
- `frontend/src/stores/dashboard.js`：首页 KPI、待办、通知状态存储
- `frontend/src/composables/useRouteFocus.js`：业务页 `focusId` 聚焦逻辑复用层

## 启动后端

1. 准备 MySQL 数据库
2. 修改 `alembic.ini` 或设置环境变量 `DATABASE_URL`
3. 安装依赖：

```bash
python -m pip install -r requirements.txt
```

4. 启动后端：

```bash
python main.py
```

默认访问地址：`http://localhost:8000`

## 启动前端

1. 进入前端目录：

```bash
cd frontend
```

2. 安装依赖：

```bash
npm install
```

3. 启动开发环境：

```bash
npm run dev
```

默认访问地址：`http://localhost:5173`

## 默认种子账号

- `sales01` / `ChangeMe123!`
- `inventory01` / `ChangeMe123!`
- `purchase01` / `ChangeMe123!`
- `production01` / `ChangeMe123!`
- `quality01` / `ChangeMe123!`
- `finance01` / `ChangeMe123!`
- `manager01` / `ChangeMe123!`
- `chairman01` / `ChangeMe123!`
- `sysadmin01` / `ChangeMe123!`

### 超级角色账号（手动创建，非 seed）

- `inventory_super01` / `ChangeMe123!`（库存超级：全仓库）
- `quality_super01` / `ChangeMe123!`（质检超级：全仓库质检）
- `finance_super01` / `ChangeMe123!`（财务超级：可接管任意付款）

## 运行测试

测试使用独立测试库 `yecai_test`（不污染开发库），首次运行自动建表+seed：

```bash
python scripts/run_tests.py          # 全部测试（含并发）
python scripts/run_tests.py --fast   # 跳过并发测试
```

当前覆盖：登录/角色/主数据冒烟、库存接口回归、并发扣库存防超卖。

## 当前状态

- 路由层核心写操作已拆分到领域服务层
- 认证已切换为 Cookie 模式
- 首页公共业务状态已抽到 `dashboard` store
- 多个业务页的 `focusId` 聚焦逻辑已抽成复用 composable
- 路由、菜单、预热配置已统一到共享清单
- 大文件已拆包：`inventory.py`→`api/routers/inventory/`、`sales_service.py`→`services/sales_service/`、`purchase_service.py`→`services/purchase_service/`（URL 与导入路径零变化）
- 库存扣减已加行级锁（`SELECT ... FOR UPDATE`）防并发超卖
- 顶栏通知中心下拉面板 + 角色化工作台首页
- 通用 Excel 导出接口 `/api/export/excel`

## 验证情况

- 后端代码已通过：

```bash
python -m compileall api services schemas main.py config.py dependencies.py security.py seed.py models.py
```

- 前端代码已通过：

```bash
cd frontend && npm run build
```

- 自动化测试：

```bash
python scripts/run_tests.py
```

## 建议阅读顺序

如果你是第一次接手这个项目，建议优先阅读：

- `项目审查与快速上手指南.md`
- `main.py`
- `models.py`
- `api/routers/`
- `services/`
- `frontend/src/router/`
- `frontend/src/stores/`
