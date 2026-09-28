"""Apply the money migration to a disposable SQLite copy of the old column types."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory

from alembic import command
from alembic.config import Config
from sqlalchemy import Numeric, create_engine, inspect

from models import Base


ROOT = Path(__file__).resolve().parent.parent
with TemporaryDirectory(prefix="yecai-money-migration-") as folder:
    database_path = Path(folder) / "money.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    os.environ["DATABASE_URL"] = database_url
    engine = create_engine(database_url)
    old_columns = []
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, Numeric) and column.type.scale == 4 and column.name in {
                "price", "unit_price", "amount", "total_amount", "deposit_amount", "transport_fee",
                "packaging_fee", "cost_amount", "profit_amount", "packaging_unit_price",
                "voucher_amount", "refund_amount", "requested_amount", "order_amount",
                "other_fee_amount", "actual_amount", "invoice_amount", "estimated_amount",
                "recognized_amount", "planned_amount", "received_amount", "delivery_fee",
                "old_price", "new_price", "standard_price", "actual_price",
            }:
                old_columns.append((table.name, column.name, column.type.precision))
                column.type = Numeric(column.type.precision - 2, 2)
    Base.metadata.create_all(engine, tables=[table for table in Base.metadata.tables.values() if table.name != "purchase_requests"])
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    command.stamp(config, "fd4e5f6a7b8c")
    command.upgrade(config, "head")
    inspector = inspect(engine)
    for table_name, column_name, precision in old_columns:
        column = next(row for row in inspector.get_columns(table_name) if row["name"] == column_name)
        assert column["type"].scale == 4, f"{table_name}.{column_name} is still scale 2"
        assert column["type"].precision == precision
    assert inspector.has_table("purchase_requests")
    engine.dispose()
    print(f"money migration passed for {len(old_columns)} columns")
