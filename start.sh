#!/bin/bash
# 企业网络智能运维诊断系统 - 一键启动脚本
# 所有网络接入信息与服务监听地址均从 .env / 环境变量读取，脚本内不硬编码。

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

# 载入本地 .env（若存在），用于读取 ZeroTier / 路由 / 服务监听配置
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$PROJECT_DIR/.env"
    set +a
fi

VENV_PY="$PROJECT_DIR/venv/bin/python3"
VENV_UVICORN="$PROJECT_DIR/venv/bin/uvicorn"

ZEROTIER_NETWORK_ID="${ZEROTIER_NETWORK_ID:-}"
EVE_MGMT_IP="${EVE_MGMT_IP:-}"
EVE_MGMT_ROUTE_CIDR="${EVE_MGMT_ROUTE_CIDR:-}"
APP_HOST="${APP_HOST:-127.0.0.1}"
APP_PORT="${APP_PORT:-8000}"
APP_RELOAD="${APP_RELOAD:-false}"

echo "========================================================================"
echo "企业网络智能运维诊断系统 - 启动"
echo "========================================================================"

# 检查虚拟环境
if [ ! -x "$VENV_PY" ]; then
    echo "❌ 虚拟环境不存在"
    echo "请先按 README 创建 venv 并安装 requirements/dev.txt + requirements/collect.txt"
    exit 1
fi

echo ""
echo "第1步: 连接远程EVE-NG网络"
echo "----------------------------------------------------------------------"

if [ -n "$ZEROTIER_NETWORK_ID" ]; then
    # 检查是否已加入网络
    if ! sudo zerotier-cli listnetworks 2>/dev/null | grep -q "$ZEROTIER_NETWORK_ID"; then
        echo "检查ZeroTier网络状态..."
        echo "加入网络: $ZEROTIER_NETWORK_ID"
        sudo zerotier-cli join "$ZEROTIER_NETWORK_ID"
        echo ""
        echo "⚠ 首次加入需要等待管理员授权"
        echo "  请告诉管理员在 https://my.zerotier.com 授权你的设备"
        echo ""
    fi

    # 获取ZeroTier网卡名并添加路由
    ZT_DEV=$(sudo zerotier-cli listnetworks | grep "$ZEROTIER_NETWORK_ID" | awk '{print $(NF-1)}')
    if [ -n "$ZT_DEV" ] && [ "$ZT_DEV" != "-" ] && [ -n "$EVE_MGMT_ROUTE_CIDR" ] && [ -n "$EVE_MGMT_IP" ]; then
        if ! sudo ip route show | grep -q "${EVE_MGMT_ROUTE_CIDR}.*$ZT_DEV"; then
            echo "添加到设备管理网络的路由..."
            sudo ip route add "$EVE_MGMT_ROUTE_CIDR" via "$EVE_MGMT_IP" dev "$ZT_DEV" 2>/dev/null || true
            echo "✓ 路由已添加"
        fi
    fi
else
    echo "⚠ 未配置 ZEROTIER_NETWORK_ID，跳过 ZeroTier 接入（仅本地环境可用）"
fi

# 现在运行连接测试
"$VENV_PY" connect_remote_network.py

echo ""
echo "第2步: 初始化数据库和加载设备"
echo "----------------------------------------------------------------------"
"$VENV_PY" - <<'PYEOF'
from dotenv import load_dotenv
load_dotenv()

from collector import db
import json

db.init_db()

with open('network/device_inventory.json') as f:
    inventory = json.load(f)

for device in inventory['devices']:
    db.upsert_device(device)

print(f"✓ 已加载 {len(inventory['devices'])} 台设备")
PYEOF

echo ""
echo "第3步: 检查 / 构建知识库索引"
echo "----------------------------------------------------------------------"
if [ -d "$PROJECT_DIR/data/chroma" ]; then
    echo "✓ 知识库索引已存在（如需重建: $VENV_PY -m knowledge_base.build_index --rebuild）"
else
    echo "未检测到知识库索引，开始构建..."
    "$VENV_PY" -m knowledge_base.build_index --rebuild
fi

echo ""
echo "第4步: 启动Web应用"
echo "----------------------------------------------------------------------"
echo ""
echo "Web应用将启动在 http://$APP_HOST:$APP_PORT"
echo "按 Ctrl+C 停止服务"
echo ""

RELOAD_FLAG=""
if [ "$APP_RELOAD" = "true" ] || [ "$APP_RELOAD" = "1" ]; then
    RELOAD_FLAG="--reload"
fi

# 捕获Ctrl+C
trap "on_exit" EXIT INT

on_exit() {
    echo ""
    echo ""
    echo "========================================================================"
    echo "服务已停止"
    echo "========================================================================"

    if [ -n "$ZEROTIER_NETWORK_ID" ]; then
        echo ""
        read -p "断开ZeroTier远程连接吗? [y/n]: " disconnect
        if [ "$disconnect" = "y" ] || [ "$disconnect" = "Y" ]; then
            echo "正在断开远程网络..."
            sudo zerotier-cli leave "$ZEROTIER_NETWORK_ID" 2>/dev/null && echo "✓ 已断开ZeroTier网络" || echo "⚠ 可能已断开"
        fi
    fi

    echo ""
    echo "感谢使用企业网络智能运维诊断系统"
    echo ""
}

# shellcheck disable=SC2086
"$VENV_UVICORN" webapp.main:app --host "$APP_HOST" --port "$APP_PORT" $RELOAD_FLAG
