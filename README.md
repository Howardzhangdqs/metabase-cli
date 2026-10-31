# mbcli — 面向 Agent 的 Metabase 命令行客户端

> 📖 **完整使用文档见 [USAGE.md](USAGE.md)** — 全部命令的语法/选项表/示例/输出样例/错误码、Agent 集成指南与故障排查（v0.2 起含 markdown 格式、search --match-any/--limit 0、fields --search、保留字提示；freshness/overview/batch 为占位）。

通过 Metabase REST API 浏览元数据、执行只读 SQL、检索已保存的资产（问题/集合/仪表盘），并一键生成适合注入 LLM prompt 的 schema 上下文。设计详见 [DESIGN.md](DESIGN.md)。

**Agent 优先的默认约定：**

- **stdout 只有数据**，默认 JSON（`ensure_ascii=False`，字段名 snake_case 白名单固化，不随 Metabase 版本漂移）；诊断与错误走 stderr；
- **稳定退出码**：`0` 成功 / `1` API 与执行错误 / `2` 用法错误（详见下文 Agent 契约）；
- **机器可读错误**：stderr 单行 JSON，含 `code` / `message` / `detail`；
- **默认只读**：`query` 默认拒绝非 SELECT 语句（词法级 write-guard）；
- **有限命令集**：只做浏览、查询、检索、上下文四类，无任何管理/写操作命令。

## 安装

