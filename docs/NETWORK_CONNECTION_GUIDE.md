# 远程网络连接指南

本指南适用于需要访问 EVE-NG 实验网的开发或运维环境。Web 应用启动不依赖此流程；
Mock 模式不需要 ZeroTier、路由或设备网络。网络接入与业务服务分开执行，避免普通启动
触发 `sudo`、修改系统路由或等待远程授权。

## 1. 配置

复制模板并填写实验网参数：

```bash
cp .env.example .env
```

| 变量 | 用途 |
|---|---|
| `ZEROTIER_NETWORK_ID` | ZeroTier 网络 ID |
| `EVE_MGMT_IP` | EVE-NG 管理网地址，同时作为旧版路由网关参数 |
| `EVE_MGMT_ROUTE_CIDR` | 需要访问的管理网段；留空时跳过本地路由设置 |
| `DEVICE_TEST_HOST` | SSH 连通性检查目标 |
| `DEVICE_TEST_TYPE` | Netmiko 设备类型，默认 `cisco_ios` |
| `DEVICE_USERNAME` / `DEVICE_PASSWORD` | 设备 SSH 凭据 |
| `SNMP_COMMUNITY` | SNMP 只读团体字 |

不要把真实凭据写入命令行或提交到 Git。

## 2. Linux 旧版流程

前置条件：已安装 ZeroTier、当前用户可执行 `sudo`、项目 `.venv` 已安装
`requirements/collect.txt`。

```bash
chmod +x scripts/connect_remote_network_legacy.sh
scripts/connect_remote_network_legacy.sh
```

脚本依次执行：

1. 检查 ZeroTier 是否在线；未入网时执行 `join`。
2. 最多等待 120 秒，确认 ZeroTier Central 已授权。
3. 按 `EVE_MGMT_ROUTE_CIDR` 和 `EVE_MGMT_IP` 添加管理路由。
4. `ping` EVE-NG 和测试设备。
5. 使用 Netmiko 检查设备 SSH。
6. 询问是否立即采集；也可使用 `--collect` 或 `--no-collect` 跳过询问。

脚本只使用项目 `.env`，不会修改 Web 服务配置。需要断开网络时：

```bash
sudo zerotier-cli leave "$ZEROTIER_NETWORK_ID"
```

如需删除本地管理路由，请先确认目标网段，再使用 `ip route del`；不同实验环境的路由
归属可能不同，不要在共享主机上直接删除未知路由。

## 3. Windows 手工步骤

Windows 不运行上述 Linux 脚本。请使用 ZeroTier 官方客户端或管理员 PowerShell：

```powershell
zerotier-cli info
zerotier-cli listnetworks
zerotier-cli join <网络ID>
Test-NetConnection <EVE_MGMT_IP> -Port 22
Test-NetConnection <DEVICE_TEST_HOST> -Port 22
```

在 ZeroTier Central 授权成员。管理网路由优先在 ZeroTier Managed Routes 中配置；
必要时检查 `route print`、`Get-NetRoute` 和 Windows 防火墙 UDP 9993。

连通后使用项目采集入口：

```powershell
.\.venv\Scripts\python.exe -m collector.run_collect --devices <设备名>
```

## 4. 验证与排障

```bash
ip route
ping -c 2 <EVE_MGMT_IP>
nc -vz <DEVICE_TEST_HOST> 22
.venv/bin/python -m collector.run_collect
```

- ZeroTier 显示 `ACCESS_DENIED`：等待管理员在 ZeroTier Central 授权。
- 能 `ping` 但 SSH 不通：检查 22 端口、设备凭据和 `DEVICE_TEST_TYPE`。
- EVE-NG 可达但业务网段不可达：检查 `EVE_MGMT_ROUTE_CIDR`、网关和 ZeroTier 路由。
- Web 服务正常但没有实时设备：这是网络或采集问题，不是应用启动失败。

旧版逻辑脚本：[scripts/connect_remote_network_legacy.sh](../scripts/connect_remote_network_legacy.sh)。
