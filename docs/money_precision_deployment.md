# 金额四位精度上线说明

本次将全部原 `DECIMAL(...,2)` 的 45 个金额/单价列扩展为四位小数（`DECIMAL(20,4)`；产品与原料标准价为 `DECIMAL(12,4)`）。销售、采购明细采用 `Decimal` 运算，并在写入金额时明确按四位小数、四舍五入处理；输入超过四位小数会被校验拒绝。金额页面至少显示三位小数，最多显示数据库保存的四位。

## 上线顺序

1. 先备份数据库，并尽量选业务低峰期。MySQL 修改金额列会持有表结构锁；不要在仍有旧后端写入两位精度时单独运行迁移。
2. 部署完整后端和前端代码，执行 `alembic upgrade head`（还会创建生产/质检自主采购需求表），然后重启后端、发布前端构建产物。
3. 用测试订单核对：单价 `1.2345`、数量 `2`，销售/采购明细和订单金额应均为 `2.4690`；再核对应收、应付页面与销售“我的应收款”。

迁移只扩大字段，不会对旧金额重新计算。**已经在旧数据库中被四舍五入成两位小数的数据，无法凭现有金额恢复第三、四位**；如需修正旧单，应依据原始凭证逐笔核对，不能批量猜测补位。

本地回归命令：

```text
set PYTHONPATH=.
venv\Scripts\python.exe scripts/check_money_precision.py
venv\Scripts\python.exe scripts/check_money_migration.py
venv\Scripts\python.exe scripts/check_independent_purchase_requests.py
```
