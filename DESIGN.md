# mbcli 设计文档

> 状态：**设计定稿 v1.0**（已过独立设计评审，18 条发现全部合入；见 §12 评审记录）。
> 基于 `PLAN.md`（产品规划）与 2026-10-30 对测试实例的只读实测。本文档回答"怎么做"；`PLAN.md` 回答"做什么"。

---

## 0. 实测基线（设计的事实依据）

对测试实例 **v0.66.0-SNAPSHOT**（地址与凭据经 `.env`/环境变量注入，不入库）的只读探测结论：

| 事实 | 实测证据 | 设计影响 |
|---|---|---|
| 实例版本 **v0.66.0-SNAPSHOT** | `/api/session/properties.version.tag` | ≥ v49 支持 API Key（头 `X-API-Key`，无过期概念）；以实测字段为准 |
| `GET /api/database` 返回 **包裹结构** `{"data":[…],"total":24}` | 24 个库 | `databases list` 取 `.data`；**该端点无 limit/offset 分页**（文档核对 v46–v64 均无），一次全量 |
| `GET /api/database/{id}/metadata` 一次返回全量 `tables[]`（quant_data **328 张表**，含 `fields[]`） | 单次调用 | `schemas`/`tables` 复用一次 metadata 调用，进程内缓存；`fields` 另走单表端点（见 §4） |
| 表行数字段是 **`estimated_row_count`**，`rows` 已为 `null`；且**该实例估值严重失真**（`app_mau` 估 10 亿、实返 65 行） | 实测 | 输出字段用 `estimated_row_count`，语义标注"约数，不可依赖" |
| 字段关键属性：`base_type`/`semantic_type`/`database_type`/`fk_target_field_id`/`database_is_pk`/`database_is_nullable`/`database_indexed`/`description` | 实测 keys | `fields` 命令与 `context` 生成器的字段来源 |
| `GET /api/search` 返回 `{data,total,limit,offset,models,…}`，item 带 `model`（card/dashboard/table/…） | `q=数据&limit=3` | search 命令透传分页参数（服务端默认 limit 50）；该实例 card 条目 `database_id` 常为 null——`--db` 页内过滤实际只命中 table 条目 |
| `GET /api/card?f=all` **单次 6MB+，60s 超时截断** | 实测失败 | **禁止**默认全量拉卡片；改用 `collection/{id}/items`（分页 `{total,data}`）+ `search`。过滤参数是 **`models`（复数）**：实测单数 `model=` 被静默忽略（曾混入 card+dashboard） |
| `GET /api/collection` 返回**平铺 list**（1823 个，含个人集合，`is_personal` 可辨） | 实测 | 默认过滤个人集合，`--all` 放开 |
| `POST /api/dataset`（native）成功：`{status:"completed", data:{cols,rows,…}, row_count, running_time, cached}` | `SELECT 1` | 查询命令的响应解析契约 |
| `POST /api/dataset` 失败：**HTTP 400** + `{"error":"…","status":"failed","error_type":…,"class":…}` | 查不存在的表 | 错误规范化：body 带 `error`+`status=failed` → `bad_query` |
| **`/api/dataset` 的服务端限量约束全部无效**：`max_results`、`middleware.max-results`、`constraints.max-results` 三种形态均被忽略（65 行原样返回） | 实测对照 | `--max-rows` 只能**客户端截断**，契约见 §5.2；内存防线见 ADR #12 |
| **401/403/404 响应体常为纯文本**（`Unauthenticated` / `Not found.`；403 可为文本或 JSON） | 伪造 session 得 401 纯文本 | 错误解析必须容忍非 JSON body；参数校验错为 `{"errors":{字段:信息}}`；dataset 失败可带 `error_code` |
| 单卡片 `GET /api/card/{id}` 含 `dataset_query`、`parameters`、`display`、`collection_id`；**v0.66 实况 `dataset_query` 已是 pMBQL 形态**：`{"lib/type":"mbql/query","stages":[{"lib/type":"mbql.stage/native","native":"<SQL>"}],"database":13}`，旧形态存在于 `legacy_query`（JSON 字符串） | card 7876 复测 | `CardDetail` 三级解析：pMBQL（取最后一个 native stage）→ legacy `type/native` → `legacy_query` 回退（ADR #16） |
| `GET /api/dashboard/{id}` 含 `dashcards[]`（`card`、`parameter_mappings`、`col/row/size_x/size_y`）、`parameters[]`、`tabs[]` | dashboard 1607（120 张卡） | `dashboards show` 的来源 |

## 1. 设计原则

1. **Agent 优先**：stdout 只有数据（默认 JSON），人读内容必须显式 `--format table`；诊断/错误走 stderr。
2. **稳定契约**：命令名、JSON 字段名（snake_case）、退出码、错误码一旦发布即视为 API，破坏性变更需 `v2`。
3. **有限命令集**：只做浏览、查询、检索、上下文四类，不做管理操作（用户/设置/写操作）。
4. **实测优于文档**：字段以探测响应为准，`models.py` 固化；未知字段丢弃而非透传（防输出漂移）。
5. **安全默认**：只读优先、结果限量、凭据不落日志。

