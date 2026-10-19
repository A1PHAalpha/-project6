# Windows 环境运行与调试指南

> 诊断日期：2026-10-18  
> 更新日期：2026-10-20（启动自动检查、项目 `.env` 显式加载、知识库降级已接入）  
> 实测环境：Windows x64、Python 3.12.10、PowerShell  
> 项目目录：`C:\Users\popcorn\Desktop\-project6`

## 1. 结论

项目的核心 Python 链路目前是**可运行的**，但还不能在原生 Windows 上“一键启动”。

已实测通过：

- `requirements/dev.txt` 与 `requirements/collect.txt` 共解析并安装 118 个包，`pip check` 无依赖冲突。
- `ruff check .` 通过。
- 常规单测 `62 passed, 1 deselected`；单独运行的 embedding slow 测试 `1 passed`。
- `BAAI/bge-small-zh-v1.5` 的 ONNX 模型可下载、可初始化。
- 真实构建 26 条知识库记录成功。
- 新进程重新打开持久化 Chroma 索引并检索成功，返回 4 条中文运维资料。
- 使用缓存并设置 `HF_HUB_OFFLINE=1` 后，embedding 仍可离线加载。
- Uvicorn 可正常监听本机端口；未登录访问页面返回 302 跳转到 `/login`，携带有效 Token 的 `/api/diagnoses` 返回 200。

尚未验证：

- 真实 EVE-NG/Cisco 设备的 SSH 与 SNMP 采集。
- ZeroTier 到实验网的接入。
- 真实 LLM API 调用。

原因很直接：原始仓库不包含 `.env`，也没有可用的设备/LLM 凭据。原始仓库同样不包含 `data/chroma` 索引；本次验证已经在本地生成了可用的 `.venv`、`models/fastembed` 和 `data/chroma`，但这些都被 `.gitignore` 排除，不会进入 Git。

## 2. 已确认的问题

### 2.1 阻断项：Bash 脚本不能直接在 PowerShell 中使用

`start.sh` 和 `collect.sh` 都使用 Linux 路径与命令，例如：

- [start.sh](start.sh) 第 16 行使用 `venv/bin/python3`。
- [start.sh](start.sh) 第 43-58 行使用 `sudo`、`grep`、`awk`、`ip route`。
- [collect.sh](collect.sh) 第 8 行同样使用 `venv/bin/python3`。

在 PowerShell 中执行 `./start.sh` 会报“不是可识别的命令”，在 Git Bash 中执行也会因为 `sudo`、`ip` 和虚拟环境路径不匹配而失败。

**处理方式：** Windows 下跳过两个 `.sh` 脚本，直接调用 Python 模块。本文后面的命令已经替代了一键脚本的主要步骤。

### 2.2 阻断项：远程网络脚本是 Linux 专用实现

[connect_remote_network.py](connect_remote_network.py) 第 54-82 行固定调用 `sudo zerotier-cli`，第 111、119 行固定使用 `ping -c 2`。

Windows 上：

- `zerotier-cli` 需要在管理员 PowerShell 中运行，不需要也不存在 `sudo`。
- Windows `ping` 使用 `-n`，不是 `-c`。
- 脚本不能验证 Windows 下的路由和网卡名。

**处理方式：** 不要在 Windows 上直接运行该脚本。使用 ZeroTier 官方客户端/CLI 完成入网，再用 `Test-NetConnection` 和 `collector.run_collect` 验证与采集。

### 2.3 已修复：知识库构建命令会忽略 `.env`

修复前，[knowledge_base/build_index.py](knowledge_base/build_index.py) 没有调用 `load_dotenv()`，而 [knowledge_base/embedding.py](knowledge_base/embedding.py) 在导入时读取：

- `EMBEDDING_MODEL`
- `FASTEMBED_CACHE_PATH`

因此修复前执行：

```powershell
python -m knowledge_base.build_index --rebuild
```

修复前它**不会读取 `.env` 中的这些配置**。如果 `.env` 自定义了模型、缓存目录或 Chroma 路径，索引构建和 Web 查询可能使用不同配置，表现为模型重复下载、索引落到错误目录，或离线时找不到已下载模型。

Web 应用 [webapp/main.py](webapp/main.py) 在导入 RAG 模块前调用了 `load_dotenv()`，所以 Web 查询侧会读取 `.env`。这正是两侧配置容易不一致的原因。

