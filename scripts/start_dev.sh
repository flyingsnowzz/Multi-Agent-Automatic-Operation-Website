#!/bin/bash
# 开发环境启动脚本（Linux/Mac）

echo "=============================================="
echo "多Agent自动运营网站 - 开发环境启动脚本"
echo "=============================================="

# 检查虚拟环境是否存在
if [ ! -d ".venv" ]; then
    echo "正在创建虚拟环境..."
    python3 -m venv .venv
fi

# 激活虚拟环境
echo "激活虚拟环境..."
source .venv/bin/activate

# 安装依赖
echo "检查依赖..."
pip install -q -r requirements.txt

# 创建日志目录
mkdir -p logs

# 初始化数据库
echo "初始化数据库..."
python scripts/init_db.py

echo ""
echo "=============================================="
echo "开发环境已准备就绪！"
echo "=============================================="
echo ""
echo "运行工作流示例："
echo "  python main.py --engine hybrid --topic \"测试选题\" --keyword \"测试关键词\""
echo ""
echo "启动定时调度器："
echo "  python scheduler/scheduler.py"
echo ""