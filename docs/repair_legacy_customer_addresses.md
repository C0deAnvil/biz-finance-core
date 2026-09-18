# 修复旧客户地址误存电话

旧 SQL Server 导出中，`Area` 存放详细地址，`TelAndAddress` 实际为电话。
导出脚本已经同时导出这两个字段，不需重新导出。客户页面和接口正常读取
`customers.address`；错误来自原导入脚本将 `TelAndAddress` 优先写入地址。
现已修正客户、供应商的后续导入映射为 `address = Area`，原地址为空则留空。

## 已有客户资料

不重导整库，不修改订单的历史收货地址。专用脚本仅更新客户档案地址，
联系电话、负责人、标签等字段保持不变。默认只读预览：

```bash
python scripts/repair_legacy_customer_addresses.py --source old_data/export --report customer-address-preview.json
```

脚本打印目标库名称，并保存旧值、新值和跳过原因供核对。
报告含客户资料，应保留在受控目录，不提交到公开仓库。
确认源数据、目标数据库和报告，备份数据库后正式执行：

```bash
python scripts/repair_legacy_customer_addresses.py --source old_data/export --report customer-address-repair.json --apply
```

执行条件：编码唯一、客户名称匹配、当前地址为电话号码且仍等于原导入的
`TelAndAddress`，并且 `Area` 有可用地址、长度不超过 255 字。
不满足条件的客户会跳过，防止覆盖人工修正或将缺失地址猜成其他内容。
执行时再次比较原地址和身份，发现变化即整批回滚；重复执行不会重复修复。

修复服务器数据时，需要在服务器项目的 Python 环境中运行，并确认配置连接服务器业务库。
服务器还需有对应来源的 `01_btype.json`，不能混用另一套旧库的导出目录。
本地预览不能证明服务器有相同数量的待修复记录，应在服务器重新预览。
无需 Alembic 迁移；只部署代码不会自动修改已有客户地址。
