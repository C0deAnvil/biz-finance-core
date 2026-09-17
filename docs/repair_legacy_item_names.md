# 产品、原料简称补齐为全称

旧数据 `02_ptype.json` 同时含有 `pname`（简称）和 `pfullname`（全称）。
普通导入脚本现在优先使用全称，全称为空时回退到简称，最多保留 200 字。
已有数据不需要整库重导，也不需要 Alembic 迁移。

## 修复已有名称

在项目根目录、已激活 Python 虚拟环境后运行。以下 `old_data/export` 是本次
尿素产品的原始来源；另一套 `export_shiji2020` 不可替代此目录。

先只读预览：

```bash
python scripts/repair_legacy_item_names.py --source old_data/export
```

核对预览结果并备份数据库后，仅提交名称修复：

```bash
python scripts/repair_legacy_item_names.py --source old_data/export --apply
```

连接使用项目 `DATABASE_URL` 等既有配置。执行前确认指向需要修复的数据库。
脚本按编码匹配，只修复当前名称仍等于原简称的档案；全称为空、已是全称、
人工改名、多个来源名称冲突、产品/原料跨表编码重复、超长全称均不强制覆盖。
不创建档案，不修改价格、库存、包装、BOM、客户或订单；保留原 ID 和关联关系。
正式执行使用事务和原名称条件检查，发现目标变化即报错回滚。重复执行不会重复修改。

此模式与全量导入独立。不要为补齐名称重新执行整套导入，也不要使用清库脚本。
普通导入的 `--skip-conflicts` 仍会保留已有档案，不会替代本名称修复功能。
