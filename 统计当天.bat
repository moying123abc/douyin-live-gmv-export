@echo off
rem 一键日报:双击默认统计今天,自动导出并生成"两列表日报文件夹"
rem   - 每场成交点表: time(只含 HH:MM) + gmv(只保留 gmv>0 的行)
rem   - 日报汇总表: 场次级统计 + 全天合计
rem 用法: 双击(统计今天)或命令行带日期(可传多个日期,各生成一个日报文件夹):
rem   统计当天.bat 2026-09-04
rem   统计当天.bat 2026-09-01 2026-09-02 2026-09-03
rem   统计当天.bat 2026-09-01,2026-09-02
chcp 65001 >nul
cd /d "%~dp0"
if "%~1"=="" (
  python main.py daily
) else (
  python main.py daily --date %*
)
echo.
echo 完成,结果见上方"日报_日期"文件夹(多日期时每个日期各一个文件夹,已自动打开输出目录)。
echo 按任意键关闭…
pause >nul
