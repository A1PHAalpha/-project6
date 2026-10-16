#!/bin/bash
# 企业网络智能运维诊断系统 - 数据采集脚本
# 凭据统一从 .env 读取（由 collector.run_collect 加载），脚本内不出现任何明文口令。

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

VENV_PY="$PROJECT_DIR/venv/bin/python3"

echo "========================================================================"
echo "企业网络智能运维诊断系统 - 数据采集"
echo "========================================================================"
echo ""

# 检查虚拟环境
if [ ! -x "$VENV_PY" ]; then
    echo "❌ 虚拟环境不存在，请先按 README 创建 venv 并安装依赖"
    exit 1
fi

# 检查 .env
if [ ! -f "$PROJECT_DIR/.env" ]; then
    echo "❌ .env 文件不存在，请从 .env.example 复制并填写真实凭据"
    exit 1
fi

echo "采集选项:"
echo "  1. 采集所有设备 (推荐)"
echo "  2. 仅采集特定设备"
echo ""
read -p "请选择 [1/2]: " option

if [ "$option" = "2" ]; then
    echo ""
    echo "可用设备: HQ-R1, BR-A-R2, BR-B-R3, HQ-SW1, BR-A-SW2, BR-B-SW3"
    read -p "请输入设备名 (多个用逗号分隔): " devices
    "$VENV_PY" -m collector.run_collect --devices "$devices"
else
    echo ""
    echo "采集所有设备..."
    "$VENV_PY" -m collector.run_collect
fi

echo ""
echo "========================================================================"
echo "采集完成"
echo "========================================================================"
echo ""
echo "后续操作:"
echo "  1. 在Web界面 (http://localhost:8000) 查看最新设备状态"
echo "  2. 提交诊断问题"
echo "  3. 需要再采集数据，运行此脚本"
echo ""
