import json
from functools import lru_cache
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
ROUTE_MANIFEST_PATH = BASE_DIR / "frontend" / "src" / "shared" / "route-manifest.json"


@lru_cache(maxsize=1)
def load_route_manifest() -> list[dict]:
    return json.loads(ROUTE_MANIFEST_PATH.read_text(encoding="utf-8"))


def build_role_menus() -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, dict[str, dict]] = {}
    for item in load_route_manifest():
        group_title = item.get("group") or "业务功能"
        for role_code in item.get("menu_roles", []):
            group_bucket = grouped.setdefault(role_code, {})
            group_item = group_bucket.setdefault(
                group_title,
                {
                    "title": group_title,
                    "path": f"/__group__/{group_title}",
                    "children": [],
                },
            )
            group_item["children"].append({"title": item["title"], "path": item["path"]})
    return {role_code: list(groups.values()) for role_code, groups in grouped.items()}


@lru_cache(maxsize=1)
def manageable_route_items() -> list[dict]:
    return [
        {
            "path": item["path"],
            "title": item["title"],
            "group": item.get("group") or "业务功能",
            "menu_roles": item.get("menu_roles", []),
        }
        for item in load_route_manifest()
        if item.get("path") not in {"/dashboard", "/approvals"}
    ]


def flatten_menu_paths(navigation: list[dict] | None) -> set[str]:
    result: set[str] = set()
    for item in navigation or []:
        children = item.get("children") or []
        if children:
            result.update(flatten_menu_paths(children))
        elif item.get("path"):
            result.add(item["path"])
    return result


def filter_navigation_by_paths(navigation: list[dict] | None, allowed_paths: set[str]) -> list[dict]:
    filtered: list[dict] = []
    for item in navigation or []:
        children = item.get("children") or []
        if children:
            child_items = filter_navigation_by_paths(children, allowed_paths)
            if child_items:
                filtered.append({**item, "children": child_items})
        elif item.get("path") in allowed_paths:
            filtered.append(item)
    return filtered


def merge_navigation_items(existing_navigation: list[dict] | None, target_navigation: list[dict] | None) -> list[dict]:
    existing_map: dict[str, dict] = {item.get("path"): item for item in (existing_navigation or []) if item.get("path")}
    merged: list[dict] = []
    for target in target_navigation or []:
        current = existing_map.get(target.get("path"))
        children = target.get("children") or []
        if children:
            merged.append(
                {
                    **target,
                    "children": merge_navigation_items((current or {}).get("children") or [], children),
                }
            )
        else:
            merged.append(current or target)
    return merged