## 2. 工具链（uv 管理，定稿）

| 项 | 决定 |
|---|---|
| 包管理 | **uv**（本机 0.11.7 已装）管理虚拟环境与依赖；提交 `uv.lock` |
| Python | `requires-python >= 3.10`；`.python-version` 固定 `3.12`（本机 3.12.13） |
| 运行时依赖 | `typer`（CLI 骨架）、`httpx`（同步客户端）；`rich` 由 typer 自带，仅用于 `--format table` |
| 开发依赖组 | `pytest`、`respx`（httpx mock）、`ruff`（lint+format）——放在 `[dependency-groups] dev` |
| 入口 | `[project.scripts] mbcli = "mbcli.cli:main"` |
| 安装 | `uv tool install .`（或 git URL）；开发期 `uv run mbcli …` |

开发工作流：

```bash
uv sync                     # 建立并同步 .venv（含 dev 组）
uv run mbcli health         # 开发期运行
uv run pytest               # 单测（默认无网络）
MBCLI_INTEGRATION=1 uv run pytest -m integration   # 集成（只读）
uv run ruff check . && uv run ruff format .
```

配置读取不引入 `python-dotenv`：`config.py` 内置 ~20 行 `.env` 解析（键白名单 `MB_URL/MB_USERNAME/MB_PASSWORD/MB_API_KEY`，`#` 注释、引号剥离），依赖面保持最小。

## 3. 总体架构

```
cli.py (typer)                      # 参数解析、全局选项、异常→退出码
  └── commands/*.py                 # 每命令一个模块：编排「解析→调用→建模→输出」
        ├── client.py               # MetabaseClient：HTTP、重试、401 重登、错误规范化
        │     ├── auth.py           # AuthManager：API key / session 缓存（原子写）
        │     └── config.py         # 配置解析（CLI > env > .env > config.toml > 默认）
        ├── resolver.py             # id|name → id 解析（db/table/collection），进程内缓存
        ├── models.py               # 输出 dataclass（稳定字段名）
        └── output.py               # json/csv/table/ndjson、--fields 裁剪、--max-rows、--out
errors.py                           # MbcliError + 错误码枚举 + stderr JSON
guard.py                            # write-guard：词法器（注释/字符串感知）+ 多语句拆分
```

依赖方向单向向下；`commands` 不直接碰 `httpx`，便于 mock。

### 3.1 配置解析（config.py）

优先级：**CLI 显式参数 > 环境变量 `MB_*` > 工作目录 `.env` > `~/.config/mbcli/config.toml` > 默认值**。

| 键 | env | 默认 | 说明 |
|---|---|---|---|
| `url` | `MB_URL` | 无（必填） | 实例根地址，尾斜杠剥离；**启动即校验** |
| `api_key` | `MB_API_KEY` | 无 | 设置则优先，免 session |
| `username` / `password` | `MB_USERNAME` / `MB_PASSWORD` | 无 | **懒校验**：首个需认证请求时才要求（无 api_key 且缺账密 → `config_missing`）——`health` 无凭据也能跑 |
| `timeout` | `MB_TIMEOUT` | 60 | 详见下方超时语义 |
| `session_cache` | `MB_SESSION_CACHE` | `~/.cache/mbcli/session.json` | 0600 |
| `config_file` | `MB_CONFIG_FILE` | `~/.config/mbcli/config.toml` | connect 写入处；测试/沙箱用 env 重定向 |
| `default_db` | `MB_DEFAULT_DB` | 无 | `--db` 缺省回退值（connect 可写入；id 或 name） |

`config.toml` 仅接受 `[metabase]` 段同名键。`--url/--api-key/--username/--password/--timeout` 提供临时覆盖（一次性运行，不写回任何文件）。

**连接持久化（connect/disconnect，ADR #18）**：`mbcli connect` 校验通过后把 url/username/password（或 api_key）/default_db 写入 `config_file`（文件 0600，目录 0700），并**废弃现有 session 缓存**；单连接模型，重复 connect 直接覆盖并回显被替换的旧连接。密码以明文落盘——README 明示风险并推荐 API Key。后续命令按既有优先级链（CLI > env > .env > toml）自动取用，无需再传任何连接参数。

**超时语义**：`httpx.Timeout(connect=5, read=N, write=N, pool=5)`——`N` 是单请求 read 超时（字节间隔上限），**不是总时长**；慢流式大查询可运行超过 `N` 秒。客户端超时/中断**不会取消**服务端查询（README 与 `--help` 注明，避免 agent 重试叠加负载）。

### 3.2 认证（auth.py）

状态机：

