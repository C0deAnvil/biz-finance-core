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
