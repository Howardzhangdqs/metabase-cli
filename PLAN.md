# Metabase CLI（面向 Agent 的查询工具）— 项目计划

> 状态：**规划中，未开工**。本文件是唯一交付物，尚未编写任何代码。

## 1. 项目定位

一个用 **Python** 编写的命令行工具，面向 **AI Agent / 自动化脚本**，通过 **Metabase REST API** 直接操作 Metabase：

- 浏览元数据（库 / schema / 表 / 字段）
- 执行原生 SQL 查询并取回结果
- 检索与查看保存的问题、集合、仪表盘
- 一键生成适合注入 LLM prompt 的 schema 上下文

设计第一原则：**Agent 友好** —— 默认 JSON 输出、稳定退出码、机器可读的错误信息、有限且可组合的命令集。

## 2. 测试环境（已验证可用）

| 项 | 值 |
|---|---|
| 实例 | `https://metabase.example.com`（真实地址经 `.env`/环境变量注入，不入库） |
| 账号 | `user@example.com`（同上，不入库） |
| 密码 | 经 `MB_PASSWORD` 环境变量注入；**绝不硬编码进代码或文档** |
| 健康检查 | `/api/health` → `{"status":"ok"}` ✅ |
| 登录 | `POST /api/session` 成功 ✅ |
| 账号权限 | 非超级用户（group_ids=[1,10]），可创建查询与原生查询 |
| 数据源 | 24 个 MySQL 库：`bi_data`、`quant_data`、`spiders_data`、`sw_data` 等，以及 13 个中文行业库（医药健康数据库、智能汽车、消费娱乐数据库…） |

凭据将存放于 `.env` / 环境变量，**绝不硬编码进代码**；`.env` 加入 `.gitignore`。

API 参考文档：https://www.metabase.com/docs/latest/api

## 3. 技术选型

| 项 | 选择 | 理由 |
|---|---|---|
| Python | 3.10+ | match 语法、现代 typing |
| HTTP | `httpx` | 超时控制、连接复用、同步用法简单 |
| CLI 框架 | `typer` + `rich`（可选降级为 `argparse`） | 声明式命令、自动帮助；若追求零重依赖可换 argparse |
| 配置 | 环境变量 `MB_URL` / `MB_USERNAME` / `MB_PASSWORD` / `MB_API_KEY`；可选 `~/.config/mbcli/config.toml` | 12-factor，便于 CI/agent 沙箱注入 |
| 打包 | `pyproject.toml`，支持 `pipx install` / `uv tool install` | 单命令安装为 `mbcli` 可执行 |

## 4. 认证方案

1. **优先 API Key**：若设置了 `MB_API_KEY`，走 `X-API-KEY` 头（Metabase ≥46 支持，免登录态管理）。
2. **回退 session token**：`POST /api/session` 用账号密码换取 id，缓存到本地文件（`~/.cache/mbcli/session`，0600 权限）；收到 401 自动重登一次。
3. 会话缓存默认有效期按 Metabase `session-duration` 处理，失效即刷新，对调用方透明。

## 5. 命令设计（命令名暂定 `mbcli`）

### Phase 1 — 元数据浏览（Agent 认知库结构）
```
mbcli databases list                        # 库列表（id、名称、引擎）
mbcli schemas --db <id|name>                # schema 列表
mbcli tables   --db <id|name> [--schema S]  # 表列表（行数、描述）
mbcli fields   --table <id|name> [--db D]   # 字段详情：类型、语义类型、FK 目标、描述
```

### Phase 2 — 查询执行（核心能力）
```
mbcli query --db <id|name> --native "SELECT ..." \
            [--format json|csv|table] [--max-rows 1000] [--timeout 60] [--out FILE]
mbcli card run <card-id> [--format ...]     # 执行已保存的问题
mbcli dashboard show <id>                   # 仪表盘结构（卡片清单 + 参数定义）
```

### Phase 3 — 内容检索（找到已有资产）
```
mbcli collections list
mbcli cards list [--collection <id>] [--db <id>]
mbcli cards show <card-id>                  # 含底层 SQL / 问题是 MBQL 还是 native
mbcli search "关键词"                       # 全局搜索，Agent 定位资产的入口
```

