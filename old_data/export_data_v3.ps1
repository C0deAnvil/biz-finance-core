#Requires -Version 5.1
<#
    Legacy SQL Server master-data export (same-schema databases)

    Usage:
        cd E:\System_dev\yecai-new\old_data
        .\export_data_v3.ps1            # Windows auth
        .\export_data_v3.ps1 -UseSa     # sa account, password typed at runtime
        .\export_data_v3.ps1 -Database shiji2020 -OutputDir .\export_shiji2020

    NOTE: this file is deliberately pure ASCII.
    PowerShell 5.1 decodes .ps1 as the system ANSI codepage (GBK on Chinese
    Windows) unless the file carries a UTF-8 BOM, and the BOM does not
    survive every file-transfer path. Keeping the script ASCII-only removes
    the whole class of problem. Chinese data itself is handled correctly:
    it is read over SqlClient as UTF-16 and written with an explicit
    UTF-8 encoder, never through a console codepage.
#>

param(
    [string]$Server   = "DESKTOP-4AI9ATV\MSSQLSERVER01",
    [string]$Database = "niaosu2020",
    [string]$OutputDir = "",
    [switch]$UseSa,
    [string]$SaPassword = ""
)

$ErrorActionPreference = "Stop"

# Anchor output to the script's own folder, not the current working directory.
# (Do NOT name any variable $pwd - that is PowerShell's builtin current-directory
#  automatic variable; overwriting it makes $PWD.Path return $null.)
$scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$outDir = if ($OutputDir) { [IO.Path]::GetFullPath($OutputDir) } else { Join-Path $scriptDir "export" }
if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir | Out-Null }

if ($UseSa) {
    # CI/local migration can pass the password through an environment variable
    # so it never has to be committed to this script. Interactive use still
    # prompts when neither parameter nor environment variable is supplied.
    $saPlain = $SaPassword
    if ([string]::IsNullOrWhiteSpace($saPlain)) {
        $saPlain = $env:YECAI_LEGACY_SA_PASSWORD
    }
    if ([string]::IsNullOrWhiteSpace($saPlain)) {
        $saSecure = Read-Host "sa password" -AsSecureString
        $saPlain  = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
                        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($saSecure))
    }
    $connStr  = "Server=$Server;Database=$Database;User Id=sa;Password=$saPlain;TrustServerCertificate=True;Encrypt=False;"
    $authDesc = "SQL login (sa)"
} else {
    $connStr  = "Server=$Server;Database=$Database;Integrated Security=True;TrustServerCertificate=True;Encrypt=False;"
    $authDesc = "Windows authentication"
}

Write-Host "Server  : $Server"
Write-Host "Database: $Database"
Write-Host "Auth    : $authDesc"
Write-Host "Output  : $outDir"
Write-Host ""

Add-Type -AssemblyName System.Data

# IMPORTANT: never return a DataTable from a PowerShell function.
# PowerShell unrolls it into individual DataRow objects, so the caller's
# $result.Rows is $null ("Cannot index into a null array").
# These two helpers return a plain String / Int64 instead - no unrolling.

function Get-JsonFromQuery {
    # Runs a FOR JSON query and returns the full JSON as one string.
    # SQL Server splits FOR JSON output across many rows; concatenate them all.
    param([string]$Sql, [int]$TimeoutSec = 600)
    $conn = New-Object System.Data.SqlClient.SqlConnection $connStr
    $sb   = New-Object System.Text.StringBuilder
    try {
        $conn.Open()
        $cmd = $conn.CreateCommand()
        $cmd.CommandText    = $Sql
        $cmd.CommandTimeout = $TimeoutSec
        $reader = $cmd.ExecuteReader()
        while ($reader.Read()) {
            if (-not $reader.IsDBNull(0)) { [void]$sb.Append($reader.GetString(0)) }
        }
        $reader.Close()
    } finally {
        $conn.Close(); $conn.Dispose()
    }
    return $sb.ToString()
}

function Get-ScalarCount {
    param([string]$Sql, [int]$TimeoutSec = 600)
    $conn = New-Object System.Data.SqlClient.SqlConnection $connStr
    try {
        $conn.Open()
        $cmd = $conn.CreateCommand()
        $cmd.CommandText    = $Sql
        $cmd.CommandTimeout = $TimeoutSec
        $value = $cmd.ExecuteScalar()
        if ($null -eq $value -or $value -is [DBNull]) { return [int64]0 }
        return [int64]$value
    } finally {
        $conn.Close(); $conn.Dispose()
    }
}

