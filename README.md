# 企业网络智能运维诊断系统

基于 RAG（检索增强生成）的企业网络故障诊断系统：采集设备真实状态作为证据，
检索运维知识库作为参考，调用大模型生成结构化诊断建议，所有可能影响设备的操作
都走只读白名单或人工确认流程，不存在自动下发配置变更的代码路径。

> 本仓库是经安全加固后的净版代码仓：无明文凭据、无生成产物、依赖精简（去 PyTorch）、
> 带 CI（lint / 单测 / 密钥扫描）。

---

## 安全说明（重要）

- **凭据只放 `.env`，永不入库**。仓库只有 `.env.example`（占位符），`.gitignore` 已排除 `.env`/`.env.*`。
- **接口鉴权**：Web 服务启用单口令 Token 鉴权，环境变量 `APP_API_TOKEN` 必填且长度 ≥ 16，
  未配置则**拒绝启动**。支持三种携带方式：
  - `Authorization: Bearer <token>`
  - `X-API-Token: <token>`
  - 登录页 `GET /login` 校验后下发的 `HttpOnly` + `SameSite=Strict` Cookie（页面内 `fetch` 自动携带）。
- **默认只绑 `127.0.0.1`**：需局域网演示时显式设置 `APP_HOST=0.0.0.0`。
- **接口文档默认关闭**：`/docs`、`/redoc`、`/openapi.json` 需显式设置 `APP_ENABLE_DOCS=true` 才开放。
- **凭据不入库**：设备采集输出与诊断证据在**写库前**统一经 `sanitize_text()` 脱敏，
  数据库内不再保留明文口令。完整设备配置仍可通过备份文件（`reports/backups/`，权限 600）追溯。
- **设备操作边界**：自动执行的设备命令必须命中只读白名单；配置变更不自动下发，
  必须经"人工确认 → 自动备份 → 人工在设备上操作 → 复测"流程。
- **SSH 采集**口令不再出现在命令行（由 `netmiko` 通过 SSH 协议传递）。
- **防复发**：CI 中的 `gitleaks` 会在每次提交/PR 时扫描密钥。

---

## 快速开始

### 一键启动

```bash
./start.sh
```

脚本会依次：连接 ZeroTier 远程网络 → 添加管理网路由 → 连接性测试 →
初始化数据库并加载设备 → 检查/构建知识库索引 → 启动 Web 应用。
按 Ctrl+C 停止服务时会询问是否断开远程网络。

> ZeroTier 网络 ID / 管理网 IP / 路由网段均从 `.env` 读取（`ZEROTIER_NETWORK_ID`、
> `EVE_MGMT_IP`、`EVE_MGMT_ROUTE_CIDR`），未配置时自动跳过 ZeroTier 接入。

### 1. 环境准备

```bash
python3 -m venv venv
./venv/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple --upgrade pip
./venv/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements/dev.txt
./venv/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements/collect.txt
```

已在 Ubuntu 24.04 + Python 3.12 验证。venv 约 550 MB（旧方案含 PyTorch 为 6.2 GB）。

### 2. 配置 `.env`

```bash
cp .env.example .env
```

关键项（**切勿把真实凭据提交到仓库**）：

- **LLM 配置**（示例为通义千问，DeepSeek 等同理替换）
  ```
  LLM_API_KEY=你的API密钥
  LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
  LLM_MODEL=qwen-plus
  ```
- **设备凭据**（真实采集/自动备份用，口令由 `.env` 提供，脚本内不出现明文）
  ```
  DEVICE_USERNAME=admin
  DEVICE_PASSWORD=change_me
  SNMP_COMMUNITY=public
  ```
- **远程网络接入**
  ```
  ZEROTIER_NETWORK_ID=...
  EVE_MGMT_IP=...
  EVE_MGMT_ROUTE_CIDR=192.168.249.0/24
  DEVICE_TEST_HOST=...
  ```
- **Web 服务与鉴权**
  ```
  APP_HOST=127.0.0.1
  APP_PORT=8000
  APP_API_TOKEN=请填一个长度>=16的随机口令
  APP_ENABLE_DOCS=false
  ```

### 3. 构建知识库索引

```bash
./venv/bin/python -m knowledge_base.build_index --rebuild
```

将 [knowledge_base/articles.json](knowledge_base/articles.json) 中的 26 篇运维资料向量化写入本地
Chroma 库（`data/chroma`，运行产物，不入库）。索引构建与查询必须使用同一 embedding 模型，
更换模型/实现后必须 `--rebuild`。

### 4. 采集设备数据