1. `MB_API_KEY` 存在 → 所有请求附 `X-API-Key` 头（v49+；key 无过期概念，错误即 401）。本实例 0.66 支持；上线前用真实 key 冒烟验证一次。
2. 否则读 session 缓存 `{session_id, obtained_at}`：不存在/超过 14 天/**文件损坏或不可解析**（静默忽略，不报错）→ `POST /api/session` 登录并写缓存。
3. 任意**非登录**请求收到 **401**（含纯文本 body）：废弃缓存 → 重登**一次** → 重放原请求；再 401 → `auth_failed`。API key 模式下 401 直接 `auth_failed`。
4. **登录调用自己的失败一律 `auth_failed`**（exit 1），不论状态码/body 形态（400+`errors`、纯文本等），不走通用错误表——错密码必须指向凭据而非"参数错误"。

缓存写入**原子化**：同目录 tempfile → `chmod 0600` → `os.replace`；目录 `0700`。并发调用产生重复登录是良性的（各自拿到有效 session），**不加锁**。

### 3.3 HTTP 客户端（client.py）

```python
class MetabaseClient:
    def get(path, params=None) -> Any          # JSON 反序列化后返回
    def post_json(path, body) -> Any
    # 内部：注入认证头 → 发送（httpx.Client, follow_redirects=False）
    #      → 401 重登重试一次（仅非登录请求）→ 状态码/响应体错误规范化 → 抛 MbcliError
    # 重试：仅幂等 GET，遇 connect 错误/429/5xx，退避 0.5s/1s，至多重试 2 次
```

错误规范化映射（`errors.py`）：

| 触发 | code | exit |
|---|---|---|
| 401（body 常为纯文本 `Unauthenticated`） | `auth_failed` | 1 |
| 登录请求失败（任意形态） | `auth_failed` | 1 |
| 403（body 可为纯文本或 JSON） | `permission_denied` | 1 |
| 404（body 常为纯文本） | `not_found` | 1 |
| 400 且 body 含 `error`+`status:"failed"`（dataset 语义错误，可带 `error_code`） | `bad_query` | 1 |
| 400 带 `{"errors":{字段:信息}}` 参数校验错 / 422 | `invalid_request`（detail 收录字段级校验） | 1 |
| 429 | `rate_limited` | 1 |
| 5xx | `server_error` | 1 |
| 连接失败 / DNS | `network_error` | 1 |
| 超过 read 超时 | `timeout` | 1 |
| 配置缺失（懒校验触发） | `config_missing` | 1 |
| 参数用法错误（typer UsageError 拦截改码） | `invalid_args` | 2 |
| 非 SELECT 语句且未 `--allow-write` | `write_blocked` | 2 |
| `health` 返回非 `{"status":"ok"}`（含 503 initializing） | `unhealthy` | 1 |
| 名称解析出现多个候选 | `ambiguous_ref` | 1 |
| 响应 JSON 解析失败 | `invalid_response` | 1 |

stderr 统一格式（单行 JSON，`ensure_ascii=False`）：

```json
{"error":{"code":"bad_query","message":"Table 'quant_data.x' doesn't exist","detail":{"http_status":400,"error_type":"…"}}}
```

`write_blocked` 取 exit 2 的理由：调用方**加一个 flag 即可修复**，与"用法错误"同类；错误 detail 中明确提示 `re-run with --allow-write`，避免 agent 误读为"命令畸形，永不重试"。

### 3.4 写保护（guard.py，独立模块）

词法感知，而非简单首词检查：

1. **去注释**（字符串字面量感知）：`--…`、`#…`（MySQL 风格）、`/*…*/` 仅在**字符串/反引号引定界之外**视为注释；`'a--b'`、`` `drop-table` `` 是字面量。
2. **多语句拆分**：在字符串之外的 `;` 处切分；**每个**语句的首关键词都必须在白名单内（`SELECT 1; DELETE …` 整体拒绝）。
3. 白名单：`SELECT / WITH / SHOW / DESCRIBE / EXPLAIN`。
4. **`WITH` 的安全性声明限于当前实例全 MySQL**（CTE 只能含 SELECT）；接入 Postgres/DuckDB 等支持写式 CTE 的引擎后须重审。
5. 定位：**纵深防御的启发式**，真正的安全边界是数据库账号只读权限——README 与本节同时声明。

测试矩阵（§7 参数化）：多语句注入、CTE、`#`/`--`/块注释、字符串内关键词、`DELETE` 前置注释、反引号表名、空语句。

### 3.5 名称解析（resolver.py）

`--db/--table/--collection` 统一接受 `<id>` 或 `<name>`：

- db：一次 `GET /api/database` 建 `{id↔name}` 映射，进程内缓存；
- table：`--table <name>` **必须同时给 `--db`**（否则 `invalid_args`，exit 2）；在 db metadata（已缓存）中按名查，多个命中 → `ambiguous_ref`（detail 列出候选 id）；`--table <id>` 可不带 `--db`；
- collection：`GET /api/collection` 平铺列表建映射；保留字 `root` 映射根集合（服务端即用字符串 `"root"`）。

### 3.6 输出（output.py）

- `--format json`（默认）：对象或数组；查询类命令输出统一信封（§5.2）；`ensure_ascii=False`，键序按 dataclass 声明。
- `--format ndjson`：查询结果逐行流式输出；**末行追加哨兵** `{"truncated":true,"rows":N}`（始终存在，agent 读到即知流完整性）。
- `--format csv`：仅适用于"记录数组"型命令；首行表头。转义遵循 RFC 4180（含逗号/引号/换行/unicode 用例测试）。
- `--format table`：rich 表格，人读调试。
- `--fields a,b,c`：**仅裁剪记录数组的键（或 CSV 列），绝不裁剪查询信封键**；拼错即 `invalid_args`（exit 2）。
- `--max-rows N`（查询类命令；默认 1000，0 = 不限）：**客户端截断**——服务端不提供限量约束（§0 实测），传输量由服务端绝对上限决定；详见 §5.2 与 ADR #12。
- `--out FILE`：结果写文件；stdout 改为摘要 `{"out":"…","rows":123,"truncated":false}`。

