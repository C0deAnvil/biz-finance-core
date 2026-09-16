@echo off
setlocal enabledelayedexpansion

REM==============================================================
REM  旧系统（SQL Server）主数据导出 —— 生成 13 个 json 文件
REM
REM  前置：已把 .bak 还原成数据库 niaosu2020
REM  用法：双击运行，或在命令行里跑。默认用 Windows 身份验证连本机默认实例。
REM        若实例名/账号不同，改下面三行。
REM ======================================================================

set SQLSERVER=DESKTOP-4AI9ATV\MSSQLSERVER01
set SQLDB=niaosu2020

REM ---- 身份验证 ----
REM 默认走 Windows 身份验证：不需要密码，也就不会在文件里留下密码。
REM -C = 信任服务器证书（sqlcmd 18+ 默认要求加密连接，不加会报证书链错误）
set AUTH=-E -C

REM 要改用 sa 账号的话，把上面一行注释掉，取消下面两行注释。
REM 密码是运行时手输的，不落盘、不进 git。
REM set /p SAPWD=请输入 sa 密码:
REM set AUTH=-U sa -P %SAPWD% -C

set OUTDIR=%~dp0export
if not exist "%OUTDIR%" mkdir "%OUTDIR%"

echo 服务器: %SQLSERVER%   数据库: %SQLDB%
echo 输出目录: %OUTDIR%
echo.

REM ---- 连接自检：连不上就别往下跑，省得生成一堆报错文件 ----
echo 正在测试连接 ...
sqlcmd -S %SQLSERVER% %AUTH% -d %SQLDB% -h -1 -W -Q "SELECT 'OK=' + CONVERT(varchar,COUNT(*)) FROM dbo.btype" 2>nul | findstr /C:"OK=" >nul
if errorlevel 1 (
  echo.
  echo [连接失败] 请检查：
  echo   1. 实例名对不对   —— 当前 %SQLSERVER%，跑 sqlcmd -L 看本机实例列表
  echo   2. 数据库名对不对 —— 当前 %SQLDB%
  echo   3. 证书报错       —— 确认 AUTH 里带了 -C
  echo   4. sqlcmd 在不在 PATH —— 装 SQL Server 命令行工具（sqlcmd -?  能跑就说明有）
  echo   5. 换 sa 账号     —— 见文件开头被注释掉的那两行
  echo.
  pause
  exit /b 1
)
echo 连接正常，开始导出 ...
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
echo ===== 导出完成 =====
dir /b "%OUTDIR%"
echo.
echo 把 export 整个文件夹交给导入脚本即可。
pause
exit /b 0

:run
set IDX=%~1
set NAME=%~2
set QUERY=%~3
set OUTFILE=%OUTDIR%\%IDX%_%NAME%.json
echo [%IDX%] %NAME% ...
REM -y 0 = 不截断长文本（FOR JSON 返回的是一个超长字段，必须加）
REM -h -1 = 不输出表头   -W = 去掉行尾空格   -f 65001 = UTF-8
sqlcmd -S %SQLSERVER% %AUTH% -d %SQLDB% -y 0 -h -1 -W -f 65001 -Q "%QUERY%" -o "%OUTFILE%"
if errorlevel 1 (
  echo     ^>^> 失败，请看 %OUTFILE% 里的报错
) else (
  for %%A in ("%OUTFILE%") do echo     ^>^> %%~zA 字节
)
exit /b 0
