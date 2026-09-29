from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from api.routers.auth import build_user_profile
from models import Base, Role, User, UserModulePermission
from services.navigation import flatten_menu_paths


def test_production_inventory_status_is_visible_with_module_permission():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        role = Role(code="production", name="生产", home_route="/dashboard", navigation_json=[], scope_json=[])
        db.add(role)
        db.flush()
        user = User(username="production_status_test", full_name="生产测试", hashed_password="x", role_id=role.id)
        db.add(user)
        db.flush()
        db.add(UserModulePermission(user_id=user.id, route_path="/inventory/stocks", assigned_by_id=user.id))
        db.flush()

        profile = build_user_profile(user, db)
        assert "/inventory/stocks" in flatten_menu_paths(profile.navigation)
