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

## 2. EVE-NG 入口与设备地址

`zerotier-cli listnetworks` 显示 `OK` 只说明本地网络配置和授权正常，不代表 ZeroTier
数据面已连通。先确认 `10.10.146.218` 可 ping/ARP，再确认管理网路由存在：

```bash
ip route | grep '192.168.249.0/24'
# 期望：192.168.249.0/24 via 10.10.146.218 dev <ZeroTier接口>
```

| 用途 | 地址 |
|---|---|
| EVE-NG 登录和拓扑页面 | `http://10.10.146.218/` |
| EVE-NG 协议 | 仅 HTTP，不要使用 `https://` |
| EVE Web 登录 | 使用 EVE 的 Web 管理账号，不是设备 SSH 密码 |
| 设备 SSH 网段 | `192.168.249.0/24` |
| 设备 SSH 账号 | `admin`，密码使用项目 `.env` 的 `DEVICE_PASSWORD` |
| EVE 主机 SSH | `10.10.146.218:22` 不是设备入口，不要使用 |

设备 SSH 地址：

| 拓扑节点 | 项目设备名 | SSH 地址 |
|---|---|---|
| R-HQ | HQ-R1 | `admin@192.168.249.11` |
| R-B1 | BR-A-R2 | `admin@192.168.249.12` |
| R-B2 | BR-B-R3 | `admin@192.168.249.13` |
| SW-HQ | HQ-SW1 | `admin@192.168.249.14` |
| SW-B1 | BR-A-SW2 | `admin@192.168.249.15` |
| SW-B2 | BR-B-SW3 | `admin@192.168.249.16` |

原生控制台通过 EVE 主机的端口映射访问：

| 节点 | 地址 |
|---|---|
| R-HQ | `telnet 10.10.146.218 32769` |
| R-B1 | `telnet 10.10.146.218 32770` |
| R-B2 | `telnet 10.10.146.218 32771` |
| SW-HQ | `telnet 10.10.146.218 32772` |
| SW-B1 | `telnet 10.10.146.218 32773` |
| SW-B2 | `telnet 10.10.146.218 32774` |
| SRV-01 | `telnet 10.10.146.218 32775` |
| PC-HQ-01 | `telnet 10.10.146.218 32776` |
| PC-B1-01 | `telnet 10.10.146.218 32777` |
| PC-B2-01 | `telnet 10.10.146.218 32778` |

SRV-01、PC-HQ-01、PC-B1-01、PC-B2-01 建议直接使用控制台，不要假定它们提供
`192.168.249.x` 的 SSH。

## 3. Linux 旧版流程

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

### 3.1 开机自动恢复管理路由

本机已安装 `zerotier-eve-route.service`（oneshot）和辅助脚本
`/usr/local/sbin/zerotier-eve-route`。服务在 `zerotier-one` 启动后等待
`ZEROTIER_NETWORK_ID` 对应接口就绪，再执行：

```bash
ip route replace 192.168.249.0/24 via 10.10.146.218 dev <ZeroTier接口>
```

管理命令：

```bash
systemctl status zerotier-eve-route.service
sudo systemctl restart zerotier-eve-route.service
sudo systemctl disable --now zerotier-eve-route.service
```

脚本默认使用 `743993800f53f957`、`192.168.249.0/24`、`10.10.146.218`。如 `.env`
中这些值变化，需要同步修改 `/usr/local/sbin/zerotier-eve-route` 的默认值，或在
unit 中用 `Environment=` 覆盖 `ZEROTIER_NETWORK_ID`、`EVE_MGMT_ROUTE_CIDR`、
`EVE_MGMT_IP`。

## 4. Windows 手工步骤

Windows 不运行上述 Linux 脚本。请使用 ZeroTier 官方客户端或管理员 PowerShell：

```powershell
zerotier-cli info
zerotier-cli listnetworks
zerotier-cli join <网络ID>
Test-NetConnection <EVE_MGMT_IP> -Port 80
Test-NetConnection <DEVICE_TEST_HOST> -Port 22
```

`<EVE_MGMT_IP>:22` 不是设备入口；EVE Web 使用 80 端口，设备 SSH 使用
`192.168.249.x:22`。

在 ZeroTier Central 授权成员。管理网路由优先在 ZeroTier Managed Routes 中配置；
必要时检查 `route print`、`Get-NetRoute` 和 Windows 防火墙 UDP 9993。

连通后使用项目采集入口：

```powershell
.\.venv\Scripts\python.exe -m collector.run_collect --devices <设备名>
```

## 5. 验证与排障

```bash
ip route | grep '192.168.249.0/24'
ping -c 2 <EVE_MGMT_IP>
curl -sS -o /dev/null -w '%{http_code}\n' "http://<EVE_MGMT_IP>/"
nc -vz <DEVICE_TEST_HOST> 22
.venv/bin/python -m collector.run_collect
```

- ZeroTier `listnetworks` 显示 `OK` 但 `ping <EVE_MGMT_IP>` 失败：先查 peer 和数据面，
  不要直接重复添加 `192.168.249.0/24` 路由。
- ZeroTier 显示 `ACCESS_DENIED`：等待管理员在 ZeroTier Central 授权。
- 能 `ping` 但 SSH 不通：检查 `192.168.249.x:22`、设备凭据和 `DEVICE_TEST_TYPE`。
- `EVE_MGMT_IP:22` 不通是正常的，EVE 主机 22 不是设备入口。
- EVE-NG 可达但业务网段不可达：检查 `EVE_MGMT_ROUTE_CIDR`、网关和 ZeroTier 路由。
- Web 服务正常但没有实时设备：这是网络或采集问题，不是应用启动失败。

旧版逻辑脚本：[scripts/connect_remote_network_legacy.sh](../scripts/connect_remote_network_legacy.sh)。
