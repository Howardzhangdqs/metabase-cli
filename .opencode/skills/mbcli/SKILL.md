---
name: Metabase CLI (mbcli)
description: Use when the task needs Metabase data — 浏览库/表/字段元数据、执行只读 SQL、检索已保存的卡片/集合/仪表盘、生成注入 LLM prompt 的 schema 上下文。通过本 skill 自带的单文件 mbcli.py 运行，默认 JSON 输出、稳定退出码。
---

# mbcli — 面向 Agent 的 Metabase CLI

单文件脚本 `mbcli.py` 就在本目录（SKILL.md 旁）。**所有路径相对本 skill 根目录**。

## 0. 运行器（统一用这个）

```bash
# zsh 安全：shell 函数（旧的 MB="uv run …"; $MB … 形式在 zsh 下 exit 127，勿再用）
mb() { uv run --no-project --with typer --with httpx python mbcli.py "$@"; }
mb --help
```

非交互/一次性场景（不便定义函数时）用原样参数形式：

```bash
uv run --no-project --with typer --with httpx python mbcli.py --help
```

若 `mbcli.py` 缺失（clone 后未带）：`curl -LO https://github.com/Howardzhangdqs/metabase-cli/releases/latest/download/mbcli.py`
或在仓库内 `uv run python scripts/build-single.py && cp mbcli.py .opencode/skills/mbcli/`。

## 1. 连接（一次性）

凭据**永不明文写进对话或文件**，三选一：

```bash
# a) 环境变量（推荐，适合 agent 沙箱）
export MB_URL=… MB_USERNAME=… MB_PASSWORD=…

# b) connect 持久化（此后所有会话零参数）
mb connect --default-db quant_data --non-interactive    # 参数/环境变量/交互补问，逐字段生效
mb disconnect                                           # 清除

# c) API Key: export MB_API_KEY=…（v49+，优先于账密）
```

`health` 无需凭据：`mb --url $MB_URL health`。

## 2. 命令速查

```
databases list                          # 全部数据源
schemas --db D / tables --db D [--match 子串] [--include-hidden]
fields --table T [--db D]               # 含 FK 目标、语义类型
fields --search 列名子串 --db D         # 跨表列搜索（零额外请求；reserved=true 标记保留字列）
query --db D --native "SELECT …" [--param k=v]… [--format json|csv|ndjson|table|markdown]
                                        #   [--max-rows N(默认1000,0=不限)] [--out F] [--allow-write]
cards run <id> [--param k=v]…           # 执行已保存问题
cards show <id>                         # 回显其 SQL / MBQL
collections list|show <id|root> [--model card|dashboard]…
dashboards show <id>                    # 结构（卡片清单+参数定义）
search "关键词" [--model card|table]… [--limit N]        # --limit 0=拉全集(页100,上限1000)
search "kw1,kw2" --match-any            # 多关键词并集，按 (model,id) 去重合并
context --db D [--table T]…             # ★ 生成 markdown schema 摘要（直接可注入 prompt）
freshness --db D [--table T]… [--sample]  # ★ 每表时间列 min/max（指纹优先，零查询；--sample 才发 MIN/MAX）
overview [--top 10]                       # ★ 实例总览：库×表数、集合内容分布、最近更新
printf 'tables --db 15 --match fut\n…' | mbcli batch   # 单进程批处理（NDJSON 行输出，共享会话省冷启动）
```

记录数组型命令统一支持 `--format json|csv|ndjson|table|markdown`（markdown = 管道表，键名做表头）。

v0.2 新命令占位（已注册、尚未实现，调用返回 `not_implemented` exit 1）：`freshness`（数据新鲜度）/ `overview`（实例总览）/ `batch`（stdin 多命令）。

`--db` 可用 id 或名称；省略时回退 default_db（connect --default-db 持久化的值）。

## 3. Agent 契约（务必遵守的解读规则）

- **stdout 只有数据**（默认 JSON）；诊断/错误在 stderr（单行 JSON `{"error":{"code","message","detail"}}`）。`-v` 请求日志也固定走 stderr，不会污染 stdout 数据流。
- **退出码**：`0` 成功；`1` API/执行错误（auth_failed / bad_query / not_found / timeout… / not_implemented）；`2` 用法错误（invalid_args / write_blocked——加 `--allow-write` 可解除，但见 §4）。
- 查询信封：`{"status","row_count","rows_returned","truncated","columns","rows","running_time_ms","cached","data_warnings"}`——`truncated:true` 时需改写 SQL 加 LIMIT 或提高 `--max-rows`。
- 大结果：SQL 自带 LIMIT（首选）→ `--max-rows` → `--out`/`ndjson`。
- `estimated_row_count` 是 Metabase 同步估值，**可失真数个数量级**，仅作参考。
- `search --db` 只过滤当前返回页（服务端不支持按库过滤）；`search --limit 0` 拉全集时若命中 1000 行硬上限，输出尾会带 `truncated` 标注。
- MySQL 保留字列（如 `date`/`order`/`key`/`year_month`）：`fields --search` 会标 `reserved:true`；bad_query 报错命中保留字时 detail 带"请用反引号包裹"提示。自生成 SQL 一律反引号标识符。

## 4. 安全纪律

- 默认只读：`SELECT/WITH/SHOW/DESCRIBE/EXPLAIN` 白名单之外一律被客户端拦截（exit 2）。**没有用户明确要求，永远不要加 `--allow-write`**。
- 测试实例疑似生产库：只跑 `SELECT … LIMIT` 级别查询，不逐表全量扫描。
- 不要把 `MB_PASSWORD`/API Key 写进任何文件、日志或回复。

## 5. 推荐工作流

1. 认知：`databases list` → `tables --db <库> --match <主题>` → `fields --table <表>`
2. 验证：`query --native "SELECT * FROM <表> LIMIT 3"` 摸清列含义
3. 取数：写正式 SQL（显式 LIMIT），`--format csv --out` 导出
4. 找资产：`search 关键词` → `cards show <id>` 复用已验证 SQL
5. 注入上下文：`context --db <库> --table <相关表>…` 输出直接贴进系统 prompt

更多测试任务样例见本目录 `examples.md`；完整手册见仓库 USAGE.md。
