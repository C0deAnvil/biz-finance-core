import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
import sqlalchemy as sa


def test_accounts_migration_preserves_history_and_imports_supplier_bank():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/c5d6e7f8a9b0_financial_accounts_trade_default.py"
    spec = importlib.util.spec_from_file_location("accounts_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE customers (id INTEGER PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE suppliers (id INTEGER PRIMARY KEY, name VARCHAR(150), bank_name VARCHAR(150), bank_account VARCHAR(60))")
        connection.exec_driver_sql("INSERT INTO suppliers VALUES (1, '测试供应商', '测试银行', '000123')")
        connection.exec_driver_sql("CREATE TABLE products (id INTEGER PRIMARY KEY, fulfillment_mode VARCHAR(30) NOT NULL DEFAULT 'production')")
        connection.exec_driver_sql("INSERT INTO products (id) VALUES (1)")
        for table in migration.SETTLEMENT_TABLES:
            connection.exec_driver_sql(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
            connection.exec_driver_sql(f"INSERT INTO {table} VALUES (1)")
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
        assert connection.scalar(sa.text("SELECT COUNT(*) FROM financial_accounts")) == 1
        assert connection.scalar(sa.text("SELECT account_number FROM financial_accounts")) == "000123"
        for table in migration.SETTLEMENT_TABLES:
            assert connection.execute(sa.text(f"SELECT id, company_account_id, counterparty_account_id FROM {table}")).one() == (1, None, None)
        assert connection.scalar(sa.text("SELECT fulfillment_mode FROM products WHERE id = 1")) == "production"
        connection.exec_driver_sql("INSERT INTO products (id) VALUES (2)")
        assert connection.scalar(sa.text("SELECT fulfillment_mode FROM products WHERE id = 2")) == "trade"
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        assert "financial_accounts" not in sa.inspect(connection).get_table_names()
        assert connection.scalar(sa.text("SELECT COUNT(*) FROM sales_receipt_requests")) == 1