**当前实现：** 已新增 [project_config.py](project_config.py)，并让知识库构建、database 与 Web 入口
显式加载项目根 `.env`。`build_index.py` 在导入 embedding 之前完成加载，因此模型、缓存和
Chroma 路径与查询侧一致。

```python
from project_config import load_project_env

load_project_env()
```

不要只写 `load_dotenv()`：它在找不到当前目录 `.env` 时会继续向上搜索，可能加载父目录中
其他项目的配置。第 2.7 节记录了本机实际遇到的这种情况。

### 2.4 中风险：`start.sh` 的索引判断没有遵守 `CHROMA_DB_PATH`

[start.sh](start.sh) 第 93 行只检查固定的 `$PROJECT_DIR/data/chroma`。

如果通过 `CHROMA_DB_PATH` 把索引放到其他目录，一键脚本仍会认为索引不存在并尝试重建。Windows 下按本文直接执行构建命令即可绕开。

### 2.5 Windows 安全语义差异：`chmod 600` 不会设置 NTFS 专用 ACL

[control/backup_manager.py](control/backup_manager.py) 第 54 行使用 `os.chmod(path, 0o600)` 保护含设备配置的备份。

在 Linux 上这会收紧权限；在 Windows 上它主要影响只读属性，不能替代 NTFS ACL。如果备份文件包含敏感配置，应在 Windows 上额外设置目录/文件 ACL，并避免放在 OneDrive 等自动同步目录。

### 2.6 文档问题：README 中的示例偏 Unix

以下内容不能原样粘到 PowerShell：

- `./venv/bin/python`
- `./start.sh`
- `python - <<'EOF'` heredoc
- `cp .env.example .env`
- `curl -H "X-API-Token: $APP_API_TOKEN"` 的环境变量写法

本文给出了 PowerShell 等价命令。

### 2.7 已修复：缺少本地 `.env` 时会误读父目录配置

`webapp/main.py:21`、`collector/run_collect.py:17`、`connect_remote_network.py:15`、
`test_cases/compare_kb.py:19`、`test_cases/demo_e2e.py:21` 都调用了无参数的
`load_dotenv()`。`python-dotenv` 会从当前目录向上搜索 `.env`。

当前机器上存在 `C:\Users\popcorn\Desktop\.env`，而项目根目录没有 `.env`。实测：

```powershell
python -c "from dotenv import find_dotenv; print(find_dotenv())"
```

输出为：

```text
C:\Users\popcorn\Desktop\.env
```

这意味着项目可能静默读取其他项目的设备账号、LLM Key 或服务令牌。除了配置不可复现，
还存在凭据串用和泄露风险。

**当前实现：**

1. 始终在项目根目录创建自己的 `.env`。
2. 启动前运行上面的 `find_dotenv()` 检查，确认路径是项目内 `.env`。
3. 各主要入口已经统一改用 `project_config.load_project_env()`，使用固定绝对路径，
   不再向上搜索父目录。

本次没有输出父目录 `.env` 中的任何值。

### 2.8 已修复：示例 Token 能通过启动校验

`.env.example` 现在写的是：

```dotenv
APP_API_TOKEN=CHANGE_ME
```

该值不满足“长度不小于 16”的检查，复制模板后必须先替换才能启动。

**处理方式：** 启动前用 `python -c "import secrets; print(secrets.token_urlsafe(32))"`
生成随机值并写入 `.env`。

## 3. 推荐环境

- Windows 10/11 x64。
- **Python 3.12.x**。项目配置目标为 3.12，本次也在 3.12.10 实测通过。
- PowerShell 7 或 Windows PowerShell 5.1。
- 本地 NTFS 磁盘。Chroma 持久化数据不建议放在网络共享、同步盘或 OneDrive 中。
- 首次安装依赖和下载 embedding 模型时需要互联网。
- 不需要安装 PyTorch、CUDA 或显卡驱动。项目的本地 embedding 使用 `fastembed` + ONNX Runtime CPU 运行时。

不建议直接升级到 Python 3.13/3.14。当前固定版本 `netmiko==4.3.0` 仍会导入 `telnetlib`，而该模块已从较新的 Python 版本中移除；项目也未在这些版本上验证。

## 4. 从零准备 PowerShell 环境

先打开 PowerShell，进入项目目录：

