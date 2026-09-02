"""pytest 全局夹具:使用独立测试库 yecai_test,避免污染开发库。

用法:直接运行 `venv/Scripts/python.exe -m pytest`。
测试库通过 DATABASE_URL 环境变量切换,启动时自动跑迁移+seed(与生产一致)。
"""

import os
import sys
from pathlib import Path

# 必须在导入 app 之前设置 DATABASE_URL → 测试库
TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "mysql+pymysql://yecai_user:CPHWcphw11223344@127.0.0.1:3306/yecai_test?charset=utf8mb4",
)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
# 测试环境关闭启动迁移(conftest 显式控制),避免与 pytest 会话冲突
os.environ["RUN_MIGRATIONS_ON_STARTUP"] = "false"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from database import SessionLocal, engine  # noqa: E402
from main import app  # noqa: E402
from seed import init_seed_data  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _prepare_test_db():
    """会话级:建表 + seed,只执行一次。

    注意:不走 alembic upgrade——bootstrap 迁移用 Base.metadata.create_all
    (按当前模型建表)建全表,后续 add_column 迁移在全新库上必然重复加列冲突。
    因此测试库直接 create_all 建当前模型,再 stamp head 对齐版本号,
    与生产最终态 schema 一致。
    """
    from alembic import command
    from alembic.config import Config

    from models import Base

    Base.metadata.create_all(bind=engine)

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.stamp(cfg, "head")

    db = SessionLocal()
    try:
        init_seed_data(db, os.getenv("DEFAULT_USER_PASSWORD", "ChangeMe123!"))
        _ensure_super_users(db)
        db.commit()
    finally:
        db.close()
    yield


def _ensure_super_users(db):
    """seed 不含超级角色/用户(生产环境手动插入),测试库补齐。"""
    from models import Role, User
    from security import hash_password
    from sqlalchemy import select

    super_map = {
        "inventory_super01": "inventory_super",
        "quality_super01": "quality_super",
        "finance_super01": "finance_super",
    }
    role_map = {r.code: r for r in db.execute(select(Role)).scalars()}
    # 补齐超级角色(复制基础角色导航)
    base_role_by_super = {"inventory_super": "inventory", "quality_super": "quality", "finance_super": "finance"}
    for super_code, base_code in base_role_by_super.items():
        if super_code in role_map:
            continue
        base_role = role_map.get(base_code)
        db.add(
            Role(
                code=super_code,
                name={"inventory_super": "库存超级", "quality_super": "质检超级", "finance_super": "财务超级"}[super_code],
                description=f"{base_role.name}超级权限(全仓库/全单据)" if base_role else "",
                home_route=base_role.home_route if base_role else "/",
                navigation_json=base_role.navigation_json if base_role else [],
                scope_json=base_role.scope_json if base_role else [],
                is_admin=base_role.is_admin if base_role else False,
            )
        )
    db.flush()
    role_map = {r.code: r for r in db.execute(select(Role)).scalars()}
    users = {u.username: u for u in db.execute(select(User)).scalars()}
    for username, role_code in super_map.items():
        if username in users:
            continue
        db.add(
            User(
                username=username,
                full_name=username,
                hashed_password=hash_password("ChangeMe123!"),
                role_id=role_map[role_code].id if role_code in role_map else None,
                department_id=None,
                is_active=True,
                data_scope="all",
            )
        )
    db.flush()


@pytest.fixture()
def client(_prepare_test_db):
    """每个测试一个独立 TestClient(不触发 lifespan 迁移)。"""
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def login(client):
    """返回一个登录辅助函数:login(username) -> dict(cookies)。"""

    def _login(username: str, password: str = "ChangeMe123!"):
        resp = client.post("/api/auth/login", json={"username": username, "password": password})
        assert resp.status_code == 200, f"登录失败 {username}: {resp.text}"
        return resp

    return _login


@pytest.fixture()
def auth_client(client, login):
    """返回已登录的 client + 当前用户信息。"""

    def _make(username: str):
        login(username)
        me = client.get("/api/auth/me")
        assert me.status_code == 200
        return client, me.json().get("data") or {}

    return _make