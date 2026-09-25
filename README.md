# 螺纹紧固件召回监控（fastener-recall-monitor）

每天盯住中国、美国、日本、德国四路官方召回信息，从中找出**缺陷本体是螺纹紧固件**的机动车召回，核对确认后把增量写入简道云，并留下一条当日运行日志。

## 业务说明

**数据源（四路，均为官方来源，历史全量已入表 + 每日新增）**：

| 地区 | 来源 | 覆盖 |
|---|---|---|
| 中国 | [市场监管总局召回栏目](https://www.samr.gov.cn/zw/zh/)官网公告 | 全量历史基线 + 每日新增 |
| 美国 | [NHTSA](https://www.nhtsa.gov/recalls) 官方召回数据集 | 2019-2026 全量历史 + 每日增量（官方主数据，覆盖完整） |
| 日本 | [国土交通省](https://www.mlit.go.jp/jidosha/recall.html)自动车召回/改善情报 | 2019-2026 全量历史 + 每日增量（公告列表及 PDF） |
| 德国 | [KBA 召回数据库](https://www.kba-online.de/rrdb/buerger/#/) | 2019-2026 全量历史 + 每日增量（官方 cars+parts CSV 导出） |

海外三路（美国、日本、德国）历史已全部完成入表，2026 年起逐日增量监控，按国家分别记录。官网是唯一来源，不用转载媒体或搜索摘要。

**判定口径**：缺陷本体为螺栓、螺母、螺钉、螺丝、螺纹等紧固件，且存在松动、断裂、脱落、扭矩不足、漏装、错装等缺陷的机动车召回；仅在维修中使用紧固件的不算。口径可在核对提示词中调整。

**输出去向**：符合条件的召回行增量写入简道云“召回数据”表（含国家字段）；每天无论有无新增，都写一条简短运行日志到“运行日志”表。

## 数据流程

```text
各官方源列表/数据集
   │ 与本地台账及内容指纹比对
   ├─ 无新增 ──────────────→ 写当日日志，结束
   └─ 有新增或修订
        │ 只核对本批变化公告的正文
        │ 两路独立提取公司、车型、数量、缺陷本体
        ├─ 不符合口径或存疑 ──→ 不上传，仅记入台账
        └─ 确认符合 ─────────→ 增量上传召回行
                              写运行日志，提交台账
```

业务规则要点：

- 只核对当批新增/修订，历史公告不重复分析；被排除的公告同样记入台账，次日不会重新分析。
- 同一全球召回可能在多国公告（如中国公告的进口车型与其原产国公告重叠）：以品牌或车型的原产国首发公告为准；非原产国公告视为同一召回的传播，召回数量不计、行不入表。
- 无新增时不读公告正文、不启动核对、不扫描远端数据表。
- 正文核对必须两路独立完成且结论一致；存疑不入表，保留待人工处理。
- 抓取失败、空列表或覆盖不足都按该源失败报告，不能当作“无新增”。
- 已上传记录的修订按同一官方编号更新；上传失败保留进度，下次从断点恢复。

## 使用

本仓库 CLI 实现国内（市场监管总局）部分的采集、判定与上传；海外三路在私有部署脚本中运行，仓库不含其代码、凭据或数据。

需要 Python 3.11+，运行时仅用标准库。

```sh
git clone https://github.com/zhenyedl/fastener-recall-monitor.git
cd fastener-recall-monitor
python -m venv .venv && . .venv/bin/activate   # Windows: .\.venv\Scripts\Activate.ps1
python -m pip install .
cp config.example.json config.json
```

1. 在自己的简道云建“召回数据”和“运行日志”两个表单，把应用、表单、字段 ID 填入本地 `config.json`，并为进程设置 `JDY_API_KEY` 环境变量。详见[简道云配置](docs/jiandaoyun.md)。
2. `recall init` 初始化私有状态目录（默认 `.recall/`）。
3. 每日运行 `recall run --url https://www.samr.gov.cn/zw/zh/ --since 上次边界日期 --config config.json`。退出码 `3` 表示有新增待核对：阅读 NEW 公告正文，按[数据格式](docs/data-format.md)保存判定，再执行 `recall review` 和 `recall sync` 完成本批。
4. 定时运行（Windows 任务计划程序、Linux/macOS 调度或代理调度）见[调度说明](docs/scheduling.md)；代理提示词见 [prompts/daily-task.md](prompts/daily-task.md)。

## 隐私

仓库不包含任何使用者的简道云配置、凭据、历史台账或业务数据；`config.json`、`.recall/` 等运行状态均被 Git 忽略。提交前可用 `python tools/check_public.py` 复查。安全细节见 [SECURITY.md](SECURITY.md)，许可证 [MIT](LICENSE)。