```powershell
Set-Location "C:\Users\popcorn\Desktop\-project6"
```

建议为当前进程启用 UTF-8，避免中文日志、中文路径和检索结果乱码：

```powershell
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
chcp 65001 > $null
```

### 4.1 创建并激活虚拟环境

```powershell
python --version
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

如果 PowerShell 阻止激活脚本，可只对当前窗口放宽策略：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

也可以不激活，直接使用 `.venv\Scripts\python.exe` 和 `.venv\Scripts\uvicorn.exe`。

### 4.2 安装依赖

```powershell
python -m pip install -r requirements\dev.txt -r requirements\collect.txt
python -m pip check
```

如果 PyPI 访问慢，可以在确认镜像可信后临时追加：

```powershell
-i https://pypi.tuna.tsinghua.edu.cn/simple
```

本次实测的 `.venv` 约 333 MiB，embedding 模型缓存约 91 MiB，均不含在 Git 仓库中。

## 5. 配置 `.env`

复制模板：

```powershell
Copy-Item .env.example .env
notepad .env
```

至少确认以下项：

```dotenv
APP_HOST=127.0.0.1
APP_PORT=8000
APP_RELOAD=true
APP_ENABLE_DOCS=false
APP_API_TOKEN=替换为随机生成的32位令牌

SQLITE_DB_PATH=./data/ops.db
CHROMA_DB_PATH=./data/chroma
EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
FASTEMBED_CACHE_PATH=./models/fastembed

LLM_API_KEY=你的API密钥
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen-plus

DEVICE_USERNAME=admin
DEVICE_PASSWORD=你的设备口令
SNMP_COMMUNITY=你的只读团体字
APP_AUTO_COLLECT=true
```

注意：

- `.env` 已被 `.gitignore` 排除，不要把真实凭据提交到 Git。
- `APP_API_TOKEN` 少于 16 位时，应用会主动拒绝启动；模板中的 `CHANGE_ME` 必须先替换。
- 项目配置只加载本项目根目录 `.env`，不会读取 `C:\Users\popcorn\Desktop\.env`。
- `.env` 中的相对路径以**启动命令所在目录**为基准；请始终在项目根目录运行命令。
- `.env` 不会自动展开 `%USERPROFILE%` 或 PowerShell 变量，路径建议直接用相对路径或完整 Windows 路径。

## 6. 初始化数据库与知识库

### 6.1 初始化 SQLite 并加载设备台账

正常启动 Web 服务时会自动完成这一步。下面的命令仅用于单独初始化或排障：

```powershell
.\.venv\Scripts\python.exe -c "from collector import db; db.init_db(); print('devices=', len(db.load_inventory('network/device_inventory.json')))"
```

预期输出包含 `devices= 9`。

如果使用已激活的虚拟环境，也可以把前面的解释器替换成 `python`。

### 6.2 准备 embedding 缓存目录

```powershell
New-Item -ItemType Directory -Force models\fastembed | Out-Null
$env:EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"
$env:FASTEMBED_CACHE_PATH = (Resolve-Path models\fastembed).Path
$env:CHROMA_DB_PATH = Join-Path (Get-Location).Path "data\chroma"
```

`BAAI/bge-small-zh-v1.5` 是 FastEmbed 官方支持的 512 维中文文本 embedding 模型，不是任意 Hugging Face 模型都能直接传给 `TextEmbedding`。

### 6.3 首次构建索引

```powershell
.\.venv\Scripts\python.exe -m knowledge_base.build_index --rebuild
```

首次运行会下载 5 个模型文件，本次实测约需 2 分钟。成功时会写入 26 条资料。

构建入口现在会加载项目根 `.env`。上面的进程环境变量仍然可以覆盖 `.env`，适合临时验证其他模型或目录。

### 6.4 验证持久化索引可被新进程读取

```powershell
$env:HF_HUB_OFFLINE = "1"
.\.venv\Scripts\python.exe -c "from knowledge_base.retriever import retrieve; hits=retrieve('分支A路由器和交换机之间不通', top_k=4); print(len(hits)); print([h['title'] for h in hits])"
```

本机实测返回 4 条结果，首批标题包括：

- `VLAN间路由缺失子接口或SVI导致跨VLAN不通`
- `交换机端口未加入正确VLAN导致终端二层不通`
- `Trunk链路未放行所需VLAN导致跨交换机VLAN不通`
- `OSPF邻居状态异常导致路由学习不完整`

只有在缓存已经完整下载后才能启用 `HF_HUB_OFFLINE=1`。缓存缺失时该变量会让程序直接报错，而不会发起下载。

当前 `FastEmbedEmbeddingFunction.__call__` 对文档和查询都调用 `embed()`。BGE 类模型通常
建议查询侧使用 `query_embed()`，可进一步改善短问题对长文档的召回。该改动不是运行前提，
但修改后应比较召回结果并重建索引。

## 7. 启动 Web 开发服务

在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -m webapp.main
```

