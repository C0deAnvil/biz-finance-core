@echo off
setlocal enabledelayedexpansion
REM ======================================================================
REM  SQL Server FOR JSON export batch
REM ======================================================================
set SQLSERVER=DESKTOP-4AI9ATV\MSSQLSERVER01
set SQLDB=niaosu2020
set SAPWD=@Ali634112
set AUTH=-U sa -P "!SAPWD!" -C

set OUTDIR=%~dp0export
if not exist "%OUTDIR%" mkdir "%OUTDIR%"
echo Server: %SQLSERVER%   Database: %SQLDB%
echo Output folder: %OUTDIR%
echo.
echo Testing connection ...
sqlcmd -S %SQLSERVER% %AUTH% -d %SQLDB% -Q "SELECT 1" >nul 2>&1
if errorlevel 1 (
  echo.
  echo [Connection Failed]
  echo   1. Check SQL instance name
  echo   2. Check SA password
  echo   3. Check trust server certificate (-C)
  pause
  exit /b 1
)
echo Connection OK, start export ...
echo.

call :run 00 btype_roots        "SELECT btypeid,parid,leveal,busercode,bname,bfullname,soncount FROM dbo.btype WHERE ISNULL(deleted,0)=0 AND (leveal<=2 OR ISNULL(parid,'')='') ORDER BY leveal,ROWINDEX FOR JSON PATH"
call :run 01 btype              "SELECT btypeid,parid,leveal,soncount,busercode,bname,bfullname,bnamepy,Area,AreaTypeID,TelAndAddress,PostCode,Person,linker,linkerTel,moPhone,Fax,EMail,TaxNumber,BankAndAcount,BankAccounts,bcomment,isStop,DefInput,GatherBtypeId,R_WARNUP,P_WARNUP,ChangeDay,ChangeRate FROM dbo.btype WHERE ISNULL(deleted,0)=0 ORDER BY leveal,ROWINDEX FOR JSON PATH"
call :run 02 ptype              "SELECT ptypeid,ParId,leveal,soncount,pusercode,pname,pfullname,pnamepy,Standard,Type,Area,punitname,SideUnit,pcomment,isStop,warnup,warndown,LastBuyPrice,Ptypetype,Custom1,Custom2,Custom3,Custom4,sourcewayzz,sourcewaywg,sourcewayww FROM dbo.ptype WHERE ISNULL(deleted,0)=0 ORDER BY leveal,RowIndex FOR JSON PATH"
call :run 03 ptype_price        "SELECT PTypeId,UnitID,PRTypeId,Price,UpDateDt FROM dbo.xw_P_PtypePrice WHERE ISNULL(Price,0)>0 ORDER BY PTypeId,UpDateDt DESC FOR JSON PATH"
call :run 04 employee           "SELECT etypeid,Parid,leveal,soncount,eusercode,ename,efullname,enamepy,Department,duty,Tel,Address,EMail,Birthday,ecomment,Incumbency,isStop FROM dbo.employee WHERE ISNULL(deleted,0)=0 ORDER BY leveal,ROWINDEX FOR JSON PATH"
call :run 05 department         "SELECT dtypeid,parid,leveal,soncount,dusercode,dname,dfullname,dnamepy,dcomment,isStop FROM dbo.Department WHERE ISNULL(deleted,0)=0 ORDER BY leveal,ROWINDEX FOR JSON PATH"
call :run 06 stock              "SELECT ktypeid,parid,leveal,soncount,kusercode,kname,kfullname,knamepy,Address,Person,Tel,kcomment,isStop FROM dbo.Stock WHERE ISNULL(deleted,0)=0 ORDER BY leveal,ROWINDEX FOR JSON PATH"
call :run 07 customer_owner     "WITH doc AS (SELECT btypeid,etypeid,[Date],Vchcode,VchType,ROW_NUMBER() OVER (PARTITION BY btypeid ORDER BY [Date] DESC,Vchcode DESC) rn,COUNT(*) OVER (PARTITION BY btypeid) doc_count FROM dbo.Dlyndx WHERE ISNULL(btypeid,'')<>'' AND ISNULL(etypeid,'')<>'' AND ISNULL(draft,0)=0) SELECT btypeid,etypeid,[Date] AS last_doc_date,VchType AS last_vch_type,doc_count FROM doc WHERE rn=1 ORDER BY btypeid FOR JSON PATH"
call :run 08 btype_doc_types    "SELECT d.btypeid,d.VchType,v.vchname,COUNT(*) AS doc_count FROM dbo.Dlyndx d LEFT JOIN dbo.Vchtype v ON v.vchtype=d.VchType WHERE ISNULL(d.btypeid,'')<>'' AND ISNULL(d.draft,0)=0 GROUP BY d.btypeid,d.VchType,v.vchname ORDER BY d.btypeid,doc_count DESC FOR JSON PATH"
call :run 09 bom_ndx            "SELECT n.VchCode,n.BOMFullname,n.BomUsercode,n.BMTypeid,n.PtypeId,n.Unit,n.Qty,n.Comment,n.CreateTime,n.UpdateTime,n.etypeid,t.BMfullname AS bom_type_name FROM dbo.BOM_Ndx n LEFT JOIN dbo.BOM_Type t ON t.BMTypeid=n.BMTypeid ORDER BY n.VchCode FOR JSON PATH"
call :run 10 bom_detail         "SELECT VchCode,Ptypeid,Unit,Qty,AttritionRate,Dlyorder,Comment FROM dbo.BOM_Detail ORDER BY VchCode,Dlyorder FOR JSON PATH"
call :run 11 goods_stocks       "SELECT PtypeId,KtypeId,SUM(Qty) AS qty,SUM(Total) AS total_amount,CASE WHEN SUM(Qty)<>0 THEN SUM(Total)/SUM(Qty) ELSE 0 END AS avg_unit_cost,COUNT(*) AS batch_rows FROM dbo.GoodsStocks GROUP BY PtypeId,KtypeId HAVING SUM(Qty)<>0 ORDER BY PtypeId,KtypeId FOR JSON PATH"
call :run 12 goods_stocks_batch "SELECT PtypeId,KtypeId,JobNumber,GoodsBatchID,Qty,Price,Total,BuyDate,UsefulEndDate,OutFactoryDate FROM dbo.GoodsStocks WHERE ISNULL(Qty,0)<>0 AND (ISNULL(JobNumber,'')<>'' OR ISNULL(GoodsBatchID,'')<>'') ORDER BY PtypeId,KtypeId FOR JSON PATH"

echo.
echo ===== Export Finished =====
dir /b "%OUTDIR%"
echo.
echo All files saved into export folder.
pause
exit /b 0

:run
set IDX=%~1
set NAME=%~2
set QUERY=%~3
set OUTFILE=%OUTDIR%\%IDX%_%NAME%.json
set TMPSQL=%temp%\sql_export_tmp.sql
echo [%IDX%] %NAME% ...
echo :XML ON > "%TMPSQL%"
echo SET NOCOUNT ON; >> "%TMPSQL%"
echo %QUERY%; >> "%TMPSQL%"
sqlcmd -S %SQLSERVER% %AUTH% -d %SQLDB% -y 0 -f 65001 -i "%TMPSQL%" -o "%OUTFILE%"
del "%TMPSQL%"
if errorlevel 1 (
  echo     --^> Failed, check %OUTFILE% for error
) else (
  for %%A in ("%OUTFILE%") do echo     --^> %%~zA bytes
)
exit /b 0