### 3.7 选项文法（全局 vs 命令级）

typer 的根 callback 选项**只能出现在子命令之前**；为保证 agent 照抄示例即可运行，文法定稿为：

- **根级（子命令前）**——连接与诊断：`--url --api-key --username --password --timeout --no-color -v`；
- **命令级（子命令后）**——数据塑形：`--format --fields --max-rows --out` 及各命令专属选项，以共享 `Annotated` 类型在命令函数上声明。

```bash
mbcli --url http://host:3000 query --db 15 --native "SELECT 1" --format csv --max-rows 100
└────────── 根级 ─────────┘└────────────────── 命令级 ──────────────────┘
```

---

## 4. 命令规格（定稿命令树）

```
mbcli health                                   # GET /api/health；启动中 503+{"status":"initializing","progress":N}
                                               # 非 "ok" → exit 1, code=unhealthy（body 进 detail）；无需凭据
mbcli whoami                                   # GET /api/user/current（id,email,is_superuser,group_ids）

# ── 连接管理 ───────────────────────────────────────────
mbcli connect [--url U] [--username U] [--password P] [--api-key K]
              [--default-db <id|name>] [--non-interactive] [--force]
                                               # 缺省项交互式补问（密码隐藏输入）；登录/whoami 验证
                                               # 成功才落盘（0600），同时废弃 session 缓存；覆盖旧连接需 --force
                                               # --default-db 经 GET /api/database 校验后写入
mbcli disconnect                               # 清空 config.toml 的 [metabase] 段 + 删 session 缓存；幂等

# ── 元数据 ─────────────────────────────────────────────
mbcli databases list                           # GET /api/database → data[]（该端点无分页，一次全量）
mbcli schemas   --db <id|name>                 # 由 db metadata 的 tables[].schema 去重（MySQL 通常仅 ''）
mbcli tables    --db <id|name> [--schema S] [--match SUBSTR] [--include-hidden]
                                               # metadata.tables[] 过滤裁剪（include_hidden 透传，默认 false）
mbcli fields    --table <id|name> [--db D] [--include-hidden]
                                               # GET /api/table/{id}/query_metadata
                                               # （名称需先给 --db 经 metadata 缓存解析成 id）

# ── 查询 ───────────────────────────────────────────────
mbcli query --db <id|name> --native "SELECT …"
            [--param k=v]… [--format …] [--max-rows 1000] [--out F] [--allow-write]
mbcli cards run <id> [--param k=v]… [--format …] [--max-rows] [--out F]

# ── 资产 ───────────────────────────────────────────────
mbcli collections list [--all]                 # 默认滤掉 is_personal（1823 个，一次全量）
mbcli collections show <id|name|root> [--model card|dashboard|collection|dataset]… [--limit] [--offset]
mbcli cards show <id>                          # 含 SQL / MBQL / 参数 / 集合
mbcli dashboards show <id>
mbcli search <QUERY> [--model card|dashboard|collection|table|dataset|metric]… [--db D] [--limit 20] [--offset N]

# ── Agent 增强 ─────────────────────────────────────────
mbcli context --db <id|name> [--table T]… [--max-tables 50]
              [--include-fields-pattern '.*'] [--markdown|--json]
```

说明与关键决策：

- **没有 `cards list`**（全量）：实例卡片量大（`f=all` 6MB+ 超时）。找卡走 `search`，浏览走 `collections show --model card`。
- **执行 SQL 只走 `POST /api/dataset`**：`POST /api/dataset/native` 实为 MBQL→SQL *翻译器*（不执行任何查询），PLAN.md §8 的备选项作废（文档核对）。
- `--model` 可重复（或逗号分隔），透传为 Metabase 的 **`models`（复数）** 参数（search 与 collection items 同名）；单数 `model=` 会被服务端静默忽略。
- `query --param k=v` 映射 native 模板变量：`parameters:[{"type":"category","target":["variable",["template-tag","k"]],"value":"v"}]`；`cards run` 同构（其端点 body 即 `{"parameters":[…], "ignore_cache":false}`）。**M2 限定**：仅 `variable` 目标（field-filter/`dimension` 目标与带参卡片的缺省行为在 M2 实测后定），逃生舱语法预留 `k:type=v`（type ∈ text/number/date）。
- `search --db D` 是**对返回页的客户端过滤**（服务端 search 不支持按库过滤）：可能因 limit 截断而漏配——文档与 `--help` 明示；按库找表的正规路径是 `tables --db D --match`。
- `dashboards show` 输出 `cards[]`（card_id、名称、series、位置、parameter_mappings）+ `parameters[]` + `tabs[]`，不执行任何卡片。
- `context` 默认 markdown（§6）；`--max-tables` 超出时**按表名字典序**取前 N（确定性）；`--json` 输出结构化 schema 树。