Write-Host "Testing connection ..." -NoNewline
try {
    $probe = Get-ScalarCount "SELECT COUNT(*) FROM dbo.btype"
    Write-Host " OK (btype has $probe rows)" -ForegroundColor Green
} catch {
    Write-Host " FAILED" -ForegroundColor Red
    Write-Host "  $($_.Exception.Message)" -ForegroundColor Yellow
    exit 1
}
Write-Host ""

$queries = @(
    @{ name = "00_btype_roots";        sql = "SELECT btypeid,parid,leveal,busercode,bname,bfullname,soncount FROM dbo.btype WHERE ISNULL(deleted,0)=0 AND (leveal<=2 OR ISNULL(parid,'')='') ORDER BY leveal,ROWINDEX FOR JSON PATH" }
    @{ name = "01_btype";              sql = "SELECT btypeid,parid,leveal,soncount,busercode,bname,bfullname,bnamepy,Area,AreaTypeID,TelAndAddress,PostCode,Person,linker,linkerTel,moPhone,Fax,EMail,TaxNumber,BankAndAcount,BankAccounts,bcomment,isStop,DefInput,GatherBtypeId,R_WARNUP,P_WARNUP,ChangeDay,ChangeRate FROM dbo.btype WHERE ISNULL(deleted,0)=0 ORDER BY leveal,ROWINDEX FOR JSON PATH" }
    @{ name = "02_ptype";              sql = "SELECT ptypeid,ParId,leveal,soncount,pusercode,pname,pfullname,pnamepy,Standard,Type,Area,punitname,SideUnit,pcomment,isStop,warnup,warndown,LastBuyPrice,Ptypetype,Custom1,Custom2,Custom3,Custom4,sourcewayzz,sourcewaywg,sourcewayww FROM dbo.ptype p WHERE ISNULL(deleted,0)=0 OR EXISTS (SELECT 1 FROM dbo.GoodsStocks g WHERE g.PtypeId=p.ptypeid AND ISNULL(g.Qty,0)<>0) ORDER BY leveal,RowIndex FOR JSON PATH" }
    @{ name = "03_ptype_price";        sql = "SELECT PTypeId,UnitID,PRTypeId,Price,UpDateDt FROM dbo.xw_P_PtypePrice WHERE ISNULL(Price,0)>0 ORDER BY PTypeId,UpDateDt DESC FOR JSON PATH" }
    @{ name = "04_employee";           sql = "SELECT etypeid,Parid,leveal,soncount,eusercode,ename,efullname,enamepy,Department,duty,Tel,Address,EMail,Birthday,ecomment,Incumbency,isStop FROM dbo.employee WHERE ISNULL(deleted,0)=0 ORDER BY leveal,ROWINDEX FOR JSON PATH" }
    @{ name = "05_department";         sql = "SELECT dtypeid,parid,leveal,soncount,dusercode,dname,dfullname,dnamepy,dcomment,isStop FROM dbo.Department WHERE ISNULL(deleted,0)=0 ORDER BY leveal,ROWINDEX FOR JSON PATH" }
    @{ name = "06_stock";              sql = "SELECT ktypeid,parid,leveal,soncount,kusercode,kname,kfullname,knamepy,Address,Person,Tel,kcomment,isStop FROM dbo.Stock WHERE ISNULL(deleted,0)=0 ORDER BY leveal,ROWINDEX FOR JSON PATH" }
    @{ name = "07_customer_owner";     sql = "WITH doc AS (SELECT btypeid,etypeid,[Date],Vchcode,VchType,draft,ROW_NUMBER() OVER (PARTITION BY btypeid ORDER BY [Date] DESC,Vchcode DESC) rn,COUNT(*) OVER (PARTITION BY btypeid) doc_count FROM dbo.Dlyndx WHERE ISNULL(btypeid,'')<>'' AND ISNULL(etypeid,'')<>'') SELECT btypeid,etypeid,[Date] AS last_doc_date,VchType AS last_vch_type,draft,doc_count FROM doc WHERE rn=1 ORDER BY btypeid FOR JSON PATH" }
    @{ name = "08_btype_doc_types";    sql = "SELECT d.btypeid,d.VchType,v.vname AS vchname,COUNT(*) AS doc_count FROM dbo.Dlyndx d LEFT JOIN dbo.Vchtype v ON v.vchtype=d.VchType WHERE ISNULL(d.btypeid,'')<>'' GROUP BY d.btypeid,d.VchType,v.vname ORDER BY d.btypeid,doc_count DESC FOR JSON PATH" }
    @{ name = "09_bom_ndx";            sql = "SELECT n.VchCode,n.BOMFullname,n.BomUsercode,n.BMTypeid,n.PtypeId,n.Unit,n.Qty,n.Comment,n.CreateTime,n.UpdateTime,n.etypeid,t.BMfullname AS bom_type_name FROM dbo.BOM_Ndx n LEFT JOIN dbo.BOM_Type t ON t.BMTypeid=n.BMTypeid ORDER BY n.VchCode FOR JSON PATH" }
    @{ name = "10_bom_detail";         sql = "SELECT VchCode,Ptypeid,Unit,Qty,AttritionRate,Dlyorder,Comment FROM dbo.BOM_Detail ORDER BY VchCode,Dlyorder FOR JSON PATH" }
    @{ name = "11_goods_stocks";       sql = "SELECT PtypeId,KtypeId,SUM(Qty) AS qty,SUM(Total) AS total_amount,CASE WHEN SUM(Qty)<>0 THEN SUM(Total)/SUM(Qty) ELSE 0 END AS avg_unit_cost,COUNT(*) AS batch_rows FROM dbo.GoodsStocks GROUP BY PtypeId,KtypeId HAVING SUM(Qty)<>0 ORDER BY PtypeId,KtypeId FOR JSON PATH" }
    @{ name = "12_goods_stocks_batch"; sql = "SELECT PtypeId,KtypeId,JobNumber,GoodsBatchID,Qty,Price,Total,BuyDate,UsefulEndDate,OutFactoryDate FROM dbo.GoodsStocks WHERE ISNULL(Qty,0)<>0 AND (ISNULL(JobNumber,'')<>'' OR ISNULL(GoodsBatchID,'')<>'') ORDER BY PtypeId,KtypeId FOR JSON PATH" }
)

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$results = @()