也可以在调试时使用 Uvicorn：

```powershell
.\.venv\Scripts\python.exe -m uvicorn webapp.main:app --host 127.0.0.1 --port 8000 --reload
```

服务启动时会自动初始化 SQLite、在数据库为空时加载设备台账、检查 Chroma 知识库，并在后台执行
一次 SSH + SNMP + 连通性采集。知识库缺失或为空不会阻止服务启动；运行状态会标记为 `degraded`，
诊断降级为仅使用设备证据。采集状态单独写入 `system_status.startup_collection`，不会阻塞 Web 服务。

开发时建议：

- 使用 `--reload` 获得代码热重载。
- 使用单个 worker，不要同时开多个进程写同一个本地 Chroma 目录。
- 用浏览器访问 `http://127.0.0.1:8000`，输入 `APP_API_TOKEN` 登录。
- 默认只监听本机。只有确实需要局域网访问时才改为 `0.0.0.0`，并确认防火墙和鉴权策略。

如果端口被占用，优先改用另一个开发端口：

```powershell
.\.venv\Scripts\python.exe -m uvicorn webapp.main:app --host 127.0.0.1 --port 8001 --reload
```

### 7.0 用 VS Code 断点调试

安装 Microsoft 的 Python 与 Python Debugger 扩展后，在项目根创建 `.vscode\launch.json`：

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "FastAPI: debug",
      "type": "debugpy",
      "request": "launch",
      "module": "uvicorn",
      "args": [
        "webapp.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8000"
      ],
      "envFile": "${workspaceFolder}/.env",
      "cwd": "${workspaceFolder}",
      "justMyCode": true
    },
    {
      "name": "pytest: quick tests",
      "type": "debugpy",
      "request": "launch",
      "module": "pytest",
      "args": ["-q"],
      "envFile": "${workspaceFolder}/.env",
      "cwd": "${workspaceFolder}",
      "justMyCode": true
    }
  ]
}
```

调试时先不要使用 `--reload`，因为在断点上下文里重启子进程会增加状态丢失和端口占用的概率。确认断点工作后，再用命令行 `--reload` 做热重载开发。

推荐断点位置：

- `knowledge_base.build_index.build_index`：确认读取的 `ARTICLES_PATH` 与 `CHROMA_PATH`。
- `knowledge_base.embedding.FastEmbedEmbeddingFunction.__call__`：确认首次调用时才初始化模型。
- `knowledge_base.retriever.retrieve`：检查 `_collection`、`top_k` 和返回的文档 ID。
- `rag_engine.diagnosis_engine.diagnose`：分别观察 `use_kb=false/true` 时检索与 LLM 调用。
- `webapp.security.TokenAuthMiddleware.dispatch`：只在排查鉴权时使用，避免把真实令牌打印到日志。

### 7.1 用 PowerShell 冒烟测试 API

先设置与 `.env` 相同的令牌：

```powershell
$token = "你的实际APP_API_TOKEN"
$headers = @{ "X-API-Token" = $token }
Invoke-RestMethod "http://127.0.0.1:8000/api/diagnoses" -Headers $headers
```

测试诊断接口：

```powershell
$body = @{
    question = "分支A路由器和交换机之间不通，请排查"
    use_kb = $true
} | ConvertTo-Json

Invoke-RestMethod `
    -Method Post `
    -Uri "http://127.0.0.1:8000/api/diagnose" `
    -Headers $headers `
    -ContentType "application/json; charset=utf-8" `
    -Body $body
```

该请求会调用真实 LLM，需要有效的 `LLM_API_KEY`。调试检索问题时先把 `use_kb` 设为 `$false`，可以隔离知识库链路。

## 8. 设备网络与采集