## 5. 输出契约（稳定字段名）

> 嵌套对象若在下方以 `…` 省略，表示"白名单透传"：字段集在 `models.py` 固化、由 golden 测试钉死（§7），不随上游漂移。

### 5.1 元数据类

```
databases list → [{"id":7,"name":"bi_data","engine":"mysql","description":null,
                   "is_sample":false,"initial_sync_status":"complete","timezone":"…","dbms_version":"…"}]
schemas        → [{"name":"","table_count":328}]        # 去重结果
tables         → [{"id":3258,"schema":"","name":"app_mau","display_name":"app_mau",
                   "description":null,"estimated_row_count":1234,"active":true,"field_count":8}]
fields         → [{"id":37677,"name":"date","display_name":"date",
                   "base_type":"type/Text","semantic_type":null,"database_type":"VARCHAR",
                   "description":null,"is_pk":false,"is_nullable":true,"is_indexed":false,
                   "fk_target":{"table":"…","field":"…"} | null,"position":1}]
                   # fk_target 取自 query_metadata 中已水合的 target 字段
collections    → [{"id":663,"name":"…","parent_id":null,"authority_level":null,"is_personal":false}]
collections show → {"total":N,"limit":…,"offset":…,
                    "items":[{"id":7876,"model":"card","name":"…","description":null,
                              "collection":{"id":1039,"name":"…"},"display":"scalar","updated_at":"…"}]}
search         → [{"id":7876,"model":"card","name":"数据日期","description":null,
                   "collection":{"id":1039,"name":"…"},"database_id":13,"updated_at":"…"}]
health         → {"status":"ok","version":"v0.66.0-SNAPSHOT"}   # 非 ok 时原样透传 status/progress
```

`estimated_row_count` 一律为**约数**（该实例估值可差 7 个数量级），README 与字段说明均标注"不可依赖，仅排序/筛选用"。

### 5.2 查询信封（query / cards run，json 默认）

```json
{"status":"completed","row_count":42,"rows_returned":42,"truncated":false,
 "columns":[{"name":"one","base_type":"type/BigInteger","database_type":"BIGINT"}],
 "rows":[[1,"x"],…],
 "running_time_ms":123,"cached":false,"data_warnings":[]}
```

- **截断语义（客户端截断，ADR #12）**：服务端忽略一切请求级限量约束（§0 实测），故 `--max-rows N>0` 时传输量由服务端绝对上限决定，mbcli 在内存中截到 N 行；`truncated = 服务端返回行数 > N`；`row_count` = 服务端本响应行数，`rows_returned` = 截后实际输出行数；服务端自身若触顶会在 `data.rows_truncated` 标注，mbcli 将其并入 `data_warnings`。**全量行数在触顶后不可知**——契约不承诺。
- 大结果防护三件套：SQL 自带 `LIMIT`（首选，agent 责任）→ `--max-rows`（输出限量）→ `--out`/`ndjson`（不进 stdout）；M4 备选 `POST /api/dataset/csv` / `POST /api/card/{id}/query/csv|json|xlsx` 服务端流式导出直落盘。
- `ndjson`：首行 `{"columns":[…]}` 信封行，其后每行一个 row 数组，**末行哨兵** `{"truncated":…,"rows_returned":N}`。
- `csv`：列名表头 + 行。

### 5.3 cards show / dashboards show

```
cards show → {"id":7876,"name":"…","description":null,"collection":{"id":1039,"name":"…"},
              "display":"scalar","query_type":"native"|"mbql","database_id":13,
              "sql":"SELECT …" | null,        # dataset_query.native.query
              "mbql":{…} | null,              # dataset_query.query（白名单透传）
              "parameters":[…],"created_at":"…","updated_at":"…","last_edit":{"user":"…","at":"…"}}
dashboards show → {"id":1607,"name":"…","description":null,"collection":{…},
                   "tabs":[{"id":…,"name":…}],
                   "parameters":[{"id":…,"name":…,"slug":…,"type":…,"default":…}],
                   "cards":[{"card_id":12,"name":"…","series":["…"],
                             "position":{"row":0,"col":0,"size_x":4,"size_y":4},
                             "parameter_mappings":[…]}]}
```

## 6. `context` 命令（差异化核心）

输入：`--db`（必填）、`--table`（可多次，缺省=全库，超 `--max-tables` 默认 50 按表名字典序取前 N）、`--include-fields-pattern`（默认 `.*`，防宽表爆炸）。数据源：一次 `GET /api/database/{id}/metadata`。

输出（markdown 默认，目标：直接粘贴进 LLM prompt）：

````markdown
# Database: quant_data (mysql, id=15) — 3 tables shown / 328 total

## Table `app_mau` (id=3258, ~65 rows¹)
> description from Metabase（可为空）

| column | type | semantic | pk | fk → | description |
|---|---|---|---|---|---|
| date | VARCHAR | type/Text | | | |
| uid | BIGINT | type/BigInteger | PK | users.id | 用户外键 |

