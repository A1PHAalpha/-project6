# 前端开发

## 1. 定位与入口

React 控制台是默认业务前端，生产构建由 FastAPI 托管在 `/console/`。旧版 Jinja 页面
保留在原有 URL，便于逐页迁移。

主要页面：

| 页面 | 路由 |
|---|---|
| 总览 | `/console/overview` |
| 设备与采集 | `/console/assets` |
| 诊断中心 | `/console/diagnosis` |
| 运维知识库 | `/console/knowledge` |
| 设置 | `/console/settings` |

## 2. 组织原则

前端按功能域组织，而不是按文件类型堆放：

```text
frontend/src/
  app/          应用入口、路由、Provider 和全局壳
  features/     总览、设备、诊断、知识和设置
  shared/       API client、通用 UI、状态、类型、Mock 和样式
```

页面负责组合功能域组件；业务组件负责交互和状态；共享组件只表达通用的视觉与行为。
大型页面应拆分为数据查询、业务组合和展示层，避免单文件同时承担全部职责。

## 3. 数据访问

所有请求经过 `shared/api` 中统一的 typed client。组件不得直接拼接 URL 或调用
`fetch`。

- Vite 开发服务器默认使用 Mock 数据。
- 直接执行普通 Vite 构建时默认使用真实 `/api/v1`；`python start.py` 会显式构建 Mock。
- 页头用状态标签明确区分 Mock 和实时数据。
- Mock 只替换数据来源，不改变页面交互和路由。

开启真实 API：

```powershell
$env:VITE_USE_MOCK_API="false"
Set-Location frontend
npm run dev
```

## 4. 本地开发

```powershell
Set-Location frontend
npm install
npm run dev
```

Vite 默认在 `http://127.0.0.1:5173` 启动，并把 `/api`、`/login`、`/logout`
代理到 `http://127.0.0.1:8000`。

生产构建：

```powershell
Set-Location frontend
npm run build
```

构建产物位于 `frontend/dist/`。项目根入口会负责构建和托管，通常不需要手工启动
预览服务。

## 5. 视觉系统

控制台采用深色运维工作台：

- 青色只用于主操作、焦点和链接；
- 绿色表示可达、成功或通过；
- 黄色表示部分成功、待确认或需人工；
- 红色表示失败、不可达或高风险；
- 灰色表示未采集或未知。

状态必须同时通过文字、图标或时间表达，不能只依赖颜色。未采集不等于故障，设备事实必须
带采集时间和来源。视觉基线、交互说明和参考预览见 [DESIGN.md](DESIGN.md)。

## 6. 安全交互

- 诊断内容只提供建议，不自动执行命令。
- 打开终端前必须确认当前配置已备份。
- 写命令需要再次确认。
- 终端操作、输出和错误进入后端审计，不在前端持久化。
- “缺少证据”和“需要补充信息”必须作为正常状态展示。

## 7. 测试

```powershell
Set-Location frontend
npm test
npm run build
npm run test:e2e -- --project=desktop-chrome
npm run test:e2e -- --project=mobile-chrome
```

Vitest 覆盖共享状态、拓扑布局、终端提醒和关键交互。Playwright 使用本机 Chrome，
覆盖桌面与移动端的主要业务流程。
