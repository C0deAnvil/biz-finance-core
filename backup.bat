@echo off
REM ============================================
REM  数据库备份脚本 (mysqldump)
REM  用法: backup.bat [输出目录]
REM ============================================
setlocal
cd /d %~dp0

set OUT_DIR=%1
if "%OUT_DIR%"=="" set OUT_DIR=backups
if not exist "%OUT_DIR%" mkdir "%OUT_DIR%"

set STAMP=%date:~0,4%%date:~5,2%%date:~8,2%_%time:~0,2%%time:~3,2%
set STAMP=%STAMP: =0%
set FILE=%OUT_DIR%\yecai_db_%STAMP%.sql

echo 备份数据库到 %FILE% ...
"C:\Program Files\MySQL\MySQL Server 8.2\bin\mysqldump.exe" -uyecai_user -pCPHWcphw11223344 --databases yecai_db > "%FILE%" 2>nul

if exist "%FILE%" (
    echo 备份完成: %FILE%
) else (
    echo 备份失败,请检查 MySQL 路径与账号密码。
    exit /b 1
)