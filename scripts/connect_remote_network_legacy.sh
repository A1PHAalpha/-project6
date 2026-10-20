#!/usr/bin/env bash
#
# 远程实验网旧版连接流程（仅 Linux）。
# 此脚本与应用启动完全解耦，不会由 start.py / Web 服务自动调用。
#
# 用法:
#   sudo scripts/connect_remote_network_legacy.sh
#   scripts/connect_remote_network_legacy.sh --collect
#   scripts/connect_remote_network_legacy.sh --no-collect
#
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

if [[ "$(uname -s)" != "Linux" ]]; then
    echo "错误：此旧版网络脚本仅支持 Linux。Windows 请按 docs/NETWORK_CONNECTION_GUIDE.md 手工接入。" >&2
    exit 1
fi

if [[ -f "$PROJECT_ROOT/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/.env"
    set +a
fi

PYTHON="${PYTHON:-$PROJECT_ROOT/.venv/bin/python}"
COLLECT_MODE="ask"

for arg in "$@"; do
    case "$arg" in
        --collect) COLLECT_MODE="yes" ;;
        --no-collect) COLLECT_MODE="no" ;;
        -h|--help)
            echo "用法: $0 [--collect|--no-collect]"
            exit 0
            ;;
        *)
            echo "未知参数: $arg" >&2
            exit 2
            ;;
    esac
done

require_var() {
    local name="$1"
    if [[ -z "${!name:-}" ]]; then
        echo "错误：缺少环境变量 $name，请在项目根 .env 中配置。" >&2
        exit 1
    fi
}

as_root() {
    if [[ "${EUID}" -eq 0 ]]; then
        "$@"
    else
        sudo "$@"
    fi
}

zerotier() {
    as_root zerotier-cli "$@"
}

require_var ZEROTIER_NETWORK_ID
require_var EVE_MGMT_IP
require_var DEVICE_TEST_HOST
require_var DEVICE_USERNAME
require_var DEVICE_PASSWORD

command -v zerotier-cli >/dev/null 2>&1 || {
    echo "错误：未找到 zerotier-cli，请先安装并启动 ZeroTier。" >&2
    exit 1
}

if [[ ! -x "$PYTHON" ]]; then
    PYTHON="$(command -v python3 || true)"
fi
if [[ -z "$PYTHON" ]]; then
    echo "错误：未找到项目 .venv Python 或 python3。" >&2
    exit 1
fi

echo "第 1 步：检查 ZeroTier"
if ! zerotier info | grep -q "ONLINE"; then
    echo "错误：ZeroTier 未在线，请先启动 zerotier-one 服务。" >&2
    exit 1
fi

networks="$(zerotier listnetworks)"
if ! grep -Fq "$ZEROTIER_NETWORK_ID" <<<"$networks"; then
    echo "加入网络 $ZEROTIER_NETWORK_ID ..."
    zerotier join "$ZEROTIER_NETWORK_ID"
fi

echo "等待 ZeroTier 网络授权（最多 120 秒）..."
authorized=false
for _ in $(seq 1 12); do
    networks="$(zerotier listnetworks || true)"
    if grep -F "$ZEROTIER_NETWORK_ID" <<<"$networks" | grep -qw "OK"; then
        authorized=true
        break
    fi
    sleep 10
done

if [[ "$authorized" != "true" ]]; then
    echo "错误：网络尚未授权。请在 ZeroTier Central 的 Members 中授权本机后重试。" >&2
    exit 1
fi

network_line="$(grep -F "$ZEROTIER_NETWORK_ID" <<<"$networks" | head -n 1)"
echo "ZeroTier 已授权：$network_line"

echo "第 2 步：配置 EVE-NG 管理路由"
zt_dev="$(awk -v id="$ZEROTIER_NETWORK_ID" '$0 ~ id {print $(NF-1); exit}' <<<"$networks")"
if [[ -n "${EVE_MGMT_ROUTE_CIDR:-}" && -n "$zt_dev" && "$zt_dev" != "-" ]]; then
    if ! ip route show | grep -Fq "$EVE_MGMT_ROUTE_CIDR"; then
        as_root ip route add "$EVE_MGMT_ROUTE_CIDR" via "$EVE_MGMT_IP" dev "$zt_dev"
        echo "已添加路由 $EVE_MGMT_ROUTE_CIDR via $EVE_MGMT_IP dev $zt_dev"
    else
        echo "管理网路由已存在：$EVE_MGMT_ROUTE_CIDR"
    fi
else
    echo "未配置 EVE_MGMT_ROUTE_CIDR，跳过本地路由设置。"
fi

echo "第 3 步：检查网络连通性"
ping -c 2 -W 2 "$EVE_MGMT_IP" >/dev/null
echo "EVE-NG 可达：$EVE_MGMT_IP"
ping -c 2 -W 2 "$DEVICE_TEST_HOST" >/dev/null
echo "测试设备可达：$DEVICE_TEST_HOST"

echo "第 4 步：检查设备 SSH"
"$PYTHON" - <<'PY'
import os

from netmiko import ConnectHandler

conn = ConnectHandler(
    device_type=os.getenv("DEVICE_TEST_TYPE", "cisco_ios"),
    host=os.environ["DEVICE_TEST_HOST"],
    username=os.environ["DEVICE_USERNAME"],
    password=os.environ["DEVICE_PASSWORD"],
    timeout=15,
)
try:
    output = conn.send_command("show version | include Version", read_timeout=15)
    print(output[:200])
finally:
    conn.disconnect()
PY
echo "SSH 连接成功"

if [[ "$COLLECT_MODE" == "ask" ]]; then
    read -r -p "是否立即执行设备采集？[y/N] " answer
    [[ "$answer" =~ ^[Yy]$ ]] && COLLECT_MODE="yes" || COLLECT_MODE="no"
fi

if [[ "$COLLECT_MODE" == "yes" ]]; then
    echo "第 5 步：采集设备数据"
    "$PYTHON" -m collector.run_collect
fi

echo "网络接入与连通性检查完成。Web 应用不会自动执行本脚本。"
