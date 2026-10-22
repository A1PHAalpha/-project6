# 运维指南

## 1. 运行方式

生产或日常运行使用唯一入口：

```bash
python start.py --live
```

启动器会构建 React 前端，并由同一个 FastAPI 进程托管 `/console` 和 `/api`。
默认监听 `127.0.0.1:8000`，通过 `.env` 修改：

```dotenv
APP_HOST=127.0.0.1
APP_PORT=8000
```

如需局域网访问，将 `APP_HOST` 改为监听地址，并在防火墙中仅开放所需端口。服务没有内置
多租户，不建议直接暴露到公网。

## 2. 配置

运行时只读取项目根目录 `.env`。首次启动会从 `.env.example` 创建文件并生成随机
`APP_API_TOKEN`；该文件不会提交到 Git。

| 配置组 | 主要变量 |
|---|---|
| Web 鉴权 | `APP_API_TOKEN`、`APP_HOST`、`APP_PORT` |
| 服务行为 | `APP_AUTO_COLLECT`、`APP_RELOAD`、`APP_ENABLE_DOCS`、`APP_RBAC_ENFORCE` |
| LLM | `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL` |
| 设备 | `DEVICE_USERNAME`、`DEVICE_PASSWORD`、`SNMP_COMMUNITY` |
| 存储 | `SQLITE_DB_PATH`、`CHROMA_DB_PATH` |
| Embedding | `EMBEDDING_MODEL`、`FASTEMBED_CACHE_PATH` |

保存设置 API 只允许修改白名单键；敏感值返回掩码，设置变更后需要重启服务。

## 3. 数据目录

默认数据均位于项目本地：

```text
data/ops.db           SQLite 业务数据库
data/chroma/          Chroma 知识库索引
models/fastembed/     Embedding 模型缓存
reports/              导出报告和配置备份
```

这些路径已被 `.gitignore` 排除。升级或迁移前应同时备份 SQLite 和 Chroma。

## 4. 采集

Web 启动与真实设备采集已解耦。默认 Mock 启动不执行采集；真实模式下仍需要
`APP_AUTO_COLLECT=true` 才会在启动时触发一次后台采集。

采集计划页面当前只保存频率、时段、范围、并发、超时和重试配置，**不会自动执行调度**。
默认计划开关为关闭；需要定时采集时请先使用手动采集，或由运维侧的任务调度器调用
`collector.run_collect --trigger scheduled`。生产版调度器属于后续硬化项。

手动采集：

```powershell
python -m collector.run_collect --devices BR-A-R2
```

采集只自动执行白名单中的只读命令。设备不可达时记录失败或未知状态，不用历史成功状态
补齐当前事实。

人工终端的写命令要求两项前置条件：会话关联设备已完成实际配置备份
（`backup_status=completed`），以及服务端签发的一次性确认令牌。只读命令不受备份门槛
限制，但所有命令和输出仍进入审计。

## 5. 知识库

启动器检查 Chroma 集合 `ops_knowledge_base`。索引缺失或为空时自动构建；诊断过程仍可在
索引不可用时降级为设备证据。

显式重建：

```powershell
python -m knowledge_base.build_index --rebuild
```

构建成功后才发布新索引版本，旧版本保留用于追溯。重建期间应避免多个进程同时写入同一
Chroma 目录。

## 6. 远程实验网

ZeroTier、EVE-NG 路由和设备接入属于环境准备工作，不属于 Web 启动流程。Windows 使用
ZeroTier 客户端手工接入；Linux 旧逻辑脚本位于
`scripts/connect_remote_network_legacy.sh`。

完整步骤和排障命令见 [NETWORK_CONNECTION_GUIDE.md](NETWORK_CONNECTION_GUIDE.md)。
不要在共享主机上直接删除来源不明的路由。

## 7. 备份与升级

建议在每次版本升级前执行：

1. 停止服务并确认没有正在运行的采集或知识库构建任务；
2. 备份 `.env`、`data/ops.db`、`data/chroma/` 和必要的报告；
3. 更新代码与依赖；
4. 运行 `python -m collector.migrate`；
5. 执行 `python start.py --check`、后端测试和启动冒烟；
6. 启动后检查登录、总览、采集批次和知识库状态。

数据库迁移会保留旧表和兼容视图，但迁移成功后仍应保留备份直到业务验证完成。

## 8. 健康检查

`/api/health` 同样需要认证，可使用 Bearer Token：

```bash
curl -H "Authorization: Bearer $APP_API_TOKEN" \
  http://127.0.0.1:8000/api/health
```

Windows PowerShell：

```powershell
$headers = @{ Authorization = "Bearer $env:APP_API_TOKEN" }
Invoke-WebRequest http://127.0.0.1:8000/api/health -Headers $headers -UseBasicParsing
```

也可在登录会话中访问 `/api/v1/overview` 和 `/api/v1/knowledge-base/status`。
日志中的命令和设备输出已经过项目脱敏函数处理。