¹ estimated_row_count 为 Metabase 同步估值，仅作参考
````

`--json` 时输出 `{"database":{…},"tables":[{…,"fields":[…]}]}` 与 §5.1 字段同构。FK 说明列由 `fk_target_field_id` 经 metadata 内字段反查（同响应内可解析，无额外请求）。

## 7. 测试策略

| 层 | 工具 | 覆盖 |
|---|---|---|
| 单元（无网络） | pytest + respx（httpx MockTransport） | **错误规范化全表**（含登录失败→auth_failed、401 重登重放且不递归、纯文本 body）；**write-guard 矩阵**（§3.4 六类用例，参数化）；session 缓存生命周期、**原子写 + 损坏缓存静默重登**、0600 权限；`.env`/config 优先级与**懒凭据校验**；`--max-rows` 截断与 `truncated/rows_returned` 语义；`--fields` 裁剪（含"不裁信封"断言）；**resolver**（id↔name、ambiguous_ref、root、`--table` 名字无 `--db` → invalid_args）；退出码 0/1/2 契约（CliRunner + stderr JSON 断言） |
| **Golden/快照** | pytest + respx 罐头响应 | 每命令一组罐头 payload → 断言**精确 JSON 形状与字段白名单**（钉死 §5 契约，防 models.py 漂移）；CSV/ndjson 边缘（unicode、逗号/引号/换行、截断哨兵） |
| 集成（只读） | `pytest -m integration`，未设 `MBCLI_INTEGRATION=1` 自动 skip | health/登录、`databases list` **≥20 库且含 `quant_data`**（不写死 24）、quant_data ≥300 表、`SELECT 1` 信封、`LIMIT` 查询 + csv 导出、search 关键词、card 7876 的 SQL 回显 |
| 纪律 | CI 不带凭据；集成仅 `SELECT 1` / `LIMIT`；**禁止**任何写操作断言 | — |

## 8. 安全

- 凭据只来自 env/.env/config（`--password` 仅限交互调试；shell history 风险在 README 注明）。
- session 缓存 `0600` 原子写；`-v` 日志对 `Authorization/X-API-Key/X-Metabase-Session` 及 password 字段脱敏。
- `.gitignore` 首个 commit 即含 `.env`/`.venv/`；提交 `uv.lock`。
- 默认只读：write-guard（纵深防御）+ 数据库账号只读权限（真正边界）；测试环境不跑任何 DML/DDL。

## 9. 决策记录（ADR 摘要）

| # | 决策 | 理由 / 放弃项 |
|---|---|---|
| 1 | typer 而非 argparse | 声明式、帮助自动生成；放弃"零依赖"以换可维护性（运行时依赖仍仅 typer+httpx） |
| 2 | uv 管理环境（本次定稿） | 速度、lockfile、`uv tool install` 一步到位；替代 pip/venv/pipx 三件套 |
| 3 | 砍掉 `cards list` 全量命令 | 实测 `f=all` 6MB+ 超时；search + collection items 覆盖同需求 |
| 4 | `--max-rows` 不改写用户 SQL | 保持语义零侵入；限量机制见 #12 |
| 5 | 错误单行 JSON → stderr，退出码 0/1/2 | PLAN §6 契约的落地细化（错误码枚举） |
| 6 | 无 python-dotenv，内置迷你解析 | 依赖最小化；白名单键，~20 行 |
| 7 | 输出字段白名单固化于 models.py + golden 测试 | 防上游版本漂移污染契约 |
| 8 | 401/403/404 容忍纯文本 body | 实测 `Unauthenticated` 非 JSON；文档核对 403/404 同类 |
| 9 | 执行 SQL 只走 `POST /api/dataset` | `/api/dataset/native` 是 MBQL→SQL 翻译器，不执行查询（文档核对，推翻 PLAN.md §8 备选） |
| 10 | 过滤参数统一透传为 `models`（复数） | 实测单数 `model=` 被服务端静默忽略，导致返回未过滤混合结果 |
| 11 | API Key：头 `X-API-Key`、v49+、无过期 | 文档核对（常被误记为 v46）；本实例 0.66 支持 |
| 12 | 限量=客户端截断；`max_results` 服务端方案**实测否决** | `max_results`/`middleware.max-results`/`constraints.max-results` 三形态均被 0.66 忽略（65 行原样返回）；契约改为 `truncated/rows_returned` 显式化 + 三件套防护（§5.2） |
| 13 | 数据塑形选项挂命令级，连接/诊断选项挂根级 | typer callback 选项只能置于子命令前；示例可照抄即运行（评审 blocker 修正） |
| 14 | `fields` 走 `GET /api/table/{id}/query_metadata` | 已水合 FK target 比库 metadata 更丰富；名称解析复用 metadata 缓存（评审 blocker 修正） |
| 15 | 登录失败一律 `auth_failed` | 错密码指向凭据而非参数；401 重登不含登录请求自身 |
| 16 | `CardDetail` 三级解析：pMBQL `stages[].native`（取最后一个）→ legacy `type/native` → `legacy_query` JSON 字符串回退 | 实测 0.66 已返回 pMBQL；回退保证 v57 前旧实例兼容 |
| 17 | `FieldInfo.position` 取 `database_position`（物理列序），缺失回退 UI `position` | DESIGN §5.1 契约；实测两键并存且语义不同 |
| 18 | `connect`/`disconnect` 连接持久化：toml 0600、验证后才落盘、覆盖旧连接需 `--force`、连带废弃 session 缓存；`default_db` 使 `--db` 可省 | 用户需求"连一次、后续零参数"；复用既有配置优先级链，无新机制；明文密码风险已文档化并推荐 API Key |

