import pytest

from database import SessionLocal
from helpers import uniq
from models import Material, PackagingConfig, Product


@pytest.mark.parametrize("endpoint,model,code_field", [
    ("products", Product, "product_code"),
    ("materials", Material, "material_code"),
])
def test_item_search_filters_before_pagination(client, login, endpoint, model, code_field):
    token = uniq("SEARCH")
    with SessionLocal() as db:
        db.add_all([
            model(**{code_field: f"{token}-{index}"}, name=f"完整名称搜索{token}-{index}",
                  specification=f"规格{token}%" if index == 0 else "普通规格")
            for index in range(12)
        ])
        db.commit()
    login("inventory01")
    url = f"/api/master-data/{endpoint}"
    response = client.get(url, params={"keyword": f"  {token}  ", "page": 2, "page_size": 10})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["pagination"]["total"] == 12
    assert len(data["items"]) == 2
    assert all(item["name"].startswith("完整名称搜索") for item in data["items"])
    for term in (f"{token}-11", f"规格{token}%"):
        response = client.get(url, params={"keyword": term, "page": 1})
        assert response.status_code == 200, response.text
        assert response.json()["data"]["pagination"]["total"] == 1
    # SQL LIKE 的通配符按用户输入的字面内容匹配。
    response = client.get(url, params={"keyword": f"{token}_", "page": 1})
    assert response.json()["data"]["items"] == []
    assert response.json()["data"]["pagination"]["total"] == 0
    response = client.get(url, params={"keyword": token})
    assert len(response.json()["data"]) == 12
    response = client.get(url, params={"keyword": "  ", "page": 1})
    assert response.json()["data"]["pagination"]["total"] >= 12


def test_packaging_search_preserves_entity_and_active_filters(client, login):
    token = uniq("PACKSEARCH")
    with SessionLocal() as db:
        db.add_all([
            PackagingConfig(entity_type=entity, entity_id=0, packaging_name=f"{token}-{index}%袋",
                            kg_per_bag=25, is_active=active)
            for index, (entity, active) in enumerate([
                ("global", True), ("global", False), ("product", True)
            ])
        ])
        db.commit()
    login("inventory01")
    url = "/api/master-data/packaging-configs"
    params = {"entity_type": "global", "keyword": f"  {token}  "}
    response = client.get(url, params=params)
    assert response.status_code == 200, response.text
    assert len(response.json()["data"]) == 2
    response = client.get(url, params={**params, "active_only": True})
    assert len(response.json()["data"]) == 1
    response = client.get(url, params={**params, "keyword": f"{token}_"})
    assert response.json()["data"] == []
