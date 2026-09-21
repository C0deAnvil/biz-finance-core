# 供应商、产品、原料简称补齐为全称

旧数据 `01_btype.json` 同时含有供应商 `bname`（简称）和 `bfullname`（全称），
`02_ptype.json` 同时含有商品 `pname`（简称）和 `pfullname`（全称）。普通导入脚本
现在优先使用全称，全称为空时回退到简称。已有数据不需要整库重导，也不需要
Alembic 迁移。

## 修复已有名称

在项目根目录、已激活 Python 虚拟环境后运行。必须使用当初导入该批数据的原始
来源。先只读预览：

```bash
python scripts/repair_legacy_item_names.py --source old_data/export
```

核对预览结果并备份数据库后，仅提交名称修复：

```bash
python scripts/repair_legacy_item_names.py --source old_data/export --apply
```

两套旧账均曾导入时，可同时核对：

```bash
python scripts/repair_legacy_item_names.py \
  --source old_data/export \
  --source old_data/export_shiji2020
```

确认预览后，在同一条命令末尾添加 `--apply`。

如果预览显示产品为 0，但页面仍有疑似简称，可查看编号覆盖情况：

```bash
python scripts/repair_legacy_item_names.py \
  --source old_data/export \
  --source old_data/export_shiji2020 \
  --diagnose
```

诊断会分别统计已经是全称、可自动修复、编号未匹配、当前名称被人工修改及同编号
跨表冲突的数量，不会修改数据库。

连接使用项目 `DATABASE_URL` 等既有配置。执行前确认指向需要修复的数据库。
脚本按编码和当前旧简称共同匹配，只修复仍等于原简称的档案；全称为空、已经是
全称、人工改名、多个来源名称冲突、产品/原料跨表编码重复、超长全称均不强制
覆盖。多个供应商使用同一个全称时，名称会写成“全称（原供应商编码）”以保留每条
档案；加编码后仍冲突的记录才会留给人工核对。不创建档案，不修改价格、库存、
包装、BOM、客户或订单；保留原 ID 和关联关系。
正式执行使用事务和原名称条件检查，发现目标变化即报错回滚。重复执行不会重复修改。

此模式与全量导入独立。不要为补齐名称重新执行整套导入，也不要使用清库脚本。
普通导入的 `--skip-conflicts` 仍会保留已有档案，不会替代本名称修复功能。