### Phase 4 — Agent 增强（差异化价值）
```
mbcli context [--db <id|name>] [--table T ...]   # 生成 LLM 友好的 schema 摘要
                                                   # （建表语句风格：字段/类型/注释/FK，含行数与样例可选）
mbcli mcp                                          # （远期可选）以 MCP server 形式暴露同等能力
```

## 6. Agent 友好性规范

- **默认输出 JSON**（stdout）；`--format csv` 导出、`--format table` 人读（终端调试用）。
- **退出码契约**：`0` 成功；`1` API/执行错误；`2` 用法/参数错误。错误以 JSON 写 stderr：`{"error": {"code": ..., "message": ..., "detail": ...}}`。
- `--fields a,b` 列裁剪；`--jq <expr>`（可选）服务端外轻量变换。
- 默认 `--max-rows`（如 1000）防结果爆炸，`--max-rows 0` 解除；大结果走流式写文件。
- 幂等只读优先：所有浏览类命令纯 GET；`query` 仅执行 native SELECT（默认拒绝非 SELECT 语句，`--allow-write` 显式放行）。

## 7. 项目结构（规划）

```
~/metabase-cli/
├── PLAN.md               # 本文件
├── README.md             # 待开工后编写
├── pyproject.toml
├── .env.example          # MB_URL / MB_USERNAME / MB_PASSWORD / MB_API_KEY
├── src/mbcli/
│   ├── __init__.py
│   ├── cli.py            # typer 入口与子命令注册
│   ├── client.py         # Metabase API 客户端：会话、重试、错误规范化
│   ├── auth.py           # API key / session 缓存与刷新
│   ├── output.py         # json/csv/table 格式化、行数限制
│   ├── models.py         # 输出 dataclass（稳定 JSON 字段名）
│   └── commands/
│       ├── databases.py  ├── tables.py  ├── fields.py
│       ├── query.py      ├── cards.py   ├── dashboards.py
│       └── search.py / context.py
└── tests/
    ├── conftest.py       # 测试环境 fixture（无网络时自动 skip 集成测试）
    ├── test_client.py    # 单元：认证、错误规范化（mock）
    └── test_live.py      # 集成：对测试实例实测（只读 SELECT，凭据经环境变量）
```

## 8. API 端点映射（基于官方 docs/latest/api）

| 功能 | 端点 |
|---|---|
| 登录 | `POST /api/session` |
| 当前用户 | `GET /api/user/current` |
| 库列表/元数据 | `GET /api/database`、`GET /api/database/{id}/metadata` |
| 执行 native 查询 | `POST /api/dataset/native`（或 `POST /api/dataset` 包 `native`） |
| 执行保存的问题 | `POST /api/card/{id}/query` |
| 问题/集合/仪表盘 | `GET /api/card`、`GET /api/collection`、`GET /api/dashboard/{id}` |
| 全局搜索 | `GET /api/search` |
| 健康检查 | `GET /api/health` |

> 开工第一步：用实测响应核对字段结构（该实例为较新版 Metabase，具体响应以实测为准），在 `models.py` 固化。

## 9. 里程碑与验收

| 里程碑 | 内容 | 验收标准 |
|---|---|---|
| **M0** 脚手架 | pyproject + client + auth + `databases list` | 对测试环境跑通，输出 24 个库的 JSON |
| **M1** 元数据 | schemas / tables / fields | 任选一库（如 `quant_data`）完整列出表与字段 |
| **M2** 查询 | `query` / `card run` + 三种格式 + 行数限制 | 对 MySQL 库执行真实 SELECT，JSON/CSV 导出正确；退出码契约测试通过 |
| **M3** 资产 | collections / cards / search / dashboard | 能按关键词搜到卡片并回显其 SQL |
| **M4** 增强 | `context` 命令 + README + 测试补全 | 生成的 schema 摘要可直接作为 LLM prompt；单测+集成全绿 |

每个里程碑在开发分支进行，实测通过后合并。

## 10. 风险与注意事项

- **只读纪律**：测试环境疑似生产实例（24 个真实业务库），集成测试仅跑 `SELECT 1` / `LIMIT` 查询；写操作一律不碰。
- **权限边界**：账号非管理员，部分管理端点（用户管理、设置）不可用属预期，工具不依赖管理员权限。
- **大结果集**：内存控制（流式落盘），避免 agent 一次拉爆。
- **API 版本差异**：以实测响应为准做字段兼容，不做过度抽象。
- **凭据安全**：`.env` + `.gitignore`；session 缓存文件 0600。
