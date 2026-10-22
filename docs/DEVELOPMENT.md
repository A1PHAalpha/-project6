# 开发指南

## 1. 环境要求

| 依赖 | 版本 |
|---|---|
| Python | 3.12 x64 |
| Node.js | 18 或更高 |
| npm | 随 Node.js 安装 |

项目使用 FastEmbed 和 ONNX Runtime，不需要 PyTorch、CUDA 或独立 Embedding 服务。
首次安装依赖和下载模型需要网络。

## 2. 首次运行

在项目根目录执行：

```powershell
python start.py
```

启动器会依次检查 Python、虚拟环境、后端依赖、`.env`、知识库索引、Node 和前端依赖，
随后以 Mock 模式构建并启动前后端。

常用参数：

```text
--mock           使用示例数据，默认
--live           使用真实 API 和设备采集
--check          只检查环境，不安装、构建或启动
--skip-install   缺少依赖时不自动安装
--skip-build     复用现有 frontend/dist（仅 Live 模式）
--skip-index     不自动构建缺失的知识库索引
```

完整参数以 `python start.py --help` 为准。

## 3. Mock 与真实 API

Mock 模式只替换前端数据来源，不会连接设备，也不会自动采集。页头会显示
`MOCK · 示例数据`。

联调真实数据：

```powershell
python start.py --live
```

真实模式仍受 `APP_AUTO_COLLECT` 控制。需要自动采集时在 `.env` 中显式设置：

```dotenv
APP_AUTO_COLLECT=true
```

## 4. 独立开发前端

后端和 Vite 分两个终端运行。

终端一：

```powershell
.\.venv\Scripts\python.exe -m uvicorn webapp.main:app --reload --host 127.0.0.1 --port 8000
```

终端二：

```powershell
Set-Location frontend
npm install
npm run dev
```

访问 `http://127.0.0.1:5173/overview`。Vite 将 `/api`、`/login` 和 `/logout`
代理到 `127.0.0.1:8000`。

默认开发模式使用 Mock 数据。联调真实 API 时：

```powershell
$env:VITE_USE_MOCK_API="false"
npm run dev
```

Linux/macOS 使用：

```bash
VITE_USE_MOCK_API=false npm run dev
```

## 5. 测试

后端：

```powershell
python -m pytest
python -m ruff check .
python -m compileall -q collector control rag_engine knowledge_base ops_tools webapp
```

前端：

```powershell
Set-Location frontend
npm test
npm run build
```

Playwright 使用本机 Chrome：

```powershell
Set-Location frontend
npm run test:e2e -- --project=desktop-chrome
npm run test:e2e -- --project=mobile-chrome
```

生成不依赖真实 LLM/设备的 Live 演示数据：

```bash
.venv/bin/python -m test_cases.seed_demo --db-path ./data/demo.db --reset
SQLITE_DB_PATH=./data/demo.db .venv/bin/python start.py --live
```

## 6. 修改数据库

数据库结构变更必须新增迁移，不能直接改写已发布迁移。修改后至少验证：

1. 空数据库能够初始化到最新版本；
2. 旧数据库备份后能够幂等升级；
3. 旧版兼容视图和公共数据库函数仍然可用；
4. 全量后端测试通过。

执行显式迁移：

```powershell
python -m collector.migrate
```

## 7. 代码约定

- 领域模块不要反向依赖 Web 模板或 React 代码。
- API 层负责参数校验、DTO 和 HTTP 状态，不复制业务规则。
- 数据库调用通过 `collector.db` 的公共接口，避免路由直接拼接 SQL。
- 新前端请求统一经过 typed API client；组件不直接调用 `fetch`。
- 时间进入接口时使用 UTC ISO 8601，浏览器按本地时区展示。
- 命令、凭据和模型输出必须经过脱敏后才能写入日志。

## 8. 常见问题

**启动器找不到 `.venv`**

先执行 `python start.py` 完成首次创建。若不需要自动安装，可加 `--skip-install`。

**401 未授权**

检查项目根 `.env` 中的 `APP_API_TOKEN`。首次生成的值会在启动日志中显示，
手动测试也可以发送 `Authorization: Bearer <token>`。

**Mock 页面正常，但没有真实设备数据**

确认使用 `--live`，再检查 `.env`、设备可达性、采集开关和最近采集批次。真实网络接入见
[NETWORK_CONNECTION_GUIDE.md](NETWORK_CONNECTION_GUIDE.md)。

**前端构建失败**

确认 Node.js 18+，在 `frontend/` 执行 `npm ci`，再运行 `npm run build`。

**知识库为空**

去掉 `--skip-index`，或显式执行：

```powershell
python -m knowledge_base.build_index --rebuild
```