foreach ($item in $queries) {
    $outFile = Join-Path $outDir "$($item.name).json"
    Write-Host ("  {0,-24}" -f $item.name) -NoNewline
    try {
        $json = Get-JsonFromQuery $item.sql

        if ([string]::IsNullOrWhiteSpace($json)) {
            [IO.File]::WriteAllText($outFile, "[]", $utf8NoBom)
            Write-Host "empty (0 rows)" -ForegroundColor Yellow
            $results += [pscustomobject]@{ File=$item.name; Rows=0; SizeKB=0; Status="EMPTY" }
            continue
        }

        [IO.File]::WriteAllText($outFile, $json, $utf8NoBom)
        $size = (Get-Item $outFile).Length

        # Avoid ConvertFrom-Json: PowerShell 5.1 has a ~2MB MaxJsonLength limit
        # and would falsely reject the large files. Structural check instead.
        $okShape = $json.StartsWith("[") -and $json.EndsWith("]")
        $count   = ([regex]::Matches($json, '\},\{')).Count + 1
        if ($okShape) {
            Write-Host ("{0,8} rows  {1,8:N0} KB" -f $count, ($size/1KB)) -ForegroundColor Green
            $results += [pscustomobject]@{ File=$item.name; Rows=$count; SizeKB=[math]::Round($size/1KB); Status="OK" }
        } else {
            Write-Host "BAD SHAPE (not wrapped in [ ])" -ForegroundColor Red
            $results += [pscustomobject]@{ File=$item.name; Rows=0; SizeKB=[math]::Round($size/1KB); Status="BADSHAPE" }
        }
    } catch {
        Write-Host "FAILED" -ForegroundColor Red
        Write-Host "      $($_.Exception.Message)" -ForegroundColor DarkGray
        $results += [pscustomobject]@{ File=$item.name; Rows=0; SizeKB=0; Status="FAILED" }
    }
}

