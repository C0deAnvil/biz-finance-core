/* =====================================================================
   旧系统（SQL Server · 管家婆 niaosu2020）主数据导出
   把 .bak 还原成数据库后执行本脚本，导出新系统需要的主数据。

   用法二选一：
   A. 命令行（推荐）：跑同目录的 export_from_sqlserver.bat，自动生成 8 个 json 文件
   B. SSMS：逐段执行，每段结果「另存为」JSON 文件，文件名见每段注释

   所有查询都是只读 SELECT，不会改动旧库。
   ===================================================================== */

USE [niaosu2020];
GO

/* ---------------------------------------------------------------------
   0. 诊断：往来单位的顶层分类
   文件名：00_btype_roots.json
   用途：旧系统 btype 把客户和供应商放在同一棵树里，靠顶层节点区分。
        先看清楚根节点叫什么，导入脚本据此归类。
   --------------------------------------------------------------------- */
SELECT btypeid, parid, leveal, busercode, bname, bfullname, soncount
  FROM dbo.btype
 WHERE ISNULL(deleted, 0) = 0
   AND (leveal <= 2 OR ISNULL(parid, '') = '')
 ORDER BY leveal, ROWINDEX
 FOR JSON PATH;
GO

/* ---------------------------------------------------------------------
   1. 往来单位全量（客户 + 供应商，含分类节点）
   文件名：01_btype.json
   bfullname 是含分类路径的全名，导入脚本靠它判断归属客户还是供应商
   --------------------------------------------------------------------- */
SELECT btypeid, parid, leveal, soncount, busercode,
       bname, bfullname, bnamepy,
       Area, AreaTypeID, TelAndAddress, PostCode,
       Person, linker, linkerTel, moPhone, Fax, EMail,
       TaxNumber, BankAndAcount, BankAccounts,
       bcomment, isStop, DefInput, GatherBtypeId,
       R_WARNUP, P_WARNUP, ChangeDay, ChangeRate
  FROM dbo.btype
 WHERE ISNULL(deleted, 0) = 0
 ORDER BY leveal, ROWINDEX
 FOR JSON PATH;
GO

/* ---------------------------------------------------------------------
   2. 商品
   文件名：02_ptype.json
   --------------------------------------------------------------------- */
SELECT p.ptypeid, p.ParId, p.leveal, p.soncount, p.pusercode,
       p.pname, p.pfullname, p.pnamepy,
       p.Standard, p.Type, p.Area, p.punitname, p.SideUnit,
       p.pcomment, p.isStop, p.warnup, p.warndown,
       p.LastBuyPrice, p.Ptypetype,
       p.Custom1, p.Custom2, p.Custom3, p.Custom4,
       p.sourcewayzz, p.sourcewaywg, p.sourcewayww
  FROM dbo.ptype p
 WHERE ISNULL(p.deleted, 0) = 0
 ORDER BY p.leveal, p.RowIndex
 FOR JSON PATH;
GO

/* ---------------------------------------------------------------------
   3. 商品售价（新系统 Product.price / Material.price 必填）
   文件名：03_ptype_price.json
   一个商品可能有多个价格类型，全部导出，导入脚本取最新的一条
   --------------------------------------------------------------------- */
SELECT PTypeId, UnitID, PRTypeId, Price, UpDateDt
  FROM dbo.xw_P_PtypePrice
 WHERE ISNULL(Price, 0) > 0
 ORDER BY PTypeId, UpDateDt DESC
 FOR JSON PATH;
GO

/* ---------------------------------------------------------------------
   4. 职员
   文件名：04_employee.json
   Incumbency / isStop 用于判断在职状态；enamepy 是拼音缩写，仅作参考
   --------------------------------------------------------------------- */
SELECT etypeid, Parid, leveal, soncount, eusercode,
       ename, efullname, enamepy,
       Department, duty, Tel, Address, EMail, Birthday,
       ecomment, Incumbency, isStop
  FROM dbo.employee
 WHERE ISNULL(deleted, 0) = 0
 ORDER BY leveal, ROWINDEX
 FOR JSON PATH;
GO

/* ---------------------------------------------------------------------
   5. 部门
   文件名：05_department.json
   --------------------------------------------------------------------- */
SELECT dtypeid, parid, leveal, soncount, dusercode,
       dname, dfullname, dnamepy, dcomment, isStop
  FROM dbo.Department
 WHERE ISNULL(deleted, 0) = 0
 ORDER BY leveal, ROWINDEX
 FOR JSON PATH;
GO

/* ---------------------------------------------------------------------
   6. 仓库
   文件名：06_stock.json
   --------------------------------------------------------------------- */
SELECT ktypeid, parid, leveal, soncount, kusercode,
       kname, kfullname, knamepy,
       Address, Person, Tel, kcomment, isStop
  FROM dbo.Stock
 WHERE ISNULL(deleted, 0) = 0
 ORDER BY leveal, ROWINDEX
 FOR JSON PATH;
