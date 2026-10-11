@echo off
rem 抖音直播数据导出助手（图形界面）—— 双击本文件即可运行
rem   · 一键日报 / 按天导出 / 汇总统计 / 账号切换，都在一个窗口里
rem   · 首次使用点右上角“登录 / 检查登录状态”扫码一次，之后自动复用
rem   · 界面里每次点击实际执行的命令都会打印在窗口日志里，可复制到终端重放
rem   命令行等价物：python main.py daily      （或 python main.py daily --date 09-04）
rem   说明：本工具要求 Python 3.9+，本脚本会自动挑一个够新的 pythonw.exe。
setlocal
set "HERE=%~dp0"
set "PYW="
call :pick "%HERE%venv\Scripts\pythonw.exe"
if not errorlevel 1 goto run
call :pick "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
if not errorlevel 1 goto run
call :pick "pythonw"
if not errorlevel 1 goto run
goto nopy

:run
start "" "%PYW%" "%HERE%gui.py"
endlocal
exit /b 0

rem ---- 挑一个可用的 pythonw：文件存在（或在 PATH 上）且版本 >= 3.9 ----
:pick
if exist "%~1" (
  set "PYW=%~1"
) else (
  where "%~nx1" >nul 2>nul
  if errorlevel 1 exit /b 1
  set "PYW=%~nx1"
)
"%PYW%" -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul
if errorlevel 1 goto pick_bad
exit /b 0
:pick_bad
set "PYW="
exit /b 1

:nopy
echo [!] 没找到 Python 3.9+ 的 pythonw.exe。
echo     请安装 Python 3.9+（安装时勾选 Add Python to PATH），
echo     或把本文件里的候选路径改成你机器上 pythonw.exe 的真实位置。
pause
exit /b 1