Write-Host ""
Write-Host "==== EXPORT RESULT ====" -ForegroundColor Cyan
$results | Format-Table -AutoSize

# ---- Diagnose empty results: is the table empty, or is the filter too strict? ----
$emptyOnes = @($results | Where-Object { $_.Status -eq "EMPTY" })
if ($emptyOnes.Count -gt 0) {
    Write-Host "==== DIAGNOSTICS FOR EMPTY RESULTS ====" -ForegroundColor Cyan
    $diag = @(
        @{ label="Dlyndx total";                sql="SELECT COUNT(*) FROM dbo.Dlyndx" }
        @{ label="Dlyndx with btypeid";         sql="SELECT COUNT(*) FROM dbo.Dlyndx WHERE ISNULL(btypeid,'')<>''" }
        @{ label="Dlyndx with etypeid";         sql="SELECT COUNT(*) FROM dbo.Dlyndx WHERE ISNULL(etypeid,'')<>''" }
        @{ label="Dlyndx with both";            sql="SELECT COUNT(*) FROM dbo.Dlyndx WHERE ISNULL(btypeid,'')<>'' AND ISNULL(etypeid,'')<>''" }
        @{ label="BOM_Ndx total";               sql="SELECT COUNT(*) FROM dbo.BOM_Ndx" }
        @{ label="BOM_Detail total";            sql="SELECT COUNT(*) FROM dbo.BOM_Detail" }
        @{ label="GoodsStocks total";           sql="SELECT COUNT(*) FROM dbo.GoodsStocks" }
        @{ label="GoodsStocks with batch no";   sql="SELECT COUNT(*) FROM dbo.GoodsStocks WHERE ISNULL(JobNumber,'')<>'' OR ISNULL(GoodsBatchID,'')<>''" }
        @{ label="DlySale total";               sql="SELECT COUNT(*) FROM dbo.DlySale" }
        @{ label="DlySale with etypeid";        sql="SELECT COUNT(*) FROM dbo.DlySale WHERE ISNULL(etypeid,'')<>''" }
    )
    $diagRows = @()
    foreach ($d in $diag) {
        try   { $diagRows += [pscustomobject]@{ Item=$d.label; Rows=(Get-ScalarCount $d.sql) } }
        catch { $diagRows += [pscustomobject]@{ Item=$d.label; Rows="query failed" } }
    }
    $diagRows | Format-Table -AutoSize
    $diagJson = $diagRows | ConvertTo-Json
    [IO.File]::WriteAllText((Join-Path $outDir "99_diagnostics.json"), $diagJson, $utf8NoBom)
}

# ---- Encoding self-check: confirm Chinese round-trips correctly ----
$sample = Join-Path $outDir "05_department.json"
if (Test-Path $sample) {
    $txt = [IO.File]::ReadAllText($sample, [Text.Encoding]::UTF8)
    $hasCjk      = $txt -match '[\u4e00-\u9fa5]'
    $hasMojibake = $txt -match '\uFFFD'
    Write-Host "==== ENCODING SELF-CHECK ====" -ForegroundColor Cyan
    Write-Host ("  contains real Chinese : {0}" -f $(if ($hasCjk) { "YES  <-- good" } else { "NO   <-- problem" })) -ForegroundColor $(if ($hasCjk) { "Green" } else { "Red" })
    Write-Host ("  contains U+FFFD junk  : {0}" -f $(if ($hasMojibake) { "YES  <-- problem" } else { "NO   <-- good" })) -ForegroundColor $(if ($hasMojibake) { "Red" } else { "Green" })
    $m = [regex]::Match($txt, '"dname":"([^"]{1,12})"')
    if ($m.Success) { Write-Host "  sample department name: $($m.Groups[1].Value)" }
}

Write-Host ""
$bad = @($results | Where-Object { $_.Status -notin @("OK","EMPTY") })
if ($bad.Count -gt 0) {
    Write-Host "$($bad.Count) file(s) had problems - send the output above." -ForegroundColor Red
    exit 1
} else {
    Write-Host "Export finished. Next step:" -ForegroundColor Green
    Write-Host "  python old_data\inspect_export.py --source `"$outDir`" --out `"$outDir\inspect_report.md`""
}