GO

/* ---------------------------------------------------------------------
   7. 客户 → 销售负责人（按最近一单的业务员）
   文件名：07_customer_owner.json

   旧系统 btype 表没有业务员字段，负责人关系只存在于单据里。
   这里按「该往来单位最近一张带业务员的单据」取 etypeid。
   同时带出单据张数和最近单据日期，方便核对。
   --------------------------------------------------------------------- */
WITH doc AS (
    SELECT btypeid, etypeid, [Date], Vchcode, VchType,
           ROW_NUMBER() OVER (PARTITION BY btypeid ORDER BY [Date] DESC, Vchcode DESC) AS rn,
           COUNT(*)    OVER (PARTITION BY btypeid) AS doc_count
      FROM dbo.Dlyndx
     WHERE ISNULL(btypeid, '') <> ''
       AND ISNULL(etypeid, '') <> ''
       AND ISNULL(draft, 0) = 0
)
SELECT btypeid, etypeid, [Date] AS last_doc_date, VchType AS last_vch_type, doc_count
  FROM doc
 WHERE rn = 1
 ORDER BY btypeid
 FOR JSON PATH;
GO

/* ---------------------------------------------------------------------
   8. 诊断：往来单位在各类单据上的出现次数
   文件名：08_btype_doc_types.json
   用途：客户一般出现在销售类单据、供应商出现在采购类单据。
        万一按分类树判断不准，用这份数据兜底区分。
   --------------------------------------------------------------------- */
SELECT d.btypeid, d.VchType, v.vchname, COUNT(*) AS doc_count
  FROM dbo.Dlyndx d
  LEFT JOIN dbo.Vchtype v ON v.vchtype = d.VchType
 WHERE ISNULL(d.btypeid, '') <> ''
   AND ISNULL(d.draft, 0) = 0
 GROUP BY d.btypeid, d.VchType, v.vchname
 ORDER BY d.btypeid, doc_count DESC
 FOR JSON PATH;
GO

/* ---------------------------------------------------------------------
   9. BOM 表头（成品 + 产出数量）
   文件名：09_bom_ndx.json
   --------------------------------------------------------------------- */
SELECT n.VchCode, n.BOMFullname, n.BomUsercode, n.BMTypeid,
       n.PtypeId, n.Unit, n.Qty, n.Comment,
       n.CreateTime, n.UpdateTime, n.etypeid,
       t.BMfullname AS bom_type_name
  FROM dbo.BOM_Ndx n
  LEFT JOIN dbo.BOM_Type t ON t.BMTypeid = n.BMTypeid
 ORDER BY n.VchCode
 FOR JSON PATH;
GO

/* ---------------------------------------------------------------------
   10. BOM 用料明细
   文件名：10_bom_detail.json
   AttritionRate 是损耗率，新系统 BOMItem 没有对应列，导入时折算进用量并在 notes 说明
   --------------------------------------------------------------------- */
SELECT VchCode, Ptypeid, Unit, Qty, AttritionRate, Dlyorder, Comment
  FROM dbo.BOM_Detail
 ORDER BY VchCode, Dlyorder
 FOR JSON PATH;
GO

/* ---------------------------------------------------------------------
   11. 库存结存（按 商品 × 仓库 汇总）
   文件名：11_goods_stocks.json
   GoodsStocks 是按批次/工号分行的，这里汇总到 商品×仓库，
   成本单价用金额加权平均，对应新系统 Inventory.avg_unit_cost
   --------------------------------------------------------------------- */
SELECT PtypeId,
       KtypeId,
       SUM(Qty)   AS qty,
       SUM(Total) AS total_amount,
       CASE WHEN SUM(Qty) <> 0 THEN SUM(Total) / SUM(Qty) ELSE 0 END AS avg_unit_cost,
       COUNT(*)   AS batch_rows
  FROM dbo.GoodsStocks
 GROUP BY PtypeId, KtypeId
HAVING SUM(Qty) <> 0
 ORDER BY PtypeId, KtypeId
 FOR JSON PATH;
GO

/* ---------------------------------------------------------------------
   12. 库存批次明细（可选，用于建 InventoryBatch）
   文件名：12_goods_stocks_batch.json
   只导有批次号的行；没有批次管理就跳过这一段
   --------------------------------------------------------------------- */
SELECT PtypeId, KtypeId, JobNumber, GoodsBatchID,
       Qty, Price, Total, BuyDate, UsefulEndDate, OutFactoryDate
  FROM dbo.GoodsStocks
 WHERE ISNULL(Qty, 0) <> 0
   AND (ISNULL(JobNumber, '') <> '' OR ISNULL(GoodsBatchID, '') <> '')
 ORDER BY PtypeId, KtypeId
 FOR JSON PATH;
GO