```bash
./collect.sh                     # 交互式：全部设备 / 指定设备
./venv/bin/python -m collector.run_collect                 # 全量：SSH + SNMP + 连通性复测
./venv/bin/python -m collector.run_collect --devices HQ-R1   # 仅指定设备
```

### 5. 启动 Web 界面

```bash
./venv/bin/uvicorn webapp.main:app --host 127.0.0.1 --port 8000
```

浏览器访问 `http://127.0.0.1:8000` 会跳转到登录页，输入 `APP_API_TOKEN` 登录后可以：

- 查看所有设备的最新采集状态
- 提交自然语言问题获得 AI 诊断
- 查看诊断历史和人工操作记录
- 导出诊断报告为 Markdown

命令行/脚本访问示例：

```bash
curl -H "X-API-Token: $APP_API_TOKEN" http://127.0.0.1:8000/api/diagnoses
```

---

## 真实演示流程（推荐）

```bash
# 第1步：连接远程网络并采集初始状态
./venv/bin/python connect_remote_network.py

# 第2步：在 EVE-NG 中手工设置故障（比如 shutdown 某个接口）

# 第3步：重新采集数据
./venv/bin/python -m collector.run_collect

# 第4步：提交诊断问题（Web 界面或命令行）
./venv/bin/python - <<'EOF'
from dotenv import load_dotenv; load_dotenv()
from rag_engine.diagnosis_engine import diagnose
print(diagnose("分支A路由器和交换机之间好像不通了", use_kb=True))
EOF

# 第5步：根据诊断建议，人工在 EVE-NG 设备上修复（no shutdown 等）

# 第6步：重新采集 + 复测连通性验证
```

## 效果测试（10 个预设场景）

```bash
# 对比知识库检索对诊断的提升效果
./venv/bin/python -m test_cases.compare_kb
```

输出写入 `test_cases/compare_results.json`（运行产物，已在 `.gitignore` 中忽略），记录
4 类故障×2 + 正常 + 证据不足 = 10 个场景的 `use_kb=True/False` 对比。

---

## 环境分层（requirements/）

| 文件 | 用途 | 内容 |
|---|---|---|
| `requirements/base.txt` | 运行环境：Web + RAG 查询 + 控制层 | fastapi/uvicorn/jinja2/pydantic/openai/netmiko/chromadb/fastembed |
| `requirements/build.txt` | 构建环境：仅构建知识库索引 | python-dotenv/chromadb/fastembed |
| `requirements/collect.txt` | 采集环境：仅设备采集 | python-dotenv/netmiko/pysnmp |
| `requirements/dev.txt` | 开发/CI | `-r base.txt` + pytest + ruff |

> 说明：索引构建与查询必须使用**同一个 embedding 与模型**，因此二者都依赖 `chromadb + fastembed`，
> 无法彻底物理隔离。分层带来的实际收益是：用 ONNX 运行时替代 PyTorch（6.2 GB → ~550 MB），
> 构建环境不含 Web 依赖、采集环境不含 Web/RAG 依赖。CI 中通过独立 job + 产物传递表达两阶段。

## Embedding 方案

- 本地向量化使用 **fastembed（ONNX Runtime） + `BAAI/bge-small-zh-v1.5`**，
  与旧 `sentence-transformers` 方案使用**同一份模型权重**，检索语义不变，仅替换运行时，去掉 PyTorch 重依赖。
- chromadb 1.5.x 已移除内置的稠密 FastEmbed 函数，故在
  [knowledge_base/embedding.py](knowledge_base/embedding.py) 中按 chromadb 的 `EmbeddingFunction`
  Protocol 自行封装 `FastEmbedEmbeddingFunction`。
- 更换 embedding 模型/实现后 collection 元数据不兼容，务必 `--rebuild`。
- 可用 `FASTEMBED_CACHE_PATH` 把模型缓存固定到仓库外目录。

---

## 目录结构与文件作用

### network/ — 网络拓扑台账
- [device_inventory.json](network/device_inventory.json)：设备清单（设备名/角色/站点/厂商/型号/管理IP/设备类型/VLAN等），
  是 `collector`、`rag_engine` 等模块识别"有哪些设备"的唯一数据源。

### collector/ — 状态采集
- [db.py](collector/db.py)：SQLite 数据层，定义 `devices`、`collection_records`、`diagnosis_records`、
  `manual_actions` 四张表及读写函数，是全系统唯一的数据持久化入口。路径由项目根推导，可用 `SQLITE_DB_PATH` 覆盖。
- [whitelist_commands.py](collector/whitelist_commands.py)：只读命令白名单。按设备类型定义允许执行的查询命令，
  以及 `is_readonly_command()` 校验函数——系统中任何自动执行命令的地方都必须先过这一层检查。
