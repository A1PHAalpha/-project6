#!/usr/bin/env python3
"""
连接远程EVE-NG网络的工具脚本。
设置ZeroTier、测试连通性、执行采集。

所有连接参数（ZeroTier 网络 ID、管理网 IP、设备凭据）均从环境变量/.env 读取，
未配置时给出明确指引并退出，脚本内不保留任何明文凭据或固定 IP。
"""
import os
import subprocess
import sys

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    """读取必需的环境变量，缺失则提示并退出。"""
    value = os.getenv(name)
    if not value:
        print(f"❌ 缺少必需的环境变量 {name}，请参考 .env.example 配置后重试")
        sys.exit(1)
    return value


def run_cmd(cmd, description="", check=True):
    """执行命令并报告结果"""
    print(f"\n{'='*70}")
    print(f"[操作] {description}")
    print(f"{'='*70}")
    print(f"命令: {cmd}\n")

    result = subprocess.run(cmd, shell=True, capture_output=False, text=True)
    if check and result.returncode != 0:
        print(f"❌ 命令失败，返回码: {result.returncode}")
        sys.exit(1)
    print("✓ 完成")
    return result


def setup_zerotier():
    """配置ZeroTier连接"""
    print("\n" + "="*70)
    print("第1步: ZeroTier网络配置")
    print("="*70)

    network_id = _require("ZEROTIER_NETWORK_ID")
    eve_mgmt_ip = _require("EVE_MGMT_IP")
    # ZeroTier 分配地址所在网段（取 EVE 管理 IP 的 /24 前缀）
    zt_prefix = ".".join(eve_mgmt_ip.split(".")[:3]) + "."

    # 检查ZeroTier状态
    result = subprocess.run("sudo zerotier-cli info", shell=True, capture_output=True, text=True)
    if "ONLINE" not in result.stdout:
        print("❌ ZeroTier未运行")
        run_cmd("sudo zerotier-cli info", "检查ZeroTier状态")
        return False

    print("✓ ZeroTier服务已运行")

    # 检查网络状态
    result = subprocess.run("sudo zerotier-cli listnetworks", shell=True, capture_output=True, text=True)
    if "OK" in result.stdout and zt_prefix in result.stdout:
        print("✓ 已加入ZeroTier网络并获得授权")
        for line in result.stdout.split('\n'):
            if zt_prefix in line:
                print(f"  本机ZeroTier IP: {line.split()[-1]}")
        return True
    elif "ACCESS_DENIED" in result.stdout:
        print("⚠ 等待管理员授权...")
        print(f"  网络ID: {network_id}")
        print("  请告诉管理员访问 https://my.zerotier.com 在Members中授权你")
        print("")

        # 等待授权循环
        max_wait = 12  # 最多等待2分钟（每10秒检查一次）
        for attempt in range(max_wait):
            import time
            time.sleep(10)

            result = subprocess.run("sudo zerotier-cli listnetworks", shell=True, capture_output=True, text=True)
            if "OK" in result.stdout and zt_prefix in result.stdout:
                print("✓ 授权成功！")
                for line in result.stdout.split('\n'):
                    if zt_prefix in line:
                        print(f"  本机ZeroTier IP: {line.split()[-1]}")
                return True

            remaining = max_wait - attempt - 1
            print(f"  等待中... ({remaining*10}秒后重试)")

        print("❌ 等待超时，请手动授权后重试")
        return False
    else:
        print("❌ ZeroTier网络状态异常")
        run_cmd("sudo zerotier-cli listnetworks", "检查网络列表")
        return False


def test_connectivity():
    """测试到远程设备的连接"""
    print("\n" + "="*70)
    print("第2步: 测试远程连接")
    print("="*70)

    eve_mgmt_ip = _require("EVE_MGMT_IP")
    test_host = _require("DEVICE_TEST_HOST")

    print(f"\n[测试] EVE-NG管理网络 ({eve_mgmt_ip})")
    result = subprocess.run(f"ping -c 2 {eve_mgmt_ip}", shell=True, capture_output=True, text=True)
    if result.returncode == 0:
        print("✓ EVE-NG可达")
    else:
        print("❌ EVE-NG不可达")
        return False

    print(f"\n[测试] 设备管理网络 ({test_host})")
    result = subprocess.run(f"ping -c 2 {test_host}", shell=True, capture_output=True, text=True)
    if result.returncode == 0:
        print("✓ 目标设备可达")
    else:
        print("❌ 目标设备不可达")
        return False

    username = _require("DEVICE_USERNAME")
    password = _require("DEVICE_PASSWORD")
    device_type = os.getenv("DEVICE_TEST_TYPE", "cisco_ios")

    print(f"\n[测试] SSH连接 ({username}@{test_host})")
    try:
        from netmiko import ConnectHandler
        conn = ConnectHandler(
            device_type=device_type, host=test_host,
            username=username, password=password, timeout=15,
        )
        output = conn.send_command("show version | include Version", read_timeout=15)
        conn.disconnect()
        print("✓ SSH连接成功")
        print(output[:200])
        return True
    except Exception as e:
        print(f"❌ SSH连接失败: {type(e).__name__}: {e}")
        return False


def collect_data():
    """执行数据采集（走统一入口 collector.run_collect）"""
    print("\n" + "="*70)
    print("第3步: 采集远程设备数据")
    print("="*70)

    if not os.path.exists('.env'):
        print("❌ 找不到 .env 文件")
        return False

    result = subprocess.run([sys.executable, "-m", "collector.run_collect"])
    return result.returncode == 0


def main():
    """主程序"""
    print("\n" + "="*70)
    print("企业网络智能运维诊断系统 - 远程网络连接工具")
    print("="*70)

    # 第1步：ZeroTier
    if not setup_zerotier():
        print("\n⚠ ZeroTier配置不完整，请先完成授权")
        return False

    # 第2步：连通性测试
    if not test_connectivity():
        print("\n❌ 无法连接到远程网络")
        return False

    # 第3步：数据采集
    print("\n是否立即执行数据采集? (y/n)")
    response = input(">>> ")
    if response.lower() == 'y':
        if not collect_data():
            print("\n❌ 数据采集失败")
            return False

    print("\n" + "="*70)
    print("✓ 所有检查完成，远程网络已就绪")
    print("="*70)
    print("\n后续可以:")
    print("  1. python3 -c \"from dotenv import load_dotenv; load_dotenv(); "
          "from rag_engine.diagnosis_engine import diagnose; print(diagnose('...', use_kb=True))\"")
    print("  2. 在EVE-NG中手工设置故障 → 采集 → 诊断 → 修复")

    return True


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
