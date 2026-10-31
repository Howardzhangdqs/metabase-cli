# mbcli 完整使用手册

> 本文对应 mbcli 的**实际实现**：所有命令、选项、默认值均逐条与 `mbcli <command> --help` 及源码核对；输出样例截取自脱敏后的真实运行（长输出以 `…` 截断）。
>
> 快速上手：`mbcli connect` 一次 → 之后所有命令零连接参数。示例统一使用占位符 `https://metabase.example.com` 与 `me@example.com`。

---

## 目录

1. [简介与核心约定](#1-简介与核心约定)
2. [安装](#2-安装)
3. [连接与认证](#3-连接与认证)
4. [选项文法](#4-选项文法)
5. [命令详解](#5-命令详解)
6. [Agent 集成指南](#6-agent-集成指南)
7. [输出格式参考](#7-输出格式参考)
8. [故障排查](#8-故障排查)
9. [FAQ](#9-faq)
10. [开发指南](#10-开发指南)

---

## 1. 简介与核心约定

mbcli 是面向 **AI Agent / 自动化脚本**的 Metabase 命令行客户端：浏览元数据、执行只读 SQL、检索已保存资产（问题/集合/仪表盘）、生成 LLM prompt 用的 schema 上下文。

核心约定（对 Agent 最重要）：

| 约定 | 说明 |
|---|---|
| **stdout = 数据** | 默认输出 JSON（`ensure_ascii=False`，中文原样）；stdout 永远只有数据；`-v` 请求日志**固定走 stderr**，绝不污染数据流 |
| **stderr = 诊断** | `-v` 日志与错误都走 stderr；错误为**单行 JSON**（见 §6） |
| **稳定退出码** | `0` 成功 / `1` API 与执行错误 / `2` 用法错误 |
| **稳定字段名** | 输出字段为 snake_case **白名单**（固化于 `models.py`），Metabase 上游新增字段不会混入输出 |
| **只读定位** | 无任何管理/写命令；`query` 默认拒绝非 SELECT 语句（词法级 write-guard） |
| **有限命令集** | 19 个命令：health / whoami / connect / disconnect / databases list / schemas / tables / fields / query / cards run / cards show / collections list / collections show / dashboards show / search / context + v0.2 新增 freshness / overview / batch（见 §5.17） |

---

## 2. 安装

需要 Python ≥ 3.10 与 [uv](https://docs.astral.sh/uv/)。

```bash
# 方式一：安装为全局工具
git clone <repo> && cd metabase-cli
uv tool install .
mbcli --help

# 方式二：源码运行（开发）
uv sync                    # 创建 .venv 并安装依赖（含 dev 组）
uv run mbcli --help
```

运行时依赖仅 `typer` + `httpx`。

### 2.1 单文件脚本（免安装）

`dist/mbcli.py` 是把整个项目合并而成的**单文件脚本**（生成物，**不入库**——`dist/` 已 gitignore，从 GitHub Releases 下载）：适合无法安装包的机器。文件头带 **PEP 723 依赖元数据**，`uv run` 自动准备依赖，零额外参数；纯 Python 运行时依赖目标机自带的库：

```bash
# 推荐：PEP 723 元数据 → uv 自动安装 typer>=0.12 / httpx>=0.27，无需 --with
uv run mbcli.py --help

# 或用目标机自带依赖（要求 Python >= 3.10 + pip install typer httpx；rich 由 typer 携带）
python3 mbcli.py connect --url https://metabase.example.com --username me@example.com
python3 mbcli.py query --native "SELECT 1 AS one"     # 与安装版用法完全等价
```

重新生成：`python3 scripts/build-single.py`（需在装有 typer/httpx 的环境运行，如 `uv run python scripts/build-single.py`；生成器自带自检——模块清单完整性、`--help`、保留字金丝雀与真实错误路径冒烟）。⚠️ `dist/mbcli.py` 是生成物，**勿手改**：请修改 `src/mbcli/` 后重新生成。

---

## 3. 连接与认证

### 3.1 connect：连一次，之后零参数

```bash
# 交互式（缺什么问什么，密码隐藏输入）
mbcli connect --url https://metabase.example.com

# 一次性给全（agent/脚本推荐，配合环境变量更佳）
MB_URL=https://metabase.example.com \
MB_USERNAME=me@example.com \
MB_PASSWORD=… \
mbcli connect --default-db quant_data --non-interactive
```

connect 成功后把连接写入 `config.toml`（默认 `~/.config/mbcli/config.toml`，文件 0600 / 目录 0700，原子写）。之后**所有命令自动取用**，无需再传任何连接参数；保存了 `--default-db` 后连 `--db` 都可以省：

```bash
mbcli query --native "SELECT 1 AS one"     # 零连接参数
mbcli tables                                # --db 也省了
```

**逐字段取值优先级**：CLI 参数 > `MB_URL/MB_USERNAME/MB_PASSWORD/MB_API_KEY` 环境变量 > 交互式补问。仅当参数与环境变量**都**没有该字段时才提示（密码 `hide_input=True`）；`--non-interactive` 仅在两者都缺失时以 exit 2 失败，永不阻塞 stdin。

**先验证后落盘**：密码模式先 `POST /api/session` 登录 + `GET /api/user/current`；API Key 模式直接 whoami。任何失败（`auth_failed` 等）**什么都不写**。

**覆盖规则**：config.toml 已存有**不同**连接（url 或身份不同）时拒绝（exit 2，提示 `--force`）；**同一连接**重连无需 `--force`（如改密码后 `mbcli connect --password 新密码`）。connect 成功会**连带删除 session 缓存**（旧 session 属于旧连接）。

**成功输出**（不含任何密码/密钥）：

```json
{"connected": true, "url": "https://metabase.example.com", "user": {"id": 42, "email": "me@example.com"}, "default_db": {"id": 15, "name": "quant_data"}, "replaced": null, "config_file": "/home/you/.config/mbcli/config.toml"}
```

`replaced` 为被覆盖的旧连接 url（无旧连接时 `null`）；`default_db` 以 **id** 落盘（对名字重名/改名免疫），给出 id+name 便于确认。

### 3.2 disconnect：忘记连接

```bash
mbcli disconnect
```

删除 config.toml 的 `[metabase]` 段（文件里没有其他内容时整个文件删除，有其他段则保留）+ 删除 session 缓存。**幂等，永远 exit 0**：

```json
{"disconnected": true, "was_connected": true, "session_cache_cleared": true}
```

### 3.3 认证机制

| 模式 | 触发 | 行为 |
|---|---|---|
| **API Key**（推荐） | `api_key` 存在（任何来源） | 每个请求附 `X-API-Key` 头；无过期概念；401 直接 `auth_failed`。要求 Metabase **v49+** |
| **Session** | 无 api_key、有账密 | `POST /api/session` 换 session id，缓存到 `MB_SESSION_CACHE`（0600 原子写，**14 天**有效）；任意非登录请求收到 401 → 自动重登一次并重放；登录自身的任何失败一律 `auth_failed`（错密码指向凭据而非参数） |

session 缓存**损坏或不可解析时静默忽略**并重登，不报错。

### 3.4 配置优先级链与环境变量

优先级：**CLI 显式参数 > `MB_*` 环境变量 > 工作目录 `.env` > `~/.config/mbcli/config.toml` > 默认值**。
（`.env` 仅接受白名单键 `MB_URL/MB_USERNAME/MB_PASSWORD/MB_API_KEY`；config.toml 仅接受 `[metabase]` 段的 `url/api_key/username/password/timeout/session_cache/default_db`。）

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `MB_URL` | 无（必填） | 实例根地址；尾斜杠自动剥离 |
| `MB_API_KEY` | 无 | 设置则优先于账密 |
| `MB_USERNAME` / `MB_PASSWORD` | 无 | 懒校验：首个需认证请求才要求（`health` 无凭据可跑） |
| `MB_TIMEOUT` | `60` | 单请求 **read** 超时秒数（字节间隔上限，非总时长） |
| `MB_SESSION_CACHE` | `~/.cache/mbcli/session.json` | session 缓存路径（0600） |
| `MB_CONFIG_DIR` | 未设置 | 配置**目录**约定：设置后 config 默认路径变为 `$MB_CONFIG_DIR/mbcli.toml`（connect 写入/读取都跟随；`MB_CONFIG_FILE` 显式文件仍优先） |
| `MB_CONFIG_FILE` | `~/.config/mbcli/config.toml` | connect 写入/读取的配置文件；多实例/沙箱用它切换（优先级高于 MB_CONFIG_DIR） |
| `MB_DEFAULT_DB` | 无 | `--db` 缺省回退值（id 或 name）；优先级 `--db` > 本变量 > config.toml `default_db` |

### 3.5 凭据安全建议

- **优先 `MB_*` 环境变量或交互式隐藏输入**；避免 `--password`/`--api-key` 命令行参数——argv 会进入 shell history，且 `ps` 进程列表可见。
- 任何输出与 `-v` 日志**永不回显** password/api_key（日志脱敏为 `***`，认证头从不记录；登录 body 记为 `{"username": "…", "password": "***"}`）。
- ⚠️ 密码经 connect 落盘是**明文**（文件 0600、目录 0700）。更安全：在 Metabase 管理界面生成 **API Key** 后 `mbcli connect --api-key …`。

---

## 4. 选项文法

typer 的根级选项只能出现在子命令**之前**：

- **根级（连接/诊断）**：`--url` `--api-key` `--username` `--password` `--timeout` `--no-color` `-v/--verbose`
- **命令级（数据塑形）**：`--format` `--fields` `--max-rows` `--out` 及各命令专属选项

```bash
# ✅ 正确：根级在前，命令级在后
mbcli --url https://metabase.example.com query --db 15 --native "SELECT 1" --format csv --max-rows 100

# ❌ 错误：根级选项放在子命令之后 → exit 2, code=invalid_args
mbcli query --db 15 --url https://metabase.example.com --native "SELECT 1"
```

---

## 5. 命令详解

> 通用命令级选项（下表省略重复说明）：
> `--format <json|ndjson|csv|table|markdown>`（默认 json）、`--fields a,b`（裁剪记录键，拼错报 `invalid_args`，**绝不裁剪查询信封**）、`--out FILE`（结果落盘，stdout 变摘要 `{"out":"…","rows":N,"truncated":bool}`）、`--max-rows`（仅查询类，默认 1000，`0` = 不限，客户端截断）。
> 记录数组型命令五种格式全支持；collections show 的 csv/ndjson/table/markdown 渲染其 `items[]`（json 保持分页信封）；单对象命令（health/whoami/cards show/dashboards show）支持 json/table/markdown，选 `csv`/`ndjson` → `invalid_args` (exit 2)。

### 5.1 `mbcli health`

实例健康检查；无需任何凭据。

```
mbcli health
```

| 选项 | 类型 | 默认 | 说明 |
|---|---|---|---|
| （无） | | | 仅根级选项可用 |

```bash
mbcli --url https://metabase.example.com health
mbcli health          # connect 过之后
```

```json
{"status": "ok", "version": "v0.66.0-SNAPSHOT"}
```

可能错误码：`config_missing`（无 URL）、`unhealthy`（状态非 ok，含 503 initializing，body 进 detail）、`network_error` / `timeout` / `server_error`。

### 5.2 `mbcli whoami`

当前认证用户（校验凭据最快的方式）。

```
mbcli whoami [--format FMT] [--out FILE]
```

| 选项 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `--format` | json/ndjson/csv/table | json | 单对象输出，实际支持 json/table |
| `--out` | 路径 | 无 | 结果写文件，stdout 变摘要 |

```bash
mbcli whoami
mbcli whoami --format table
```

```json
{"id": 42, "email": "me@example.com", "is_superuser": false, "group_ids": [1, 10]}
```

可能错误码：`config_missing`（无 URL）、`config_missing`（无凭据——懒校验在此触发）、`auth_failed`、`network_error`、`timeout`。

### 5.3 `mbcli connect`

校验并持久化连接（详见 §3.1）。

```
mbcli connect [--url U] [--username U] [--password P] [--api-key K]
              [--default-db <id|name>] [--non-interactive] [--force]
```

| 选项 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `--url` | str | 无（可由 env/补问） | 实例根地址 |
| `--username` | str | 无 | 登录用户名 |
| `--password` | str | 无 | 登录密码（缺失时隐藏输入补问；建议用 env 而非 argv） |
| `--api-key` | str | 无 | API Key，优先于账密 |
| `--default-db` | str | 无 | 缺省库（id 或 name，经 `GET /api/database` 校验，**以 id 落盘**） |
| `--non-interactive` | flag | 关 | 缺任何字段直接 exit 2，永不提示 |
| `--force` | flag | 关 | 允许覆盖一个**不同**的旧连接 |

```bash
mbcli connect --url https://metabase.example.com --username me@example.com \
              --default-db quant_data          # 只问密码（隐藏输入）
mbcli connect --non-interactive                 # 全靠 MB_* env；缺则 exit 2
mbcli connect --password 新密码                  # 同连接改密重连，无需 --force
```

输出样例见 §3.1。可能错误码：`invalid_args`（缺字段/需 `--force`）、`auth_failed`（登录或 whoami 失败）、`not_found` / `ambiguous_ref`（default-db 校验）、`network_error`、`timeout`。

### 5.4 `mbcli disconnect`

忘记已保存的连接（详见 §3.2）。无选项；幂等；永远 exit 0。

```
mbcli disconnect
```

```bash
mbcli disconnect
MB_CONFIG_FILE=/tmp/other.toml mbcli disconnect   # 操作另一份配置文件
```

```json
{"disconnected": true, "was_connected": false, "session_cache_cleared": false}
```

可能错误码：（无——始终 exit 0）。

### 5.5 `mbcli databases list`

列出全部数据库。该端点**无分页**，一次全量。

```
mbcli databases list [--format FMT] [--fields KEYS] [--out FILE]
```

| 选项 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `--format` / `--fields` / `--out` | | | 见本节开头通用说明 |

```bash
mbcli databases list
mbcli databases list --fields id,name --format csv
```

```json
[{"id": 7, "name": "bi_data", "engine": "mysql", "description": null, "is_sample": false, "initial_sync_status": "complete", "timezone": "Asia/Shanghai", "dbms_version": {"flavor": "MySQL", "version": "8.0.33"}}, …]
```

可能错误码：`config_missing`、`auth_failed`、`network_error`、`timeout`、`server_error`。

### 5.6 `mbcli schemas`

数据库的 schema 去重列表（MySQL 通常只有 `""`）。

```
mbcli schemas [--db <id|name>] [--format FMT] [--fields KEYS] [--out FILE]
```

| 选项 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `--db` | str | 无 | 库 id 或 name；省略时回退 `MB_DEFAULT_DB` > config `default_db`（三者皆无 → exit 2） |

```bash
mbcli schemas --db quant_data
mbcli schemas                                  # 用 connect --default-db 存的缺省库
```

```json
[{"name": "", "table_count": 328}]
```

可能错误码：`invalid_args`（--db 全缺）、`not_found` / `ambiguous_ref`（名字解析）、`auth_failed`、`network_error`、`timeout`。

### 5.7 `mbcli tables`

库内表列表（一次 metadata 调用，进程内缓存）。

```
mbcli tables [--db <id|name>] [--schema S] [--match SUBSTR] [--include-hidden]
             [--format FMT] [--fields KEYS] [--out FILE]
```

| 选项 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `--db` | str | 无 | 同 schemas 的回退链 |
| `--schema` | str | 无 | 按 schema 名过滤 |
| `--match` | str | 无 | 表名子串过滤（大小写不敏感）。**仅匹配物理表名 `name`**——不匹配中文描述/`display_name`；按语义找表请用 `search --model table` |
| `--include-hidden` | flag | 关 | 默认隐藏 `active=false` 或带 `visibility_type` 的表 |

```bash
mbcli tables --db quant_data --match mau
mbcli tables --db 15 --fields id,name,estimated_row_count --format csv
```

```json
[{"id": 3258, "schema": "", "name": "app_mau", "display_name": "app_mau", "description": null, "estimated_row_count": null, "active": true, "field_count": 4}, …]
```

⚠️ `estimated_row_count` 是 Metabase 同步**估值**，本实例可差多个数量级（`null` 亦常见），仅作排序/筛选用。可能错误码：同 schemas，另加 `invalid_response`。

### 5.8 `mbcli fields`

表字段明细（`GET /api/table/{id}/query_metadata`，FK target 已水合），或跨表列名搜索（v0.2）。

```
mbcli fields --table <id|name> [--db <id|name>] [--include-hidden]
            [--format FMT] [--fields KEYS] [--out FILE]
mbcli fields --search <子串> --db <id|name> [--include-hidden]
            [--format FMT] [--fields KEYS] [--out FILE]
```

| 选项 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `--table` | str | — | 表 id 或 name；name 需配合 `--db`（或缺省库）。与 `--search` 互斥 |
| `--search` | str | — | **跨表列名子串搜索**（大小写不敏感）；需 `--db`（或缺省库）。扫 metadata 全表字段，**零额外请求**；默认不含隐藏表（`--include-hidden` 放开） |
| `--db` | str | 无 | name/列搜索解析用；省略时走缺省库回退；**纯数字表 id 无需任何 db** |
| `--include-hidden` | flag | 关 | 包含隐藏字段/隐藏表 |

两者都缺、或同时给出 → `invalid_args`（exit 2）。

```bash
mbcli fields --table app_mau --db quant_data
mbcli fields --table 3258                # 数字 id：不需要 --db
mbcli fields --search year_month --db 15 # 哪些表有该列
mbcli fields --search phone --db 15 --format markdown
```

`--table` 输出（键序即此顺序）：

```json
[{"id": 37677, "name": "date", "display_name": "date", "base_type": "type/Text", "semantic_type": null, "database_type": "VARCHAR", "description": null, "is_pk": false, "is_nullable": true, "is_indexed": null, "fk_target": null, "position": 0}, …]
```

`--search` 输出（v0.2 §13.4；`fk_target` 经 metadata 内 `fk_target_field_id` 反查，同响应内解析）：

```json
[{"table": "tab_x", "table_id": 3301, "column": "year_month", "base_type": "type/Text", "semantic_type": null, "is_pk": false, "fk_target": null, "reserved": true}, …]
```

`reserved:true` 表示列名命中 **MySQL 8.0 保留字表**（`date`/`order`/`key`/`year_month`/`rank`…）——写 SQL 时请用反引号包裹，如 `` SELECT `year_month` FROM tab_x ``。`fk_target` 形如 `{"table": "users", "field": "id"}`；`position`（--table 模式）是**物理列序**（`database_position`）。可能错误码：`invalid_args`（name 无任何 db / --table 与 --search 互斥）、`not_found`、`ambiguous_ref`、`auth_failed`、`network_error`、`timeout`。

### 5.9 `mbcli query` ⭐

执行原生 SQL（`POST /api/dataset`）。默认只读；**限量是客户端截断**（服务端忽略一切请求级限量）。

```
mbcli query --native SQL [--db <id|name>] [--param k=v]… [--allow-write]
           [--format FMT] [--max-rows N] [--out FILE] [--fields KEYS]
```

| 选项 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `--native` | str | **必填** | SQL 文本；写保护在任何网络动作**之前**执行 |
| `--db` | str | 无 | 库 id 或 name（缺省库回退链同上） |
| `--param` | str（可重复） | 无 | 模板变量 `k=v`（值原样字符串）；**仅支持 variable 目标**；缺 `=` 或空 k → exit 2。v0.2 起自动为每个 `--param` 声明 `template-tags`（含独立 uuid id），参数对象也带稳定 `id`——不声明会被服务端 400 拒绝（`missing required parameters`）；标签类型按值形状推断：数字→`number`（替换不带引号）、ISO 日期→`date`、其余→`text` |
| `--allow-write` | flag | 关 | 绕过写保护（自担风险） |
| `--format` | | json | 四种格式均支持 |
| `--max-rows` | int ≥0 | 1000 | 客户端截断；`0` = 不限 |
| `--out` / `--fields` | | | `--fields` 对查询信封**不可用**（exit 2） |

```bash
mbcli query --db 15 --native "SELECT 1 AS one"
mbcli query --db quant_data --native "SELECT * FROM app_mau LIMIT 3" --format csv
mbcli query --db 15 --native "SELECT * FROM app_mau LIMIT 3" --max-rows 2   # 截断演示
mbcli query --db 15 --native "SELECT count(*) FROM app_mau WHERE date = {{dt}}" --param dt=2023-07-01
mbcli query --db 15 --native "SELECT * FROM app_mau" --format ndjson --out big.ndjson
```

JSON 输出（信封逐字段说明见 §6.3）：

```json
{"status": "completed", "row_count": 3, "rows_returned": 2, "truncated": true, "columns": [{"name": "date", "base_type": "type/Text", "database_type": "VARCHAR"}, …], "rows": [["2023-07-01", 233.555, 50.5076, 222.843], ["2023-07-02", 241.042, 54.9598, 221.78]], "running_time_ms": 44, "cached": false, "data_warnings": []}
```

`row_count` = 服务端本响应行数；`rows_returned` = 截断后实际输出；`truncated` = 服务端行数 > N。写保护拦截（exit 2，零服务端执行）：

```json
{"error": {"code": "write_blocked", "message": "non-read-only statement rejected (leading keyword DELETE); re-run with --allow-write to allow write operations", "detail": {"keyword": "DELETE", "statement": "DELETE FROM app_mau"}}}
```

可能错误码：`write_blocked`（**已知写/管理关键词**（DELETE/UPDATE/INSERT/DROP/SET/…）非只读，exit 2，detail 提示加 `--allow-write`）、`invalid_args`（param 畸形 / --db 全缺 / --fields 用于信封 / 纯注释空 SQL / **无法识别的首关键词**（疑似拼写错误，如 `SELECTNOSUCH`——此类语句 `--allow-write` 也救不了，报错绝不提示它））、`bad_query`（HTTP 400 且 `status=failed`，如表不存在）、`auth_failed`、`not_found`（库名解析）、`timeout`（注意：超时不取消服务端查询）、`rate_limited`、`server_error`。

### 5.10 `mbcli cards run`

执行已保存的问题（`POST /api/card/{id}/query`），输出与 query 相同的信封。

```
mbcli cards run CARD_ID [--param k=v]… [--format FMT] [--max-rows N] [--out FILE] [--fields KEYS]
```

| 选项 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `card_id` | int | **必填**（位置参数） | 卡片 id |
| `--param` | str（可重复） | 无 | 同 query 的 `k=v` 文法；v0.2.2 起绑定到卡片**声明的参数 id**（卡片 `parameters` 按 name/slug 匹配；native 模板变量按 tag 名匹配——含 pMBQL `dataset_query.stages[*].template-tags` 列表形态）。**硬失败语义**：`{{var}}` 出现在 SQL 但卡片未声明（休眠模板变量）→ `invalid_args`（exit 2，指引改用 `query --native` 复用 SQL）；key 完全不匹配 → `invalid_args` 并列出可用参数名——**绝不静默无过滤执行**。带 `--param` 时会先 `GET /api/card/{id}` 读取定义 |
| 其余 | | | 同 query 的塑形选项 |

```bash
mbcli cards run 7876
mbcli cards run 7876 --format csv --max-rows 100 --out r.csv
```

```json
{"status": "completed", "row_count": 1, "rows_returned": 1, "truncated": false, "columns": [{"name": "max_date", …}], "rows": [["2026-10-30"]], "running_time_ms": 38, "cached": false, "data_warnings": []}
```

可能错误码：`not_found`（卡片不存在）、`bad_query`（卡片查询失败）、`invalid_args`、`auth_failed`、`timeout`。

### 5.11 `mbcli cards show`

查看卡片定义（SQL/MBQL/参数/集合），不执行。

```
mbcli cards show CARD_ID [--format FMT] [--fields KEYS] [--out FILE]
```

| 选项 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `card_id` | int | **必填**（位置参数） | 卡片 id |
| `--format` | | json | 单对象：json/table |
| `--fields` | str | 无 | 裁剪该对象的顶层键 |

```bash
mbcli cards show 7876
mbcli cards show 7876 --fields id,name,sql
```

```json
{"id": 7876, "name": "数据日期", "description": null, "collection": {"id": 1039, "name": "微信"}, "display": "scalar", "query_type": "native", "database_id": 13, "sql": "select max(date) as max_date from tab_net_moblie_user_month", "mbql": null, "parameters": [], "created_at": "…", "updated_at": "…", "last_edit": {"user": "…", "at": "…"}}
```

`query_type` 为 `native` 时 `sql` 有值、`mbql` 为 null；MBQL 卡片相反。可能错误码：`not_found`、`invalid_args`（--fields 拼错）、`auth_failed`、`network_error`。

### 5.12 `mbcli collections list`

集合列表；默认滤掉个人集合（实例可有上千个）。

```
mbcli collections list [--all] [--format FMT] [--fields KEYS] [--out FILE]
```

| 选项 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `--all` | flag | 关 | 包含 `is_personal` 的个人集合 |

```bash
mbcli collections list --fields id,name
mbcli collections list --all --format csv
```

```json
[{"id": "root", "name": "Our analytics", "parent_id": null, "authority_level": null, "is_personal": false}, {"id": 663, "name": "共享报表", "parent_id": null, "authority_level": null, "is_personal": false}, …]
```

根集合 id 是字符串 `"root"`。可能错误码：`auth_failed`、`config_missing`、`network_error`、`timeout`。

### 5.13 `mbcli collections show`

浏览集合内容（分页信封）。`--model` 过滤以**复数 `models`** 参数发给服务端（单数 `model=` 会被 Metabase 静默忽略）。json 输出 `{total,limit,offset,items}` 信封；csv/ndjson/table/markdown 直接渲染 `items[]` 记录数组（v0.2 §13.4 格式统一）。

```
mbcli collections show <id|name|root> [--model M]… [--limit N] [--offset N]
                       [--format FMT] [--fields KEYS] [--out FILE]
```

| 选项 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `ref` | str | **必填**（位置参数） | 集合 id / 名称 / 保留字 `root` |
| `--model` | str（可重复/逗号分隔） | 无 | `card` / `dashboard` / `collection` / `dataset` |
| `--limit` | int ≥1 | 无（不发送，服务端默认） | 页大小 |
| `--offset` | int ≥0 | 无（不发送，即从头开始） | 翻页偏移 |
| `--fields` | str | 无 | 只裁剪 `items[]` 记录键，信封键 `total/limit/offset` 永不裁剪 |

```bash
mbcli collections show root --model card --limit 20
mbcli collections show 1039 --model card,dashboard --offset 20
```

```json
{"total": 57, "limit": 20, "offset": 0, "items": [{"id": 7876, "model": "card", "name": "数据日期", "description": null, "collection": {"id": 1039, "name": "报表"}, "display": "scalar", "updated_at": "2026-10-01T08:00:00.000Z"}, …]}
```

可能错误码：`not_found` / `ambiguous_ref`（名称解析）、`invalid_args`、`auth_failed`、`permission_denied`（无权集合）、`network_error`。

### 5.14 `mbcli dashboards show`

仪表盘结构（tabs / parameters / cards 含位置），**不执行任何卡片**。

```
mbcli dashboards show DASHBOARD_ID [--format FMT] [--fields KEYS] [--out FILE]
```

| 选项 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `dashboard_id` | int | **必填**（位置参数） | 仪表盘 id |
| `--format` | | json | 单对象：json/table |

```bash
mbcli dashboards show 1607
mbcli dashboards show 1607 --fields id,name,cards
```

```json
{"id": 1607, "name": "疫情消费品类情况", "description": null, "collection": {"id": 1274, "name": "疫情下消费品类情况"}, "tabs": [], "parameters": [], "cards": [{"card_id": 9712, "name": "可选消费品类3月社零情况", "series": [], "position": {"row": 114, "col": 5, "size_x": 19, "size_y": 4}, "parameter_mappings": []}, {"card_id": null, "name": null, "series": [], "position": {"row": 9, "col": 0, "size_x": 5, "size_y": 4}, "parameter_mappings": []}, …]}
```

文本卡等无 `card` 的 dashcard 输出 `card_id: null`。可能错误码：`not_found`（404，body 常为纯文本）、`permission_denied`、`auth_failed`、`network_error`。

### 5.15 `mbcli search`

全局检索资产（`GET /api/search`），Agent 定位资产的入口。

```
mbcli search QUERY [--model M]… [--db <id|name>] [--limit N] [--offset N] [--match-any]
                   [--format FMT] [--fields KEYS] [--out FILE]
```

| 选项 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `query` | str | **必填**（位置参数） | 关键词；`--match-any` 时为逗号分隔多关键词 |
| `--model` | str（可重复/逗号分隔） | 无 | 复数 `models` 透传；card/dashboard/collection/table/dataset/metric |
| `--db` | str | 无 | **页内客户端过滤**：card 匹配 `database_id`、table 匹配 `table_db_id`，两者皆无的条目被丢弃；可能因 limit 截断漏配（见 §8） |
| `--limit` | int ≥0 | 无（服务端默认 50） | 页大小；**`0` = 拉全集**：客户端翻页（页 100，硬上限 1000 行），触顶时输出尾标注 truncated |
| `--offset` | int ≥0 | 无（不发送，即从头开始） | 翻页 |
| `--match-any` | flag | 关 | 把 QUERY 视为逗号分隔多关键词：各关键词分页拉取后按 `(model,id)` 去重合并（first-seen 顺序，ADR #21）；`--limit N` 变为合并结果的输出上限 |

```bash
mbcli search 数据 --limit 3
mbcli search 收入 --model card,dashboard --limit 10
mbcli search 用户 --db 15 --limit 20
mbcli search 手机,mobile,phone --match-any --limit 5    # 多关键词并集
mbcli search 用户 --limit 0                             # 拉全集（≤1000 行）
```

```json
[{"id": 7876, "model": "card", "name": "数据日期", "description": null, "collection": {"id": 1039, "name": "微信"}, "database_id": null, "database_name": null, "updated_at": "2026-10-30T22:21:23.292094+08:00"}, …]
```

`database_name`（v0.2.2）：客户端经 Resolver 缓存的 `/api/database` 索引解析的**库名**（card 取 `database_id`，table 取 `table_db_id`，一次索引请求共享复用、零逐条请求；无库或未知 id 为 null）——找资产时可直接按名过滤，无需再 `databases list` 对照。

**`--limit 0` / `--match-any` 全集模式的截断标注**（§5.2 哲学）：命中 1000 行硬上限时——json 变为信封 `{"truncated": true, "rows_returned": 1000, "items": […]}`；ndjson 末行追加哨兵 `{"truncated": true, "rows_returned": 1000}`；csv 末行追加 `# {"truncated": …}` 注释行；table/markdown 末行追加同名标记行。未触顶时输出与普通模式完全一致（纯数组）。

注意：本实例的 search 条目 `database_id` 常为 `null`（见 §8）。找某库的表请用 `tables --db D --match`。可能错误码：`not_found`（--db 名字解析）、`invalid_args`（--match-any 空关键词）、`auth_failed`、`network_error`、`timeout`。

### 5.16 `mbcli context` ⭐

生成 LLM prompt 用的 schema 上下文。一次 `GET /api/database/{id}/metadata`，默认 **markdown**。

```
mbcli context [--db <id|name>] [--table T]… [--max-tables N]
              [--include-fields-pattern REGEX] [--markdown|--json]
```

| 选项 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `--db` | str | 无 | 缺省库回退链同其他命令 |
| `--table` | str（可重复） | 全库 | 指定表**按给定顺序**输出；缺失 → `not_found`，重名 → `ambiguous_ref` |
| `--max-tables` | int ≥1 | 50 | 未指定 --table 时按**表名字典序**取前 N（确定性） |
| `--include-fields-pattern` | 正则 | `.*` | 字段名过滤（Python `re.search` 部分匹配语义）；防宽表爆炸 |
| `--markdown` / `--json` | flag | markdown | 二选一（同时给以 `--json` 为准）；本命令**没有** `--format/--fields/--out` |

```bash
mbcli context --db 15 --table app_mau
mbcli context --db quant_data --max-tables 10 > schema.md
mbcli context --db 15 --include-fields-pattern '^(uid|id)$' --json
```

markdown 全样例（真实布局；`estimated_row_count` 为 null 时显示 `~?`；FK 由 `fk_target_field_id` 在**同一响应内**反查，零额外请求）：

```markdown
# Database: quant_data (mysql, id=15) — 1 tables shown / 328 total

## Table `app_mau` (id=3258, ~? rows¹)

| column | type | semantic | pk | fk → | description |
|---|---|---|---|---|---|
| date | VARCHAR | type/Text | | | |
| anjuke | FLOAT | type/Float | | | |
| lianjia | FLOAT | type/Float | | | |

¹ estimated_row_count 为 Metabase 同步估值，仅作参考
```

`--json` 输出 `{"database":{…DatabaseInfo…},"tables":[{…TableInfo…,"fields":[…FieldInfo…]}]}`（字段与 §5.5/§5.7/§5.8 同构；`field_count` 跟随字段过滤结果）：

```json
{"database": {"id": 15, "name": "quant_data", "engine": "mysql", "description": "量化", "is_sample": false, "initial_sync_status": "complete", "timezone": "UTC", "dbms_version": {…}}, "tables": [{"id": 3258, "schema": "", "name": "app_mau", "display_name": "app_mau", "description": null, "estimated_row_count": 65, "active": true, "field_count": 2, "fields": [{"id": 37677, "name": "date", "display_name": "date", "base_type": "type/Text", "semantic_type": null, "database_type": "VARCHAR", "description": null, "is_pk": false, "is_nullable": true, "is_indexed": false, "fk_target": null, "position": 1}, …]}]}
```

可能错误码：`invalid_args`（--db 全缺 / 正则非法，exit 2，正则校验先于任何网络请求）、`not_found` / `ambiguous_ref`（表选择）、`auth_failed`、`network_error`、`timeout`。

### freshness — 数据新鲜度（v0.2）

每表自动挑一个时间列（优先级：语义类型 > `type/DateTime|Date` > 列名匹配 `date|dt|day|month|year|time`），**优先读 Metabase 同步指纹，全程零查询**；线上指纹形态为嵌套 `type/DateTime.{earliest,latest}`（实测 v0.66），扁平/空形态容错处理。

```bash
mbcli freshness --db 15                                   # 全库前 20 张（字典序），零查询
mbcli freshness --db 15 --table app_mau --table 3258      # 指定表（id 或名称），不受上限约束
mbcli freshness --db 15 --sample --max-tables 20          # 无指纹表显式发 MIN/MAX（标识符一律反引号）
# 输出 [{table, table_id, column, min, max, source: fingerprint|sample|none, reason}]
# 无指纹且未 --sample → source:"none", reason:"no fingerprint (use --sample)"
```

### overview — 实例总览（v0.2）

一次调用看清实例全貌：库×表数（`?include=tables`，不可用自动回退逐库）、非个人顶层集合的卡片/仪表盘计数、最近更新 TOP N。

```bash
mbcli overview
mbcli overview --top 20 --format markdown
# 输出 {totals:{databases,collections,cards,dashboards}, databases:[…], collections:[…], recent:[…]}
```

### batch — 单进程批处理（v0.2）

从 stdin 逐行读子命令 argv（`#` 注释/空行跳过），**一个进程跑完**——省去每次 uv 冷启动与登录（session 磁盘缓存共享，实测多条命令仅登录一次）。stdout 为 NDJSON，每命令一行：

```bash
printf 'databases list\ntables --db 15 --match fut\nsearch 手机 --limit 2\n' | mbcli batch
# {"index":1,"argv":["databases","list"],"exit":0,"error":null,"result":[…]}   ← index 从 1 起
# {"index":2,"argv":["tables","--db","15","--match","fut"],"exit":0,"error":null,"result":[…]}
```

行内错误不中断批次（该行 `exit`/`error` 记录，进程退出码任一非 0 则 1）；禁止嵌套 `batch`；行内不支持根级选项（连接配置走环境变量/配置文件）。

### 5.17 v0.2 新命令：`freshness` / `overview` / `batch`

以下命令自 v0.2.0 起**已全部实装**（早期占位阶段的 `not_implemented` 固定返回已移除）：

```
mbcli freshness --db <id|name> [--table T]… [--max-tables 20] [--sample] [--format …] [--fields …] [--out F]
mbcli overview [--top 10] [--format …] [--out F]
mbcli batch < stdin    # 每行一条子命令 argv，输出 NDJSON
```

| 命令 | 用途 | 关键选项 |
|---|---|---|
| `freshness` | 每表时间列 min/max 数据新鲜度；优先读字段指纹（零查询），无指纹且 `--sample` 才发 `SELECT MIN(\`col\`), MAX(\`col\`)` | `--db`（缺省库回退）、`--table`（可重复）、`--max-tables`（默认 20，防查询风暴）、`--sample` |
| `overview` | 实例总览：库×表数、非个人顶层集合内容分布、最近更新 | `--top`（默认 10） |
| `batch` | 单进程多命令（消 uv 冷启动），stdout 每命令一行 NDJSON `{"index","argv","exit","error","result"}` | — |

**freshness 的 `--max-tables` 截断语义**：未指定 `--table` 时按**表名字典序**确定性取前 N（默认 20）；库内表更多时输出尾标注 `truncated`（§5.2 哲学）——完整覆盖请加 `--max-tables` 或逐批 `--table`。指定 `--table` 则精确处理那些表、无截断。

```bash
mbcli freshness --db 15                 # [{"table","table_id","column","min","max","source":"fingerprint|sample|none","reason"}]
mbcli overview --top 20
printf 'tables --db 15 --match fut\nfields --table 3258\n' | mbcli batch
```

---

## 6. Agent 集成指南

### 6.1 退出码

| 码 | 含义 | 处置建议 |
|---|---|---|
| 0 | 成功 | 解析 stdout |
| 1 | API / 执行错误 | 读 stderr JSON 的 `code` 决定重试或放弃 |
| 2 | 用法错误 | **改参数/加 flag 即可修复**，不要原样重试 |
| 130 | 用户中断（Ctrl-C） | — |

### 6.2 错误形态与错误码表

stderr 单行 JSON（`ensure_ascii=False`）：

```json
{"error": {"code": "bad_query", "message": "Table 'quant_data.x' doesn't exist", "detail": {"http_status": 400, "error_type": "…"}}}
```

| code | exit | 含义 |
|---|---|---|
| `auth_failed` | 1 | 登录失败或 401（错密码/key 指向凭据） |
| `permission_denied` | 1 | 403（如无权集合） |
| `not_found` | 1 | 404 或名称解析无命中 |
| `bad_query` | 1 | SQL 语义错误（400 + `status=failed`）；报错 token 命中 MySQL 保留字时 `detail` 追加 `reserved_word` + 反引号提示 |
| `invalid_request` | 1 | 400 参数校验错（`detail.errors` 字段级） |
| `rate_limited` | 1 | 429 |
| `server_error` | 1 | 5xx |
| `network_error` / `timeout` | 1 | 连不上 / 读超时（**不取消服务端查询**，重试注意叠加负载） |
| `config_missing` | 1 | 无 URL 或缺凭据（懒触发） |
| `unhealthy` | 1 | health 非 ok |
| `ambiguous_ref` | 1 | 名称多命中（detail 列候选 id，改用数字 id） |
| `invalid_response` | 1 | 响应不是预期 JSON 结构 |
| `invalid_args` | **2** | 参数用法错误（typer 层 + 业务校验） |
| `write_blocked` | **2** | 非 SELECT 被拦（detail 提示加 `--allow-write`） |

### 6.3 查询信封逐字段

```json
{"status": "completed", "row_count": 42, "rows_returned": 42, "truncated": false,
 "columns": [{"name": "one", "base_type": "type/BigInteger", "database_type": "BIGINT"}],
 "rows": [[1, "x"]],
 "running_time_ms": 123, "cached": false, "data_warnings": []}
```

| 字段 | 说明 |
|---|---|
| `status` | 恒为 `completed`（失败走错误通道） |
| `row_count` | **服务端本响应**行数（截断前） |
| `rows_returned` | 客户端截断后实际输出行数 |
| `truncated` | `row_count > --max-rows` 时 true；触顶后全量行数**不可知** |
| `columns` | 列名 + base_type + database_type 白名单 |
| `rows` | 二维数组，保持列序 |
| `running_time_ms` | 服务端耗时 |
| `cached` | 是否命中 Metabase 缓存 |
| `data_warnings` | 如服务端自身截断（`data.rows_truncated`）并入此列表 |

### 6.4 ndjson 流

首行信封、每行一个 row 数组、**末行哨兵**（读到即知流完整）：

```jsonl
{"columns": [{"name": "one", "base_type": "type/BigInteger", "database_type": "BIGINT"}]}
[1]
[2]
{"truncated": false, "rows_returned": 2}
```

管道消费注意事项（jq 侧）：

```bash
# 只要数据行：跳过首行元数据（columns）与末行哨兵（truncated）
mbcli query --db 15 --native "SELECT …" --format ndjson \
  | jq -c 'select(type == "array") | .[0]'            # row 是数组，不是对象
# 先校验完整性再取数（哨兵带 truncated:true 时应改写 SQL 加 LIMIT）
… | jq -s '.[-1] | select(.truncated == true) // empty'
```

- 首行 `{"columns": …}` 与末行 `{"truncated": …}` 都是**对象**，数据行是**数组**——用 `select(type=="array")` 一分为二，勿按 `.field` 盲取（对象行没有同名字段）。
- `jq` 按行流式处理时无法"回头"：需要"先看哨兵再用数据"就得 `jq -s` 一次性读入（大结果慎用）或落盘后两遍扫描（`--out f.ndjson` + `tail -1 f.ndjson` 预检）。
- 记录数组型命令（search/tables 等）的 ndjson 每行是**对象**，无 columns 元数据行；仅有触顶时的末行哨兵。

### 6.5 大结果三件套

1. **SQL 自带 `LIMIT`**（首选，Agent 的责任）；
2. `--max-rows N` 输出限量（客户端截断，语义见 §6.3）；
3. `--out FILE` / `--format ndjson` 让大结果不进 stdout。

### 6.6 四步典型工作流

```bash
mbcli connect --default-db quant_data --non-interactive     # ① 连接（或 MB_* env）
mbcli tables --match 用户                                     # ② 找表
mbcli fields --table app_mau                                  # ③ 看字段与 FK
mbcli query --native "SELECT * FROM app_mau LIMIT 5"          # ④ 小样验证
mbcli context --table app_mau > context.md                    # ⑤ 生成 prompt 上下文
```

### 6.7 名称解析规则

- `--db/--table/--collection` 统一接受 `<id>` 或 `<name>`（纯数字视为 id 直通）；
- 名称解析失败 → `not_found`；多命中 → `ambiguous_ref`（detail 给候选 id，改用数字 id）；
- `collections` 的保留字 `root` 映射根集合（服务端字符串 `"root"`）；
- 库名→id 用一次 `GET /api/database` 建进程内缓存；表名必须配合 `--db`（或缺省库）。

---

## 7. 输出格式参考

| 格式 | 场景 | 样例 |
|---|---|---|
| `json`（默认） | Agent 消费；对象或数组 | 见各命令 |
| `csv` | 表格/Excel；**仅记录数组型命令**（databases/tables/fields/schemas/collections list/search/query/cards run；collections show 渲染其 items[]） | 见下 |
| `ndjson` | 流式消费大结果；**仅查询类**（query/cards run）与记录数组 | 见 §6.4 |
| `table` | 人读调试（rich 表格）；可配 `--no-color` | — |
| `markdown` | 管道表（GitHub 风格）；记录键名做表头，unicode 原样，单元格内 `\|` 与换行转义；**记录数组型命令 + 查询类 + 单对象命令**（whoami 等）均支持 | 见下 |

**markdown 样例**（`mbcli tables --db 15 --match fut --format markdown`）：

```markdown
| id | schema | name | display_name | description | estimated_row_count | active | field_count |
|---|---|---|---|---|---|---|---|
| 3301 |  | fut_basic | fut_basic |  | 1234 | true | 20 |
| 3302 |  | fut_daily | fut_daily | 期货行情 | 56789 | true | 15 |
```

**csv 规则（RFC 4180）**：`\r\n` 行尾；含逗号/引号/换行的字段加引号，`"` 转义为 `""`；`null` 输出空；unicode 原样 UTF-8；嵌套对象单元格渲染为 JSON 文本。实跑样例（`SELECT 'a,b' AS x, 'say "hi"' AS y UNION ALL SELECT NULL, '换行前'`）：

```csv
x,y
"a,b","say ""hi"""
,换行前
```

`--fields id,name` 可在 CSV/markdown 里只导出指定列；`--out FILE` 落盘并输出摘要 `{"out": "r.csv", "rows": 3, "truncated": false}`。

---

## 8. 故障排查

| 错误码 / 症状 | 处置 |
|---|---|
| `auth_failed` | 核对账密/API Key；session 模式下先 `mbcli whoami` 快速验证；key 模式 401 即 key 无效 |
| `config_missing` | 无 URL：`mbcli connect` 一次或设 `MB_URL`；缺凭据：设 `MB_API_KEY` 或 `MB_USERNAME/MB_PASSWORD`（`health` 无凭据也能跑，可先排除网络问题） |
| `bad_query` | message 含服务端原因（表/列不存在等）；detail 带 `http_status`/`error_type`；**命中保留字**时 detail 另带 `reserved_word` 与"请用反引号包裹"提示（如列名 `year_month`），改写 SQL 为 `` `year_month` `` 后重试 |
| `not_found` | 404 或名称无命中；用 `databases list` / `tables --match` 找正确名字或直接用数字 id |
| `permission_denied` | 当前账号对该对象无权限（如他人个人集合），预期行为 |
| `write_blocked` | exit 2：加 `--allow-write`（明确知晓风险才加）；白名单 SELECT/WITH/SHOW/DESCRIBE/EXPLAIN，多语句每条都要过检 |
| `ambiguous_ref` | detail 列出候选 id，改用数字 id |
| `invalid_args` | exit 2：检查选项拼写、`--fields` 键名、根级/命令级选项位置（§4）、`--param k=v` 形态 |
| `timeout` | 提高 `--timeout`；**超时不取消服务端查询**，重试前考虑叠加负载 |
| `rate_limited` / `server_error` | 幂等 GET 自动退避重试两次（0.5s/1s）；仍失败则稍后再试 |
| health 非 ok | `unhealthy`，detail 里有原始 body（如 `{"status":"initializing","progress":30}`），等同步完成再试 |
| session 缓存损坏 | **自动处理**：静默忽略并重登，无需手动清理 |
| `estimated_row_count` 失真 | 已知问题：同步估值可差数量级或为 `null`，**不可依赖**，仅排序/筛选用 |
| `search --db` 漏结果 | 页内过滤语义：服务端 search 不支持按库过滤，超 `--limit` 的结果不扫；按库找表用 `tables --db D --match`；且本实例 search 条目 `database_id` 常为 `null`，此时仅 table 条目（`table_db_id`）能被 `--db` 保留 |

---

## 9. FAQ

**Q1：怎么切换默认库？**
临时：`--db <id|name>`；会话级：`MB_DEFAULT_DB`；永久：`mbcli connect --default-db <id|name>`（重连同一连接即可，无需 `--force`）。

**Q2：多实例 / 多套配置怎么办？**
用 `MB_CONFIG_FILE` 指向不同的 toml（connect 写入、所有命令读取都跟随）：`MB_CONFIG_FILE=~/.config/mbcli/prod.toml mbcli connect …`。只想到"另一套目录"时也可用 `MB_CONFIG_DIR=~/mbcli-configs/prod`（解析为该目录下的 `mbcli.toml`；显式 `MB_CONFIG_FILE` 优先）。

**Q3：为什么没有 `cards list` 全量命令？**
实测该实例 `GET /api/card?f=all` 单次 6MB+ 且 60s 超时。找卡走 `search`，浏览走 `collections show --model card`（分页）。

**Q4：`--max-rows 0` 有什么风险？**
解除客户端截断，内存与 stdout 大小完全取决于服务端返回（服务端忽略请求级限量）。大结果务必配 `--out` 或 `--format ndjson`，SQL 里自带 `LIMIT` 才是首选。

**Q5：密码改了 / 忘了怎么办？**
改密：`mbcli connect --username 同一账号 --password 新密码`（同一连接重连无需 `--force`）。想彻底重来：`mbcli disconnect` 后重新 `connect`。

**Q6：API Key 怎么建？**
Metabase 管理员 UI：管理设置 → Authentication → API Keys 生成（要求 v49+）。之后 `mbcli connect --api-key …`——无明文密码落盘、无过期，优于账密。

**Q7：`--db` 明明给了名字却报 `ambiguous_ref`？**
重名库/表/集合，detail 里列出全部候选 id，用数字 id 精确定位。

**Q8：集成测试怎么跑？**
`MBCLI_INTEGRATION=1 MB_URL=… MB_USERNAME=… MB_PASSWORD=… uv run pytest -m integration`（只读纪律：仅 SELECT 1 / LIMIT / GET；未设变量时自动 skip）。

**Q9：session 会话多久过期？会突然掉线吗？**
缓存 14 天有效；任意请求遇 401 自动重登一次并重放，再失败才 `auth_failed`，正常使用无感。

**Q10：`context` 输出里的 `~? rows¹` 是什么？**
该表 `estimated_row_count` 为 `null`；行数是同步估值（见 §8），仅作参考。

---

## 10. 开发指南

```bash
uv sync                                          # 安装依赖（含 dev 组）
uv run pytest                                    # 离线单测/契约测试（无网络，含 golden）
MBCLI_INTEGRATION=1 MB_URL=… MB_USERNAME=… MB_PASSWORD=… \
    uv run pytest -m integration                 # 只读集成测试（未启用自动 skip）
uv run ruff check . && uv run ruff format --check .
uv run mbcli …                                   # 开发期运行
```

目录结构：

```
src/mbcli/
├── cli.py            # typer 入口、根级选项、错误→退出码
├── config.py         # 配置链（env/.env/toml/默认）、connect 落盘、--db 回退
├── auth.py           # API Key / session 缓存（0600 原子写、脱敏）
├── client.py         # HTTP：认证头注入、401 重登重放、GET 重试、错误归一
├── resolver.py       # id|name 解析（进程内缓存、ambiguous_ref、root）
├── models.py         # 输出 dataclass 白名单（稳定字段名/键序）
├── output.py         # json/ndjson/csv/table、--fields、--max-rows、--out
├── guard.py          # 词法级 write-guard（注释/字符串感知、多语句拆分）
├── errors.py         # MbcliError + 错误码枚举 + stderr 单行 JSON
└── commands/         # 每命令一模块（connect/query/context/…）
tests/                # 离线 respx 契约测试 + test_live.py 集成（-m integration）
```

设计与决策记录见 [DESIGN.md](DESIGN.md)，快速上手见 [README.md](README.md)。