## 10. 与 PLAN.md 的偏差清单

| 项 | PLAN.md | DESIGN.md | 依据 |
|---|---|---|---|
| 卡片全量列表 | `cards list` | 移除（search / `collections show --model card` 替代） | ADR #3 |
| 命令名 | `card run` / `dashboard show` | `cards run` / `dashboards show` | 与 `cards show`/`collections` 保持名词复数族一致 |
| `--jq` 轻量变换 | §6 可选 | **砍掉**（YAGNI） | `--fields` 覆盖主需求；jq 本体可在管道里用 |
| 输出格式数 | 3（json/csv/table） | 4（+`ndjson`） | agent 流式消费大结果 |
| `/api/dataset/native` 备选 | §8 列为备选 | 明确作废 | ADR #9 |

## 11. 里程碑映射（对 PLAN.md §9 的工程化细化）

- **M0**：uv 脚手架（pyproject/uv.lock/目录骨架）+ config/auth/client/errors/guard/output + `health`/`whoami`/`databases list` + 单测框架 + 退出码契约测试。验收：`uv run mbcli databases list` 输出 ≥20 库 JSON。
- **M1**：resolver + `schemas/tables/fields` + metadata 进程缓存。验收：quant_data ≥300 表、任一表字段全量、FK 明细正确。
- **M2**：`query`/`cards run` + 四格式 + 截断/导出 + write-guard 矩阵 + respx 错误矩阵 + golden 测试。验收：真实 SELECT 信封/csv 正确，0/1/2 契约绿；`--param` 用真实带参卡片实测（开放问题 #1）。
- **M3**：collections/cards show/dashboards/search。验收：关键词→卡片→SQL 回显闭环。
- **M4**：`context` + README + 集成补全 + （备选）服务端流式导出。验收：markdown 摘要直接可用，单测+集成全绿。

## 12. 评审记录与开放问题

**评审**：v1.0 前经独立架构评审（18 条发现：2 blocker / 5 major / 11 minor·nit），全部合入——fields 端点统一（#14）、选项文法（#13）、write-guard 词法化（§3.4）、截断契约显式化（ADR #12）、登录错误归一（#15）、缓存原子写（§3.2）、golden/resolver/CSV 边缘测试（§7）、错误码补全（§3.3）、health 免凭据（§3.1 懒校验）、search --db 页内过滤语义（§4）、`…` 白名单透传规则（§5）、偏差清单（§10）、`--param` M2 限定（§4）、超时语义（§3.1）、write_blocked 语义说明（§3.3）、`--max-tables` 入树（§4）、`--fields` 不裁信封（§3.6）。

**开放问题**：

1. `cards run` / `query --param` 对带参卡片与 field-filter（`dimension` 目标）模板的实测行为——M2 用真实带参卡片验证后决定 `k:type=v` 逃生舱的具体形态。
2. API Key 冒烟：文档已定稿（`X-API-Key`、v49+、无过期），待拿到真实 key 后在实例上跑一次 `whoami` 验证。

## 13. v0.2.0 增量（实测反馈驱动，2026-10-31）

> 来源：真实 Agent 会话（"手机领域数据摸底"任务）9 条反馈；#1/#2/#5 为最高价值。

### 13.1 新命令 `freshness`（数据新鲜度）

```
mbcli freshness --db <id|name> [--table T]… [--max-tables 20] [--sample] [--format …]
```
- **时间列自动识别**（每表选一列，按优先级）：`semantic_type ∈ {type/CreationTimestamp, type/UpdatedAt, type/CreationTime}` > `base_type ∈ {type/DateTime, type/Date}` > 名称匹配 `(^|_)(date|dt|day|month|year|time)$`；平分时取 `database_position` 靠前者。
- **取值来源**：优先读 db metadata 字段 `fingerprint` 的 min/max（**零查询**）；无指纹且给 `--sample` 才对该表发 `SELECT MIN(\`col\`), MAX(\`col\`)`（mbcli 生成的 SQL **一律反引号**标识符）；默认 `--max-tables 20` 防查询风暴。
- 输出：`[{"table","table_id","column","min","max","source":"fingerprint"|"sample"|"none","reason"}]`，支持全部格式。

### 13.2 新命令 `overview`（实例总览）

```
mbcli overview [--top 10] [--format …]
```
- 库×表数：`GET /api/database?include=tables`（实测确认形态；不可用则回退逐库 metadata 并标注 slow）。
- 内容分布：非个人**顶层**集合（上限 50）× `{models=card, models=dashboard}` 各取 `total`。
- 最近更新：各集合 items（limit=100）合并按 `updated_at` 倒序取 `--top`。
- 输出：`{"totals":{databases,collections,cards,dashboards},"databases":[{"id","name","engine","table_count"}],"collections":[{"collection":{id,name},"cards":N,"dashboards":N}],"recent":[{"model","id","name","collection","updated_at"}]}`。

