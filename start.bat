@echo off
REM ============================================
REM  业财一体化系统 - Windows 一键启动脚本
REM  用法:
REM    start.bat           启动后端(前台)
REM    start.bat --test    先跑测试再启动
REM    start.bat --build   先构建前端再启动
REM ============================================
setlocal
cd /d %~dp0

if "%1"=="--test" (
    echo [1/3] 运行测试...
    venv\Scripts\python.exe scripts\run_tests.py --fast || goto :fail
)
if "%1"=="--build" (
    echo [1/3] 构建前端...
    pushd frontend
    call node_modules\.bin\vite build || (popd & goto :fail)
    popd
)

echo [2/3] 启动后端 (http://localhost:8000)...
venv\Scripts\python.exe main.py
goto :eof

:fail
echo 启动失败,请检查上方错误信息。
exit /b 1