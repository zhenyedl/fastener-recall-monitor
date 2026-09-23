# Recall

每日检查市场监管总局机动车召回公告，**只核对新增公告、只上传确认后的增量**，并把简短运行日志写入简道云。

适用于“缺陷本体为螺纹紧固件”的召回监控，也可在核对提示词中调整判定口径。仓库不包含任何使用者的简道云配置、凭据、历史台账或业务数据。

## 工作方式

```text
官网列表元数据 → 与本地台账/历史基线比较
                  ├─ 无新增 → 写当天简短日志 → 完成
                  └─ 有新增 → 只核对 NEW → 保存判定
                                           ├─ 排除/存疑 → 不上传召回行
                                           └─ 确认入表 → 增量上传
                                      写运行日志 → 提交台账 → 完成
```

- “已排除”的公告也记入台账，次日不会因为没入表而重复分析。
- 无新增时不读公告正文、不启动核对子代理、不扫描远端召回数据表。
- 有增量时读取远端记录做幂等对照；这不是重新分析历史公告。
- 上传进度逐条保存；请求结果不确定时先查询远端，再决定是否重试。
- 判定、运行时间和新增/推送数量分别记录。多行文本采用“第 N 条 / 字段：内容”，不使用 Markdown 表格。
- HTTP 403、空列表、动态页面空壳、过期快照或覆盖不足均报错，不能当成“无新增”。

**分析不是内置大模型 API 调用。** Python 负责状态、差集、校验及上传；新公告的全文阅读和双路核对由人工或具备工具权限的代理完成。仅设置系统定时器不会自动生成判定。有新增时 CLI 返回待核对状态，必须处理后才能完成本批。

## 安装

需要 Python 3.11+、Git；支持 Windows、macOS 和 Linux。运行时仅使用 Python 标准库。

