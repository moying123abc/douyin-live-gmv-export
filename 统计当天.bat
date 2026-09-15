@echo off
rem 一键日报:双击默认统计今天,自动导出并生成"两列表日报文件夹"
rem   - 每场成交点表: time(只含 HH:MM) + gmv(只保留 gmv>0 的行)
rem   - 日报汇总表: 场次级统计 + 全天合计
rem 用法: 双击(统计今天)或命令行带日期:  统计当天.bat 2026-09-04
chcp 65001 >nul
cd /d "%~dp0"
set "D=%~1"
if "%D%"=="" (
  python main.py daily
) else (
  python main.py daily --date %D%
)
echo.
echo 完成,结果见上方"日报_日期"文件夹(已自动打开)。
echo 按任意键关闭…
pause >nul