需要 Python ≥ 3.10 与 [uv](https://docs.astral.sh/uv/)。

```bash
# 作为工具安装（全局命令）
uv tool install .

# 源码开发
git clone <repo> && cd metabase-cli
uv sync                       # 建 .venv 并安装依赖（含 dev 组）
uv run mbcli --help
```

### 单文件脚本（免安装，经 Release 发布）

[`mbcli.py`](../../releases) 是**整个项目合并而成的单文件脚本**（生成物，**不入库**，从 GitHub Releases 下载最新版）：适合无法安装包、但目标机自带 Python 与依赖的机器。依赖**不打包、不内嵌**——直接使用目标机自己的库：

```bash
# 目标机要求：Python >= 3.10，且已安装 typer（>=0.12）与 httpx（>=0.27）
pip install typer httpx       # rich 由 typer 携带，仅 --format table 需要

python3 mbcli.py --help       # 用法与安装版 mbcli 完全等价
python3 mbcli.py health
```

改动 `src/mbcli/` 后重新生成：`python3 scripts/build-single.py`（在装有 typer/httpx 的环境中运行，如 `uv run python scripts/build-single.py`；生成器自带自检）。

> ⚠️ **`mbcli.py` 是生成物，请勿手改**——任何修改都会在下次生成时丢失；请改 `src/mbcli/` 源码后重新生成。

## 配置

优先级：**CLI 显式参数 > 环境变量 > 工作目录 `.env` > `~/.config/mbcli/config.toml` > 默认值**。

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `MB_URL` | 无（必填） | 实例根地址，尾斜杠自动剥离 |
| `MB_API_KEY` | 无 | 设置则优先，所有请求附 `X-API-Key` 头（Metabase v49+），免登录 |
| `MB_USERNAME` / `MB_PASSWORD` | 无 | 账密换 session（懒校验：首个需认证请求才要求，`health` 无凭据可跑） |
| `MB_TIMEOUT` | `60` | 单请求 **read** 超时秒数（字节间隔上限，不是总时长；慢大查询可超时后仍在服务端运行，重试前请注意） |
| `MB_SESSION_CACHE` | `~/.cache/mbcli/session.json` | session 缓存路径，`0600` 权限、原子写、14 天有效，收到 401 自动重登一次 |

- `.env`：仅识别白名单键（`MB_URL/MB_USERNAME/MB_PASSWORD/MB_API_KEY`），支持 `#` 注释与引号；已在 `.gitignore` 中。
- `config.toml`：`~/.config/mbcli/config.toml`，仅接受 `[metabase]` 段同名键（snake_case：`url`、`api_key`、`username`、`password`、`timeout`、`session_cache`）。
- 临时覆盖：`--url --api-key --username --password --timeout`（**根级选项，必须放在子命令之前**），一次性运行，不写回任何文件。

**凭据安全**：凭据只来自 env/`.env`/config（**推荐 `MB_*` 环境变量**，`--password` 命令行参数会进入 shell history 与 `ps` 进程列表，仅限交互调试自担风险）；`-v` 日志对 `Authorization`/`X-API-Key`/`X-Metabase-Session` 及密码字段脱敏为 `***`，任何输出都不回显 password/api_key；session 缓存 `0600` 原子写，损坏即静默重登。

## 连接管理（connect 一次，之后零参数）

不想每次都带连接参数？用 `connect` 把连接持久化到 `config.toml`，后续命令自动取用：

```bash
mbcli connect --url https://metabase.example.com --username 'me@example.com' --default-db quant_data
# 密码交互式隐藏输入；校验（登录 + whoami）通过才落盘

mbcli query --native "SELECT 1 AS one"        # 之后：零连接参数
mbcli tables                                  # --db 也可省（用 --default-db 存的缺省库）
mbcli disconnect                              # 清空 [metabase] 段 + 删 session 缓存；幂等
```

- **先验证后落盘**：密码模式走 `登录 + whoami`，API Key 模式走 `whoami`；任何失败什么都不写。
- **取值优先级（逐字段）**：CLI 参数 > `MB_URL/MB_USERNAME/MB_PASSWORD/MB_API_KEY` 环境变量 > 交互式补问——仅当两者皆缺才提示（密码隐藏输入），环境变量齐了就完全不问。
- **`--non-interactive`**：仅在参数与环境变量**都**缺失时 exit 2（agent/脚本路径，永不阻塞 stdin）。
- **`--force`**：覆盖一个**不同**的旧连接（同一连接重连无需 --force，例如改密码后）。
- **凭据传入方式建议**：优先 `MB_*` 环境变量或交互式隐藏输入，避免 `--password`/`--api-key` 命令行参数——argv 会进入 shell history，且进程列表（`ps`）中可见。
- **`--default-db`** 经 `GET /api/database` 校验后以 **id** 落盘；生效优先级 `--db` > `MB_DEFAULT_DB` env > config `default_db`。
- 成功输出 `{"connected":true,"url":…,"user":{…},"default_db":{…}|null,"replaced":旧url|null,"config_file":"…"}`；connect 会**连带废弃 session 缓存**（旧 session 属于旧连接）。
- ⚠️ **密码以明文存入 config.toml**（文件 0600、目录 0700）。更安全的做法：在 Metabase 管理界面生成 **API Key**（v49+，`mbcli connect --api-key …`），无明文密码、无过期概念。
- 配置文件路径可用 `MB_CONFIG_FILE` 重定向（默认 `~/.config/mbcli/config.toml`），便于沙箱/多环境。

## 命令参考

**选项文法**：连接/诊断选项在子命令**前**（根级），数据塑形选项（`--format --fields --max-rows --out` 及命令专属选项）在子命令**后**：

```bash
mbcli --url http://host:3000 query --db 15 --native "SELECT 1" --format csv --max-rows 100
└────────── 根级 ──────────┘└────────────────── 命令级 ──────────────────┘
```

```
mbcli health                                     # GET /api/health；无需凭据
mbcli whoami                                     # 当前用户（id/email/is_superuser/group_ids）

# ── 连接管理 ──
mbcli connect [--url U] [--username U] [--password P] [--api-key K]
              [--default-db <id|name>] [--non-interactive] [--force]
mbcli disconnect                                 # 清空 [metabase] 段 + 删 session 缓存；幂等

# ── 元数据浏览 ──
mbcli databases list                             # 全部数据库（该端点无分页）
mbcli schemas   --db <id|name>                   # 去重 schema（MySQL 通常仅 ""）
mbcli tables    --db <id|name> [--schema S] [--match 子串] [--include-hidden]
mbcli fields    --table <id|name> [--db D] [--include-hidden]   # 表名必须配 --db；id 可独立
mbcli fields    --search <列名子串> --db D        # 跨表列搜索（零额外请求；reserved 标记保留字列）

# ── 查询 ──
mbcli query --db <id|name> --native "SELECT …" [--param k=v]… [--format …] [--max-rows 1000] [--out F] [--allow-write]
mbcli cards run <id> [--param k=v]… [--format …] [--max-rows] [--out F]
mbcli cards show <id>                            # 回显 SQL / MBQL / 参数 / 集合

# ── 资产检索 ──
mbcli collections list [--all]                   # 默认滤掉个人集合
mbcli collections show <id|name|root> [--model card|dashboard|collection|dataset]… [--limit N] [--offset N]
mbcli dashboards show <id>                       # tabs / parameters / cards（含位置），不执行任何卡片
mbcli search <QUERY> [--model …]… [--db D] [--limit 20] [--offset N]
mbcli search "kw1,kw2" --match-any [--limit N]   # 多关键词并集，按 (model,id) 去重；--limit 0 = 拉全集（上限 1000）

# ── Agent 增强 ──
mbcli context --db <id|name> [--table T]… [--max-tables 50] [--include-fields-pattern '.*'] [--markdown|--json]
```

<!-- v0.2-wave2: freshness/overview/batch -->
> v0.2 占位命令（已注册签名、尚未实现，调用返回 `not_implemented` exit 1）：
> `freshness --db D [--table T]… [--max-tables 20] [--sample]`、`overview [--top 10]`、`batch`（stdin 逐行子命令）。

真实示例（对测试实例）：

```bash
mbcli --url https://metabase.example.com health
mbcli databases list
mbcli tables --db quant_data --match mau
mbcli fields --table app_mau --db quant_data
mbcli fields --search year_month --db 15            # 哪些表有 year_month 列（reserved:true 为保留字）
mbcli query --db 15 --native "SELECT * FROM app_mau LIMIT 10" --format csv --max-rows 100
mbcli search 数据 --limit 3
mbcli search 手机,mobile,phone --match-any --limit 5   # 多关键词并集检索
mbcli tables --db 15 --match fut --format markdown    # 管道表输出
mbcli cards show 7876                            # 返回该卡片的 SQL
mbcli collections show root --model card --limit 20
mbcli dashboards show 1607
mbcli context --db quant_data --table app_mau    # 默认 markdown，可直接粘进 LLM prompt
mbcli context --db 15 --include-fields-pattern '^(uid|id)$' --json
```

通用数据塑形选项（命令级）：`--format json|ndjson|csv|table|markdown`（默认 json；记录数组型命令五种全支持——markdown 为管道表、键名做表头；csv/ndjson 仅记录数组与查询类）；`--fields a,b` 裁剪**记录**键（拼错报 `invalid_args`，绝不裁查询信封键）；`--out FILE` 结果落盘，stdout 改为摘要 `{"out":"…","rows":123,"truncated":false}`。

## Agent 契约

**退出码**

| 码 | 含义 | 典型 code |
|---|---|---|
| 0 | 成功 | — |
| 1 | API / 执行错误 | `auth_failed` `permission_denied` `not_found` `bad_query` `invalid_request` `rate_limited` `server_error` `network_error` `timeout` `config_missing` `unhealthy` `ambiguous_ref` `invalid_response` |
| 2 | 用法错误（改参数/加 flag 即可修复） | `invalid_args` `write_blocked` |

**错误形态**（stderr，单行 JSON；401/403/404 容忍纯文本 body）：

```json
{"error":{"code":"bad_query","message":"Table 'quant_data.x' doesn't exist","detail":{"http_status":400,"error_type":"…"}}}
```

**查询信封**（`query` / `cards run`，json 默认）：

```json
{"status":"completed","row_count":42,"rows_returned":42,"truncated":false,
 "columns":[{"name":"one","base_type":"type/BigInteger","database_type":"BIGINT"}],
 "rows":[[1,"x"]],
 "running_time_ms":123,"cached":false,"data_warnings":[]}
```

- **截断语义（客户端截断）**：Metabase 忽略一切请求级限量约束，mbcli 在内存中截到 `--max-rows`（默认 1000，`0` = 不限）；`row_count` = 服务端本响应行数，`rows_returned` = 截后实际输出行数，`truncated` = 服务端行数 > N。全量行数在触顶后不可知。
- **大结果三件套**：SQL 自带 `LIMIT`（首选，agent 责任）→ `--max-rows`（输出限量）→ `--out` / `--format ndjson`（不进 stdout）。
- **ndjson 哨兵**：首行 `{"columns":[…]}`，其后每行一个 row，**末行固定** `{"truncated":…,"rows_returned":N}` —— 读到即知流完整性。

**其他语义要点**

- **五种输出格式**（记录数组型命令：databases/schemas/tables/fields/search/collections list 等）：`json`（默认）/ `ndjson` / `csv` / `table`（人读）/ `markdown`（GitHub 风格管道表，键名做表头，unicode 原样）。
- `--model` 过滤以**复数 `models`** 参数发给服务端（可重复或逗号分隔；单数 `model=` 会被 Metabase 静默忽略）。
- `search --db D` 是**对返回页的客户端过滤**（服务端 search 不支持按库过滤），可能因 limit 截断而漏配；按库找表的正规路径是 `tables --db D --match`。
- `search --match-any "kw1,kw2"`：多关键词各自分页拉取后按 `(model,id)` 去重合并（first-seen 顺序）；`search --limit 0` 拉全集（客户端翻页，页 100，**硬上限 1000 行**，触顶时 json 输出变为 `{"truncated":true,"rows_returned":N,"items":[…]}` 信封、ndjson 末行追加 `{"truncated":…}` 哨兵）。
- `fields --search <子串> --db D`：扫 metadata 全表字段做列名子串搜索（**零额外请求**），行含 `{"table","table_id","column","base_type","semantic_type","is_pk","fk_target","reserved"}`；`reserved:true` 表示该列名是 MySQL 8.0 保留字。
- **MySQL 保留字防护**：内置保留字表（`src/mbcli/reserved.py`）；`bad_query` 报错 token 命中保留字时，`error.detail` 追加 `reserved_word` + 反引号提示；mbcli 自生成 SQL 一律反引号标识符（不改动用户 SQL）。
- `-v` 请求日志**固定输出到 stderr**（凭据脱敏），stdout 永远只有数据。
- `estimated_row_count` 一律为**约数**（同步估值，可差数量级），仅作排序/筛选用，不可依赖。
- 名称引用（`--db/--table/--collection`）统一接受 `<id>` 或 `<name>`；重名报 `ambiguous_ref`（detail 列出候选 id，改用数字 id 即可）；collection 保留字 `root` 映射根集合。

## 安全与只读纪律

- **write-guard 是纵深防御，不是安全边界**：词法级检查（注释/字符串字面量感知、多语句按 `;` 拆分且**每条**首关键词都必须属于白名单 `SELECT / WITH / SHOW / DESCRIBE / EXPLAIN`），默认拒绝一切写语句，`SELECT 1; DELETE …` 整体拒绝（exit 2，零服务端执行）。
- **真正的边界是数据库账号的只读权限**——请为 mbcli 使用的账号配置只读授权；guard 的启发式不应作为唯一防线。
- `--allow-write` 会绕过 guard 并原样提交语句，仅限明确知道自己在线做什么的场景使用，生产环境慎用。
- 凭据不写日志（脱敏）、session 缓存 `0600` 原子写、`.env` 不入库；本工具不含任何管理/写操作命令。
- 客户端超时/中断**不会取消**服务端查询，重试前请考虑叠加负载。

## 开发

```bash
uv sync                                          # 安装依赖（含 dev 组）
uv run pytest                                    # 离线单测/契约测试（默认无网络）
MBCLI_INTEGRATION=1 MB_URL=http://host:3000 \
    MB_USERNAME=… MB_PASSWORD=… uv run pytest -m integration
                                                 # 只读集成测试（未启用时自动 skip）
uv run ruff check . && uv run ruff format --check .
uv run mbcli …                                   # 开发期直接运行
```

集成测试纪律：只执行 `SELECT 1` / `SELECT … LIMIT n`（n ≤ 100）与元数据 GET，**永不**断言任何写操作；session 缓存重定向到临时目录，不触碰开发者真实缓存。