- [collector_ssh.py](collector/collector_ssh.py)：通过 **netmiko** 对路由器/交换机执行白名单 show 命令，
  写库前对输出做脱敏；口令不进命令行。
- [collector_snmp.py](collector/collector_snmp.py)：通过 SNMP（ifTable+sysUpTime）采集设备在线状态和接口状态，
  作为 SSH 采集之外的补充证据来源。
- [connectivity_test.py](collector/connectivity_test.py)：从终端对目标 IP 执行 ping 并记录结果。
- [run_collect.py](collector/run_collect.py)：采集入口。支持 `--devices A,B`；不指定则跑 SSH + SNMP + 连通性复测。

### knowledge_base/ — 运维知识库
- [articles.json](knowledge_base/articles.json)：26 篇运维排查资料（VLAN/路由/DHCP/ACL/接口故障/通用排查方法）。
- [embedding.py](knowledge_base/embedding.py)：★ fastembed(ONNX) 稠密向量化封装（chromadb EmbeddingFunction Protocol）。
- [build_index.py](knowledge_base/build_index.py)：用 `embedding.py` 将 articles.json 向量化写入 Chroma。
- [retriever.py](knowledge_base/retriever.py)：检索接口 `retrieve(query, top_k, category_filter)`。

### rag_engine/ — RAG 诊断核心
- [diagnosis_engine.py](rag_engine/diagnosis_engine.py)：诊断主流程 `diagnose(question, use_kb, top_k)`，
  串联"识别设备→取证据→脱敏→检索→组装 Prompt→调用 LLM→解析→落盘（脱敏）"。
- [device_extractor.py](rag_engine/device_extractor.py)：从自然语言问题识别站点并映射到设备；识别不到则返回全部设备。
- [evidence_builder.py](rag_engine/evidence_builder.py)：取每台设备各采集项的最新证据，区分 success/failed/no_data，
  超过 30 分钟标记为过期。
- [prompt_templates.py](rag_engine/prompt_templates.py)：诊断系统提示词与用户提示词模板（强制 JSON 输出结构）。
- [llm_client.py](rag_engine/llm_client.py)：封装 OpenAI 兼容接口调用；`safe_parse_json()` 做多级降级解析。
- [sanitize.py](rag_engine/sanitize.py)：脱敏模块，覆盖 username/password/enable secret/key-string/
  crypto isakmp key/tacacs key/snmp community 等常见敏感字段。

### control/ — 操作控制（安全边界）
- [readonly_executor.py](control/readonly_executor.py)：只读检查执行器，命令必须在白名单内才发往设备。
- [backup_manager.py](control/backup_manager.py)：变更前自动备份运行配置到 `reports/backups/`（权限 600）。
- [manual_change_workflow.py](control/manual_change_workflow.py)：人工变更流程编排（确认→备份→人工操作→复测→记录）。

### webapp/ — Web 展示层
- [main.py](webapp/main.py)：FastAPI 主入口，挂载鉴权中间件与登录路由，默认关闭接口文档。
- [security.py](webapp/security.py)：★ 单口令 Token 鉴权中间件 + 登录/登出路由。
- [report_export.py](webapp/report_export.py)：将单次诊断导出为 Markdown 报告。
- [templates/](webapp/templates/)：Jinja2 模板（`login.html` 登录页、`index.html` 总览、`device_detail.html`、`diagnose.html`）。

### tests/ — 单元测试（pytest）
- 纯函数测试：`test_sanitize.py`、`test_whitelist.py`、`test_device_extractor.py`、`test_evidence.py`、`test_llm_parse.py`。
- 鉴权测试：`test_security.py`（FastAPI TestClient）。
- 慢测试：`test_retrieve_slow.py`（标注 `@pytest.mark.slow`，需下载模型，默认跳过）。

```bash
./venv/bin/python -m pytest            # 快速、不联网、不碰设备
./venv/bin/python -m pytest -m slow    # 可选：真实建索引 + 检索回归
```

### .github/workflows/ci.yml — CI
`lint`（ruff + compileall）/ `test`（pytest + 导入冒烟）/ `secret-scan`（gitleaks）/
`build-index`（手动触发，产出 chroma 索引产物）。

### 根目录文件
- `requirements/`：四套分层依赖清单（见上）。
- [pyproject.toml](pyproject.toml)：ruff + pytest 唯一配置入口。
- [.env.example](.env.example)：环境变量模板（仅占位符）。
- [.gitignore](.gitignore)：排除 `.env`、`data/`、`venv/`、报告与备份等。
