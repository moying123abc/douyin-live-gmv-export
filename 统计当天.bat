@echo off
rem “统计当天”原来是命令行脚本，现已改成图形界面（启动助手）：
rem   · 双击本文件 = 双击 启动助手.bat：打开界面，“一键日报”里默认就是今天
rem   · 还想用命令行：python main.py daily      （或 python main.py daily --date 09-04）
call "%~dp0启动助手.bat"
exit /b %errorlevel%
