# 从 shiji2020 追加导入旧系统数据

项目实际路径是 `E:\System_dev\yecai-new`。继续使用原来的字段映射和分类逻辑，源数据库改为 SQL Server 的 `shiji2020`。目标仍是当前系统配置的数据库。

本次涉及的脚本：

- `old_data/export_data_v3.ps1`：已有 `-Database` 参数，新增 `-OutputDir` 参数，避免覆盖昨天的导出文件。导出失败返回非零退出码。
- `scripts/import_legacy_master_data.py`：新增 `--skip-conflicts`，只新增非冲突记录。原来不带该参数的更新模式仍然保留。

## 1. 准备与备份

先备份**新系统的目标数据库**，保存导入前的完整备份。在数据库管理工具中，选择与项目 `.env` 中 `DATABASE_URL` 对应的数据库，执行导出 SQL/转储数据库，包含表结构和全部数据，并确认任务成功。不要把目标数据库配置改成 `shiji2020`：它只是导出脚本读取的旧系统数据库。

导入期间暂停业务录入，避免其他程序同时修改相同档案、BOM 或库存。

打开 PowerShell，进入项目目录：

```powershell
Set-Location 'E:\System_dev\yecai-new'
$env:PYTHONIOENCODING = 'utf-8'
```

后面的命令都在这个目录执行。直接使用项目虚拟环境里的 Python，无需激活环境或重新安装依赖。

## 2. 导出 shiji2020

使用 Windows 身份验证：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\old_data\export_data_v3.ps1 -Server 'DESKTOP-4AI9ATV\MSSQLSERVER01' -Database 'shiji2020' -OutputDir 'E:\System_dev\yecai-new\old_data\export_shiji2020'
if ($LASTEXITCODE -ne 0) { throw '导出失败，请先处理报错，不要继续导入。' }
```

如果昨天使用的是 SQL Server 的 `sa` 登录，则改用以下命令（以上两种方式只选一种）：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\old_data\export_data_v3.ps1 -Server 'DESKTOP-4AI9ATV\MSSQLSERVER01' -Database 'shiji2020' -OutputDir 'E:\System_dev\yecai-new\old_data\export_shiji2020' -UseSa
if ($LASTEXITCODE -ne 0) { throw '导出失败，请先处理报错，不要继续导入。' }
```

密码在提示时输入。服务器名称来自现有脚本；如果数据库位于其他实例，把 `-Server` 后面的值换成实际实例名。

确认屏幕上 `Database` 为 `shiji2020`，`Output` 为 `export_shiji2020`，结果中所有文件为 `OK` 或 `EMPTY`。`EMPTY` 表示对应查询没有数据，应核对是否符合预期；`FAILED` 或 `BADSHAPE` 必须处理后完整重导。

昨天的 `old_data/export` 不受影响。不要将昨天的 JSON 或复核 CSV 复制到新目录；两个旧库的内部 ID 可能相同但含义不同。该目录本次只用于 `shiji2020`。

## 3. 试运行

```powershell
.\venv\Scripts\python.exe .\scripts\import_legacy_master_data.py --source old_data/export_shiji2020 --skip-conflicts
if ($LASTEXITCODE -ne 0) { throw '试运行失败，请先处理报错。' }
```

不带 `--apply` 不会提交数据库变更，但会在新导出目录生成以下复核表：

- `employee_role_map.csv`：核对员工的 `role_code`，确认后将 `confirmed` 填为 `Y`。角色沿用系统现有角色，如 `sales`、`purchase`、`inventory`、`production`、`quality`、`finance`、`manager`。无法识别的角色标为 `__unresolved__`，对应新账号默认停用。
- `product_material_split.csv`：核对 `kind`，成品填 `product`，原料填 `material`，不导入填 `skip`；确认后将 `confirmed` 填为 `Y`。

用表格软件编辑时，将 ID 列保持为文本，避免丢失前导零；保存为 UTF-8 CSV。`confirmed` 是复核标记，脚本不会强制所有行均已确认，请实际核对角色和分类。

复核后重新执行上面的试运行命令，确认目标库、新增数量、冲突跳过数量和告警。预览使用内存比较检测待新增记录，正式导入使用目标库实际排序规则，统计可能略有差异。

## 4. 正式导入

确认备份、数据来源及复核结果后执行：

```powershell
.\venv\Scripts\python.exe .\scripts\import_legacy_master_data.py --source old_data/export_shiji2020 --skip-conflicts --apply
if ($LASTEXITCODE -ne 0) { throw '导入失败，请保留报错信息并核查；本次数据库事务已回滚。' }
```

看到“已提交，数据已写入新系统”才表示本次正式导入完成。不要遗漏 `--skip-conflicts`，否则会使用原有的更新模式。

如果此次只想追加基础档案，暂不导入 BOM 和库存，将正式命令换成：

```powershell
.\venv\Scripts\python.exe .\scripts\import_legacy_master_data.py --source old_data/export_shiji2020 --skip-conflicts --skip bom,stock --apply
```

对应试运行也加上 `--skip bom,stock`。不要跳过 `product` 或 `warehouse` 阶段后又导入库存，因为库存需要这些阶段生成关联映射。

## 5. 冲突规则与范围

| 数据 | 跳过条件 |
| --- | --- |
| 部门、仓库 | 编码或名称已存在 |
| 员工 | 根据姓名生成的用户名已存在；保留原密码、角色、部门、启停状态 |
| 产品、原料 | 各自表中的编码已存在；不覆盖名称、价格和启停状态 |
| 客户、供应商 | 各自表中的编码、名称或格式有效的统一社会信用代码任一已存在；整条跳过，保留负责人、联系方式、备注、标签等 |
| BOM | 产品已有 BOM，无论旧库版本号是否相同，都跳过；用料关联缺失则整份跳过 |
| 库存 | 同仓库、同产品或原料的结存已存在；不覆盖或累加数量、成本、预留数量 |
| 库存批次 | 结存冲突时相关批次一起跳过；同仓库、同货品、同批次号也跳过 |

同一批导入文件内部的上述重复也会检测；先出现的非冲突记录保留。客户/供应商同名但编码不同，也会跳过，不再自动改名导入。因此同名但实际不同的单位需要后续人工核对。

对需用于关联的冲突档案，只有身份字段一致才复用：部门/仓库的编码和名称、员工的用户名和姓名、产品/原料的编码及名称/单位/规格。身份不一致则不建立关联并输出告警；相关库存、BOM 会跳过，新增客户找不到业务员时负责人留空。

这是**追加非冲突数据**，不会把两套旧库相同货品的库存合计，也不会自动合并两个单位。它只沿用已有主数据、BOM、库存导入范围；没有新增历史销售单、采购单、财务单据的迁移功能。

文件缺失、空文件、无效 JSON 会在跳过模式下阻止导入。已识别的重复会跳过；连接失败、字段错误、外键错误以及并发写入造成的未预先识别的唯一键冲突仍会报错并回滚本次事务，不会吞掉所有错误。

## 6. 导入后核对

在系统中抽查新增客户、供应商、员工、产品/原料；如导入了 BOM、库存，再核对配方及数量。确认昨天已有记录未被覆盖。

可再次执行不带 `--apply` 的试运行，查看重复记录的跳过结果。保存新目录下的复核表，后续重复执行同样命令时继续使用。

代码已通过内存 SQLite 的隔离回归检查，未连接 SQL Server 导出或对实际业务数据库执行正式导入。MySQL 实际排序规则及真实数据仍以本机试运行、正式导入结果为准。