### 8.1 测试连通性

用 PowerShell 测 ICMP：

```powershell
Test-Connection 192.168.249.11 -Count 2
```

测 SSH 端口：

```powershell
Test-NetConnection 192.168.249.11 -Port 22
```

测项目自己的跨平台 ping 封装：

```powershell
.\.venv\Scripts\python.exe -c "from collector.connectivity_test import ping; print(ping('192.168.249.11'))"
```

`collector/connectivity_test.py` 第 19-23 行已经针对 Windows 使用 `ping -n`，这部分可以直接工作。

### 8.2 ZeroTier

先确认 CLI 是否存在：

```powershell
where.exe zerotier-cli
zerotier-cli info
zerotier-cli listnetworks
```

加入网络时请使用**管理员 PowerShell**：

```powershell
zerotier-cli join <你的网络ID>
```

然后在 ZeroTier Central 中授权该成员。管理网路由优先在 ZeroTier 的 Managed Routes 中配置；不要直接照搬 Linux 的 `ip route add`。Windows 本地路由问题可用以下命令检查：

```powershell
route print
Get-NetRoute
```

若 SSH 仍不通，检查 Windows 防火墙是否允许 ZeroTier 虚拟网卡流量，以及 ZeroTier 是否允许 UDP 9993。

### 8.3 执行采集

Web 服务启动时已经会自动在后台执行一次全量采集。本节命令用于手动补采或排障。

单设备 SSH：

```powershell
.\.venv\Scripts\python.exe -m collector.run_collect --devices HQ-R1
```

全量采集：

```powershell
.\.venv\Scripts\python.exe -m collector.run_collect
```

指定设备模式只执行 SSH；完整模式还会执行 SNMP 和分支终端到总部服务器的 ping 复测。

若老 Cisco IOSv 只支持 SHA-1 SSH 算法，可在 `.env` 设置：

```dotenv
SSH_LEGACY_ALGOS=true
```

## 9. 测试与调试流程

### 9.1 静态检查

```powershell
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\python.exe -m compileall -q collector control knowledge_base rag_engine webapp
```

### 9.2 快速单测

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

本项目配置默认跳过 slow 测试。本机实测结果为 `62 passed, 1 deselected`。

### 9.3 真实 embedding 回归

```powershell
$env:FASTEMBED_CACHE_PATH = (Resolve-Path models\fastembed).Path
.\.venv\Scripts\python.exe -m pytest -m slow -vv -s
```

该测试会真实构建临时 Chroma 索引并检索。首次运行需要联网下载模型；缓存完成后可加：

```powershell
$env:HF_HUB_OFFLINE = "1"
```

本次实测在缓存命中后通过：1 个 slow 测试通过，临时建库 26 条，检索返回 4 条。

### 9.4 推荐的分层排查顺序

1. **解释器是否正确**：`python -c "import sys; print(sys.executable)"` 应指向项目 `.venv`。
2. **依赖是否正确**：`python -m pip check`。
3. **纯逻辑是否正常**：`pytest -q`。
4. **embedding 是否正常**：单独初始化 `TextEmbedding`，再构建索引并检索。
5. **LLM 是否正常**：先 `use_kb=false`，再打开知识库。
6. **设备是否可达**：先 `Test-NetConnection -Port 22`，再跑单设备采集。
7. **Web 是否正常**：最后启动 Uvicorn，用 API 令牌冒烟测试。

这样可以把 embedding、LLM、设备网络和 Web 层的问题分开，避免一次改动多个变量。

## 10. 常见故障对照

