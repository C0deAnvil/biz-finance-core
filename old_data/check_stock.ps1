#Requires -Version 5.1
# Pure ASCII. Checks which inventory table actually holds the data.
param(
    [string]$Server   = "DESKTOP-4AI9ATV\MSSQLSERVER01",
    [string]$Database = "niaosu2020"
)
$ErrorActionPreference = "Stop"
$scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$outDir    = Join-Path $scriptDir "export"
if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir | Out-Null }
$connStr = "Server=$Server;Database=$Database;Integrated Security=True;TrustServerCertificate=True;Encrypt=False;"
Add-Type -AssemblyName System.Data

function Get-ScalarCount {
    param([string]$Sql)
    $conn = New-Object System.Data.SqlClient.SqlConnection $connStr
    try {
        $conn.Open(); $cmd = $conn.CreateCommand(); $cmd.CommandText = $Sql; $cmd.CommandTimeout = 600
        $v = $cmd.ExecuteScalar()
        if ($null -eq $v -or $v -is [DBNull]) { return [int64]0 }
        return [int64]$v
    } finally { $conn.Close(); $conn.Dispose() }
}
function Get-JsonFromQuery {
    param([string]$Sql)
    $conn = New-Object System.Data.SqlClient.SqlConnection $connStr
    $sb = New-Object System.Text.StringBuilder
    try {
        $conn.Open(); $cmd = $conn.CreateCommand(); $cmd.CommandText = $Sql; $cmd.CommandTimeout = 600
        $r = $cmd.ExecuteReader()
        while ($r.Read()) { if (-not $r.IsDBNull(0)) { [void]$sb.Append($r.GetString(0)) } }
        $r.Close()
    } finally { $conn.Close(); $conn.Dispose() }
    return $sb.ToString()
}

Write-Host "Which inventory table holds the data?" -ForegroundColor Cyan
$tables = @(
    "GoodsStocks", "PhysicalGoodsStocks", "GoodsStocksSL", "PhysicalGoodsStocksSL",
    "KWGoodsStocksSL", "ProdStocks", "SideStocks", "IniGoodsStocks", "IniPhysicalStocks"
)
$rows = @()
foreach ($t in $tables) {
    try {
        $total   = Get-ScalarCount "SELECT COUNT(*) FROM dbo.$t"
        $nonzero = try { Get-ScalarCount "SELECT COUNT(*) FROM dbo.$t WHERE ISNULL(Qty,0)<>0" } catch { "n/a" }
        $rows += [pscustomobject]@{ Table=$t; Total=$total; NonZeroQty=$nonzero }
    } catch {
        $rows += [pscustomobject]@{ Table=$t; Total="no such table"; NonZeroQty="-" }
    }
}
$rows | Format-Table -AutoSize

# If PhysicalGoodsStocks has more data, export it as an alternative source
$phys = ($rows | Where-Object { $_.Table -eq "PhysicalGoodsStocks" }).Total
if ($phys -is [int64] -and $phys -gt 23) {
    Write-Host "PhysicalGoodsStocks has more rows - exporting it as 13_physical_stocks.json" -ForegroundColor Yellow
    $sql = "SELECT PtypeId,KtypeId,SUM(Qty) AS qty,COUNT(*) AS batch_rows FROM dbo.PhysicalGoodsStocks GROUP BY PtypeId,KtypeId HAVING SUM(Qty)<>0 ORDER BY PtypeId,KtypeId FOR JSON PATH"
    $json = Get-JsonFromQuery $sql
    if (-not [string]::IsNullOrWhiteSpace($json)) {
        [IO.File]::WriteAllText((Join-Path $outDir "13_physical_stocks.json"), $json, (New-Object System.Text.UTF8Encoding($false)))
        $n = ([regex]::Matches($json,'\},\{')).Count + 1
        Write-Host "  exported $n rows" -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "How many products actually have any stock anywhere:" -ForegroundColor Cyan
$d = @(
    @{ l="ptype leaf products";        s="SELECT COUNT(*) FROM dbo.ptype WHERE ISNULL(deleted,0)=0 AND ISNULL(soncount,0)=0" }
    @{ l="distinct ptype in GoodsStocks"; s="SELECT COUNT(DISTINCT PtypeId) FROM dbo.GoodsStocks" }
    @{ l="distinct ptype in Physical";    s="SELECT COUNT(DISTINCT PtypeId) FROM dbo.PhysicalGoodsStocks" }
)
$dr = @()
foreach ($x in $d) {
    try { $dr += [pscustomobject]@{ Item=$x.l; Count=(Get-ScalarCount $x.s) } }
    catch { $dr += [pscustomobject]@{ Item=$x.l; Count="failed" } }
}
$dr | Format-Table -AutoSize
