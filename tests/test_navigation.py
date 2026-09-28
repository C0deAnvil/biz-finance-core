"""角色导航菜单不应包含重复路径。"""

from services.navigation import build_role_menus


def _leaf_paths(items: list[dict]) -> list[str]:
    paths: list[str] = []
    for item in items:
        children = item.get("children") or []
        if children:
            paths.extend(_leaf_paths(children))
        elif item.get("path"):
            paths.append(item["path"])
    return paths


def test_role_navigation_has_no_duplicate_paths():
    for role_code, navigation in build_role_menus().items():
        paths = _leaf_paths(navigation)
        assert len(paths) == len(set(paths)), f"{role_code} 菜单存在重复路径: {paths}"


def test_finance_dashboard_and_analytics_appear_once():
    paths = _leaf_paths(build_role_menus()["finance"])
    assert paths.count("/dashboard") == 1
    assert paths.count("/analytics") == 1


def test_sales_receivables_menu_does_not_depend_on_migration_grant(_prepare_test_db):
    from sqlalchemy import delete, select

    from api.routers.auth import build_user_profile
    from database import SessionLocal
    from models import User, UserModulePermission

    # 模拟迁移漏执行或后来新增的销售账号；事务回滚，不改测试库现有授权。
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == "sales01"))
        db.execute(delete(UserModulePermission).where(
            UserModulePermission.user_id == user.id,
            UserModulePermission.route_path == "/sales/my-receivables",
        ))
        profile = build_user_profile(user, db)
        assert "/sales/my-receivables" in _leaf_paths(profile.navigation)
        db.rollback()