### 13.3 新命令 `batch`（单进程多命令，消 uv 冷启动）

```
mbcli batch < stdin    # 每行一条子命令 argv，如: tables --db 15 --match fut
```
- `#` 注释/空行跳过；进程内共享 client/session/resolver 缓存；禁止嵌套 `batch`。
- stdout = NDJSON，每命令一行：`{"index","argv","exit","error","result"}`（result 为该命令本应输出的 JSON 对象/数组；非 JSON 格式时为字符串）。
- 进程退出码：全部成功 0，任一非 0 则 1。

### 13.4 既有命令增强

- **格式统一**：所有记录数组型命令（databases/schemas/tables/fields/search/collections list/show/freshness）补齐 `--format json|csv|ndjson|table|markdown`（markdown = 管道表，键名做表头）；审计现状补齐缺口。
- **search**：新增 `--match-any "kw1,kw2"`（多关键词各取全集后按 `(model,id)` 去重合并）；`--limit 0` = 拉全集（客户端翻页，页大小 100，硬上限 1000 行防爆炸，超限截断并在输出尾标注）。
- **fields 跨表列搜索**：`fields --search <子串> --db D` —— 扫 metadata 全表字段（零额外请求），输出 `[{"table","table_id","column","base_type","semantic_type","is_pk","fk_target"}]`；标注 MySQL 保留字列（`reserved: true`）。
- **保留字防护**：内置 MySQL 8.0 保留字表；mbcli 自生成 SQL 一律反引号；`bad_query` 且报错 token 命中保留字表时，error.detail 追加提示（"列名 \`year_month\` 为保留字，请用反引号包裹"）。
- **`-v` 日志去向修正**：请求日志必须走 stderr（实测疑似进 stdout 污染数据流——若确认按 bug 修复并补测试）。

### 13.5 SKILL.md 修正

运行器示例改 zsh 安全的 shell 函数：`mb() { uv run --no-project --with typer --with httpx python mbcli.py "$@"; }`（原 `MB="uv run …"; $MB …` 在 zsh 下 exit 127）。

| ADR | 决策 |
|---|---|
| 19 | freshness 零查询优先（fingerprint），采样查询显式 opt-in 且限量 | 反馈 #1：摸新鲜度是最高频动作，不能默认产生 328 条 SQL |
| 20 | batch 输出 NDJSON 包装而非拼接 stdout | 保持"stdout 只有数据"可流式解析；uv 冷启动成本摊平到多命令 |
| 21 | 多关键词/列级搜索在客户端归并（服务端无此能力） | 反馈 #4；列搜索复用 metadata 缓存零请求 |
| 22 | 保留字只做"生成侧反引号 + 错误侧提示"，不改写用户 SQL | 与 ADR #4（零侵入）一致 |

## 14. v0.3 待办（两轮实弹评测沉淀，均为打磨级；评测循环已收敛）

> 来源：R1 消费板块 7/10、R2 汽车板块 9/10（零 P0/P1 收敛）。P0/P1 类问题已全部在 v0.2.1/v0.2.2 修复。

| # | 项 | 说明 | 备注 |
|---|---|---|---|
| 1 | 输出形状统一或速查表 | 裸数组（tables/search）vs 信封（collections show/query）并存；至少文档给形状速查 | 契约级，需设计 |
| 2 | `tables`/元数据列表加 `--limit` 或默认截断+truncated | `--db bi_data` 倾倒 466 条刷爆上下文 | |
| 3 | `--format table` 自适应列宽 / `--width`；浮点降噪 | 定宽 12 字符截断、`472.98760000000004` 直出 | output.py |
| 4 | `"Not found."` 英文透传归一化 | 与结构化中文错误风格割裂 | client 错误规范化 |
| 5 | `write_blocked` 独立退出码（如 3） | 与 invalid_args 共用 exit 2，脚本无法区分"打错字"与"被安全策略拦" | **契约变更**，需 v2 级决策 |
| 6 | `schemas` 对 MySQL 单库空名无信息量；`context` 行数缺失时省略 `~? rows¹` 占位 | | |
| 7 | `dashboards show` 文本/标题卡标 `type:"text"` 或过滤 | 现输出 `{card_id:null,name:null}` 行，易误判数据卡损坏 | R2 新增 |
| 8 | `cards run` 省略已声明可选参数时 stderr 提示口径 | 裸跑=服务端省略过滤子句，与过滤结果同为 74 行易误读 | R2 新增；stderr info 不破坏契约 |
| 9 | freshness 时间值格式归一 | DATETIME 带 ISO 时区 vs DATE 裸日期并存（实例 schema 混用所致，可文档化缓解） | R2 观察 |
| 10 | 实例侧数据治理（非工具问题，记录） | 日期列 VARCHAR、description 全空、空壳库、卡片命名随意、同名仪表盘重复 | 反馈给实例管理员 |