| 现象 | 常见原因 | 处理 |
|---|---|---|
| `APP_API_TOKEN ... 长度不小于 16` | 没有 `.env` 或令牌太短 | 从 `.env.example` 复制并设置长令牌 |
| 使用示例配置后无法启动 | `.env.example` 使用 `CHANGE_ME`，长度不足 | 生成随机令牌并写入项目 `.env` |
| 读取不到配置 | 项目根没有 `.env` | 从 `.env.example` 复制到项目根并填写 |
| `ModuleNotFoundError: No module named 'fastembed'` | 使用了系统 Python 或未安装依赖 | 激活 `.venv` 或使用 `.venv\Scripts\python.exe` |
| `unable to open database file` | 数据目录不存在、路径不可写、路径只写了文件名 | 使用 `./data/ops.db` 或完整可写路径 |
| 模型下载卡住或超时 | Hugging Face 网络受限、代理未配置 | 检查代理，设置可信 `HF_ENDPOINT`，或预置缓存 |
| `LocalEntryNotFoundError` | 已开启离线模式但缓存不完整 | 关闭 `HF_HUB_OFFLINE`，先完整下载；或把缓存指向正确目录 |
| 提示 Windows 不支持符号链接 | Hugging Face 缓存降级为复制模式 | 警告本身不致命；可启用开发者模式，或设置 `HF_HUB_DISABLE_SYMLINKS_WARNING=1` |
| 更换模型后检索报维度/配置不匹配 | 旧 Chroma collection 仍绑定旧 embedding | 使用 `--rebuild` 重建索引 |
| 检索结果为空 | 索引没建、路径不一致或模型配置不一致 | 核对 `CHROMA_DB_PATH`，重新构建并分进程验证 |
| 中文日志乱码 | Windows 控制台编码与 Python 输出不一致 | 设置 `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8` 并执行 `chcp 65001` |
| 启动采集显示 `skipped` | 未配置设备账号/口令 | 检查 `DEVICE_USERNAME`、`DEVICE_PASSWORD`；页面服务仍可正常使用 |
| 启动采集长期 `running` | SSH/SNMP 目标不可达或超时 | 检查 ZeroTier、路由和 22/161 端口；采集在后台执行 |
| `pytest` 卡在异步 TestClient | 受限沙箱可能阻塞 asyncio 事件循环 | 鉴权测试已改为同步边界测试；完整 HTTP 集成建议在普通 Windows 终端运行 |
| `./start.sh` 无法执行 | 脚本是 Bash/Linux 实现 | 使用本文的 Python 模块命令 |
| ZeroTier 命令失败 | 未使用管理员 PowerShell 或 CLI 不在 PATH | 提权运行，先用 `where.exe zerotier-cli` 定位 |

## 11. 建议的代码/脚本后续优化

这些不是本次运行的必要前置条件，但能减少后续踩坑：

1. 增加 `collect.ps1`，统一使用 `.venv\Scripts\python.exe`，替代 Bash 采集脚本。
2. Windows 备份文件改用 NTFS ACL 限制访问，不要只依赖 `os.chmod(0o600)`。
3. 在 CI 增加 `windows-latest` 的导入、单测和 embedding smoke job，至少覆盖路径、编码和 socket 行为。
4. 按 [TODO.md](TODO.md) 增加知识库状态页面、初始化/重建接口和文章管理。

## 12. 参考资料

- [Python 3.12 venv：Windows 激活与虚拟环境说明](https://docs.python.org/3.12/library/venv.html)
- [Python 3.12 on Windows：UTF-8 Mode 与 `PYTHONUTF8`](https://docs.python.org/3.12/using/windows.html)
- [python-dotenv：`load_dotenv` 查找顺序与显式路径](https://github.com/theskumar/python-dotenv)
- [FastEmbed Supported Models：确认 `BAAI/bge-small-zh-v1.5` 与 512 维](https://qdrant.github.io/fastembed/examples/Supported_Models/)
- [FastEmbed Getting Started：`TextEmbedding` 首次下载与初始化](https://qdrant.github.io/fastembed/Getting%20Started/)
- [Hugging Face Hub Cache：Windows 符号链接限制与缓存模式](https://huggingface.co/docs/huggingface_hub/en/guides/manage-cache)
- [Hugging Face 环境变量：`HF_HUB_OFFLINE`、`HF_HOME`](https://huggingface.co/docs/huggingface_hub/package_reference/environment_variables)
- [Chroma Python Client：`PersistentClient`](https://docs.trychroma.com/reference/python)
- [ONNX Runtime：Windows 安装与运行要求](https://onnxruntime.ai/docs/install/)
- [ZeroTier CLI：Windows 需要管理员命令提示符](https://docs.zerotier.com/cli/)
- [ZeroTier 网络配置：Windows 防火墙与 UDP 9993](https://docs.zerotier.com/routertips/)
- [Microsoft `Test-NetConnection`](https://learn.microsoft.com/en-us/powershell/module/nettcpip/test-netconnection)
- [FastAPI/Uvicorn：开发时使用 `--reload`](https://fastapi.tiangolo.com/deployment/manually/)
