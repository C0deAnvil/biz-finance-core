"""Verify the purchase target-warehouse migration on a disposable SQLite database."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory

from alembic import command
from alembic.config import Config
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, inspect


root = Path(__file__).resolve().parent.parent
with TemporaryDirectory(prefix="yecai-purchase-target-") as folder:
    database_url = f"sqlite:///{(Path(folder) / 'test.db').as_posix()}"
    os.environ["DATABASE_URL"] = database_url
    engine = create_engine(database_url)
    metadata = MetaData()
    Table("warehouses", metadata, Column("id", Integer, primary_key=True))
    Table(
        "purchase_orders", metadata,
        Column("id", Integer, primary_key=True),
        Column("order_no", String(60), nullable=False),
    )
    metadata.create_all(engine)
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.stamp(config, "ff6a7b8c9d0e")
    command.upgrade(config, "head")
    assert "target_warehouse_id" in {column["name"] for column in inspect(engine).get_columns("purchase_orders")}
    engine.dispose()

print("purchase target warehouse migration passed")
