$server = "DESKTOP-4AI9ATV\MSSQLSERVER01"
$db = "niaosu2020"
$pwd = Read-Host "请输入sa密码"
$outDir = Join-Path $PWD.Path "export"
if(-not (Test-Path $outDir)){ New-Item -ItemType Directory -Path $outDir | Out-Null }

$queries = @(
    @{name="00_btype_roots";sql="SELECT btypeid,parid,leveal,busercode,bname,bfullname,soncount FROM dbo.btype WHERE ISNULL(deleted,0)=0 AND (leveal<=2 OR ISNULL(parid,'')='') ORDER BY leveal,ROWINDEX FOR JSON PATH"},
    @{name="01_btype";sql="SELECT btypeid,parid,leveal,soncount,busercode,bname,bfullname,bnamepy,Area,AreaTypeID,TelAndAddress,PostCode,Person,linker,linkerTel,moPhone,Fax,EMail,TaxNumber,BankAndAcount,BankAccounts,bcomment,isStop,DefInput,GatherBtypeId,R_WARNUP,P_WARNUP,ChangeDay,ChangeRate FROM dbo.btype WHERE ISNULL(deleted,0)=0 ORDER BY leveal,ROWINDEX FOR JSON PATH"},
    @{name="02_ptype";sql="SELECT ptypeid,ParId,leveal,soncount,pusercode,pname,pfullname,pnamepy,Standard,Type,Area,punitname,SideUnit,pcomment,isStop,warnup,warndown,LastBuyPrice,Ptypetype,Custom1,Custom2,Custom3,Custom4,sourcewayzz,sourcewaywg,sourcewayww FROM dbo.ptype WHERE ISNULL(deleted,0)=0 ORDER BY leveal,RowIndex FOR JSON PATH"},
    @{name="03_ptype_price";sql="SELECT PTypeId,UnitID,PRTypeId,Price,UpDateDt FROM dbo.xw_P_PtypePrice WHERE ISNULL(Price,0)>0 ORDER BY PTypeId,UpDateDt DESC FOR JSON PATH"},
    @{name="04_employee";sql="SELECT etypeid,Parid,leveal,soncount,eusercode,ename,efullname,enamepy,Department,duty,Tel,Address,EMail,Birthday,ecomment,Incumbency,isStop FROM dbo.employee WHERE ISNULL(deleted,0)=0 ORDER BY leveal,ROWINDEX FOR JSON PATH"},
    @{name="05_department";sql="SELECT dtypeid,parid,leveal,soncount,dusercode,dname,dfullname,dnamepy,dcomment,isStop FROM dbo.Department WHERE ISNULL(deleted,0)=0 ORDER BY leveal,ROWINDEX FOR JSON PATH"},
    @{name="06_stock";sql="SELECT ktypeid,parid,leveal,soncount,kusercode,kname,kfullname,knamepy,Address,Person,Tel,kcomment,isStop FROM dbo.Stock WHERE ISNULL(deleted,0)=0 ORDER BY leveal,ROWINDEX FOR JSON PATH"},
    @{name="07_customer_owner";sql="WITH doc AS (SELECT btypeid,etypeid,[Date],Vchcode,VchType,ROW_NUMBER() OVER (PARTITION BY btypeid ORDER BY [Date] DESC,Vchcode DESC) rn,COUNT(*) OVER (PARTITION BY btypeid) doc_count FROM dbo.Dlyndx WHERE ISNULL(btypeid,'')<>'' AND ISNULL(etypeid,'')<>'' AND ISNULL(draft,0)=0) SELECT btypeid,etypeid,[Date] AS last_doc_date,VchType AS last_vch_type,doc_count FROM doc WHERE rn=1 ORDER BY btypeid FOR JSON PATH"},
    @{name="08_btype_doc_types";sql="SELECT d.btypeid,d.VchType,v.vchname,COUNT(*) AS doc_count FROM dbo.Dlyndx d LEFT JOIN dbo.Vchtype v ON v.vchtype=d.VchType WHERE ISNULL(d.btypeid,'')<>'' AND ISNULL(d.draft,0)=0 GROUP BY d.btypeid,d.VchType,v.vchname ORDER BY d.btypeid,doc_count DESC FOR JSON PATH"},
    @{name="09_bom_ndx";sql="SELECT n.VchCode,n.BOMFullname,n.BomUsercode,n.BMTypeid,n.PtypeId,n.Unit,n.Qty,n.Comment,n.CreateTime,n.UpdateTime,n.etypeid,t.BMfullname AS bom_type_name FROM dbo.BOM_Ndx n LEFT JOIN dbo.BOM_Type t ON t.BMTypeid=n.BMTypeid ORDER BY n.VchCode FOR JSON PATH"},
    @{name="10_bom_detail";sql="SELECT VchCode,Ptypeid,Unit,Qty,AttritionRate,Dlyorder,Comment FROM dbo.BOM_Detail ORDER BY VchCode,Dlyorder FOR JSON PATH"},
    @{name="11_goods_stocks";sql="SELECT PtypeId,KtypeId,SUM(Qty) AS qty,SUM(Total) AS total_amount,CASE WHEN SUM(Qty)<>0 THEN SUM(Total)/SUM(Qty) ELSE 0 END AS avg_unit_cost,COUNT(*) AS batch_rows FROM dbo.GoodsStocks GROUP BY PtypeId,KtypeId HAVING SUM(Qty)<>0 ORDER BY PtypeId,KtypeId FOR JSON PATH"},
    @{name="12_goods_stocks_batch";sql="SELECT PtypeId,KtypeId,JobNumber,GoodsBatchID,Qty,Price,Total,BuyDate,UsefulEndDate,OutFactoryDate FROM dbo.GoodsStocks WHERE ISNULL(Qty,0)<>0 AND (ISNULL(JobNumber,'')<>'' OR ISNULL(GoodsBatchID,'')<>'') ORDER BY PtypeId,KtypeId FOR JSON PATH"}
)

Write-Host "Testing connection..."
sqlcmd -S $server -U sa -P $pwd -C -d $db -Q "SELECT 1" >$null 2>&1
if($LASTEXITCODE -ne 0){
    Write-Host "连接失败！检查账号密码/实例名" -ForegroundColor Red
    Read-Host "按回车退出"
    exit
}
Write-Host "连接成功，开始导出`n"

foreach($item in $queries){
    $outFile = Join-Path $outDir "$($item.name).json"
    Write-Host ">> $($item.name)"
    $tmpSql = Join-Path $env:TEMP "tmp_sql_$(Get-Random).sql"
    ":XML ON`nSET NOCOUNT ON;`n$($item.sql);" | Out-File $tmpSql -Encoding ASCII
    sqlcmd -S $server -U sa -P $pwd -C -d $db -y 0 -f 65001 -i $tmpSql -o $outFile
    Remove-Item $tmpSql
    $f = Get-Item $outFile
    Write-Host "    ---> $($f.Length) bytes"
}
Write-Host "`n==== 全部导出完成 ===="
Get-ChildItem $outDir | Select-Object Name,Length
