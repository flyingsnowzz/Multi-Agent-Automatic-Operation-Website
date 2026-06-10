@echo off
REM 开发环境启动脚本
REM 使用方法: .\scripts\start_dev.ps1

echo ==============================================
echo 多Agent自动运营网站 - 开发环境启动脚本
echo ==============================================

REM 检查虚拟环境是否存在
if not exist ".venv" (
    echo 正在创建虚拟环境...
    python -m venv .venv
)

REM 激活虚拟环境
echo 激活虚拟环境...
call .venv\Scripts\activate.bat

REM 安装依赖（如果需要）
echo 检查依赖...
pip install -q -r requirements.txt

REM 创建日志目录
if not exist "logs" mkdir logs

REM 初始化数据库（仅首次运行）
echo 初始化数据库...
python scripts\init_db.py

echo.
echo ==============================================
echo 开发环境已准备就绪！
echo ==============================================
echo.
echo 运行工作流示例：
echo   python main.py --engine hybrid --topic "测试选题" --keyword "测试关键词"
echo.
echo 启动定时调度器：
echo   python scheduler/scheduler.py
echo.

REM 保持窗口打开
cmd /k