```sh
git clone https://github.com/zhenyedl/Recall.git
cd Recall
python -m venv .venv
```

Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install .
Copy-Item config.example.json config.json
```

macOS / Linux：

```sh
. .venv/bin/activate
python -m pip install .
cp config.example.json config.json
```

也可不安装包，直接在仓库根目录使用 `python -m recall` 替代 `recall`。

## 配置简道云

1. 在自己的简道云中准备“召回数据”和“运行日志”两个表单。
2. 将应用 ID、两个表单 ID、字段 ID 填入本地 `config.json`。配置只提供占位符，需全部替换。
3. 在执行任务的进程环境中设置 `JDY_API_KEY`。不要把密钥填入源码、任务提示词或命令行参数。
4. 如日志需过滤自己的目录名、内部名称，可在 `private_terms` 中添加文本。

字段类型与配置说明见 [简道云配置](docs/jiandaoyun.md)。代码没有自动读取 `.env` 的行为；`.env.example` 仅说明环境变量名称。

PowerShell 可避免将密钥明文写入命令历史：

```powershell
$recallSecret = Read-Host "JDY API key" -AsSecureString
$env:JDY_API_KEY = [System.Net.NetworkCredential]::new("", $recallSecret).Password
```

Bash：

```bash
read -rs -p "JDY API key: " JDY_API_KEY; echo
export JDY_API_KEY
```

定时运行时由调度器或系统凭据设施为进程注入环境变量。本项目不会上传、创建或变更简道云表单结构。

## 初始化

```sh
recall init
```

默认私有状态目录为当前工作目录的 `.recall/`；可用环境变量 `RECALL_HOME` 或前置参数 `recall --home PATH ...` 指定。

已有历史数据时，可以在**确认历史元数据已核对**后初始化基线：

```sh
recall --home private/state init --baseline private/reviewed-baseline.json
```

基线格式是 `date/title/url` 对象数组。不要用尚未核对的今日列表作为基线，否则它会被当成历史跳过。初始化不会覆盖已有台账；仓库没有附带任何真实基线。

## 每日流程

### 1. 获取列表元数据

优先读取[市场监管总局召回栏目](https://www.samr.gov.cn/zw/zh/)。`--since` 指定必须覆盖到的历史重叠日期；下面日期仅为演示，应替换为自己的有效检查边界。

```sh
recall fetch --url https://www.samr.gov.cn/zw/zh/ --since 2020-01-01 --output .recall/today.json
```

若需翻页，按顺序重复传入每个官方列表页的 `--url`。收集器支持明确的 JSON 元数据及带日期的 HTML 列表项，不自动推测网站动态 API 或分页 URL。提供完整页面序列是操作者/调度代理的责任；日期覆盖检查不能证明中间页没有遗漏。

如果官网返回 403 或页面依赖 JavaScript，用浏览器/代理从官网取得完整的标题、日期、链接数组，保存为 `.recall/list.json`，再确认覆盖范围：

```sh
recall snapshot --input .recall/list.json --url https://www.samr.gov.cn/zw/zh/ --since 2020-01-01 --confirm-complete --output .recall/today.json
```

本命令只记录操作者的完整性确认，不会再次联网验证。不要用搜索摘要或媒体转载替代官网完整列表。工具会拒绝旧日期的快照供新批次使用。

### 2. 判定新增

```sh
recall prepare --snapshot .recall/today.json
```

退出码 `3` 表示有 NEW，需要核对；不是运行故障。输出只有本批新增公告和历史数量。若存在未完成批次，优先恢复它，不创建另一批。

### 3. 仅有 NEW 时核对

打开 NEW 的官网正文；两路独立提取日期、公司、车型、数量、缺陷本体与结论。两路一致且主代理核实后，才能把确认的增量行放入 `rows` 并声明 `crosscheck_agreed: true`。

判定示例见 [数据格式](docs/data-format.md)。保存到 `.recall/reviews.json` 后：

```sh
recall review --input .recall/reviews.json
```

无 NEW 时跳过此步。存疑公告不入表，保留说明待人工处理；本版本不会自动重开已完成的存疑判定。

### 4. 验证并上传

```sh
recall sync --dry-run
recall sync --config config.json
```

`--dry-run` 不访问简道云、不需要凭据、不改变批次/台账，只输出校验和预览。正常上传即使没有新增也会写当日简短日志。全部召回行和日志确认成功后才将判定提交到台账。

本通用版本直接以判定 JSON 的增量 `rows` 为上传来源，不依赖 Excel；不附带或更改任何私人工作簿，也不负责原业务流程中的附件归档。

## 定时运行

`run` 合并“抓取/载入列表 → 判新增 → 无新增时写日志”：

```sh
recall run --snapshot .recall/today.json --config config.json
```

也可使用 `run --url OFFICIAL_LIST_URL --since YYYY-MM-DD --config config.json` 从官方列表开始。

| 退出码 | 含义 | 下一步 |
|---|---|---|
| 0 | 当前动作成功 | 查看状态；完成批次已归档 |
| 1 | 抓取、配置或上传失败 | 保留状态，排查后重试 |
| 2 | CLI 参数错误 | 修正命令 |
| 3 | 有新增、等待核对 | 只核对 NEW，然后 review / sync |
| 4 | 旧批次恢复成功，今天还需检查 | 再抓取今天的列表运行一轮 |

使用同一状态目录、同一执行用户，避免重复初始化。任务进程有互斥锁；原子替换保护状态文件，但仍应定期备份私有状态。不同电脑必须由调度层保证不会同时向同一表单处理同一批；本地锁不是分布式锁。

[代理定时提示词](prompts/daily-task.md) 提供 GLM 主代理 + DeepSeek/Kimi 子代理的流程。[系统调度示例](docs/scheduling.md) 包含 Windows 和 Linux 安装说明。仓库 CI 只做测试，不自动运行召回采集或访问简道云。

## 项目结构

```text
recall/                 状态、官网元数据、CLI、简道云同步
tests/                  隔离测试；禁止真实网络调用
prompts/                代理日常任务提示词
docs/                   安装、表单、数据格式、调度说明
tools/check_public.py   提交文件隐私检查
config.example.json     空白安装配置示例
```

私有运行目录包含 `ledger.json`、`baseline.json`、`batches/`、`snapshots/` 和 `logs/`，均不应提交到 Git。

## 验证与安全

```sh
python -B -m unittest discover -s tests -v
python tools/check_public.py
```

- 所有测试使用虚构记录和内存接口，不消耗真实 API，不会往真实简道云写测试数据。
- `.gitignore` 排除配置、凭据、运行状态、日志、工作簿和备份；检查器额外扫描待提交/已跟踪文件。
- 错误输出不包含 API Key、请求头、响应原文或配置内容。
- 真实字段映射和账号信息只放本机；不要在 issue、截图或 CI 日志中粘贴它们。
- 详情见 [SECURITY.md](SECURITY.md)。

## 已知边界

官网页面结构与访问策略会变化。采集器的失败状态必须人工或代理处理，不能按“0 新增”跳过。正文核对是必需环节，本仓库不提供无人监督的大模型自动分类承诺。远端创建的断线重试依靠已保存进度和查询去重；不能保证多个独立部署同时写入时的全局 exactly-once。

许可证：[MIT](LICENSE)。
