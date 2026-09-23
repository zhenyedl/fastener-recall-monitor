# 本地调度

任务要在**固定工作目录**执行，使用虚拟环境中的绝对解释器路径、持久状态目录和由运行用户可访问的私有配置。不要为每次运行建立新的空台账。

## 代理调度（包含分析）

将 [daily-task.md](../prompts/daily-task.md) 放入支持浏览器、文件操作及子代理的调度环境。
主代理使用 GLM，两个核对子代理使用 DeepSeek 和 Kimi。按平台支持情况启用委派。
Python 的退出码 3 是代理接管正文核对的入口；退出码 4 要再执行一次今日检查。

本仓库的 GitHub Actions 仅测试，不包含采集定时器，也不需要配置任何简道云 Secrets。

## Windows 任务计划程序（仅 CLI）

创建任务时自行选择执行时间和用户：

- 程序：部署目录中的 `.venv\Scripts\python.exe` 的绝对路径。
- 参数：`-m recall --home PRIVATE_STATE_DIR run --url OFFICIAL_LIST_URL --since OVERLAP_DATE --config PRIVATE_CONFIG_PATH`。
- 起始目录：项目部署目录。
- 为该执行用户安全设置 JDY_API_KEY；不要放进参数、XML 或公开脚本。

把三个大写占位符替换为你自己的值；重叠日期至少回到上次成功检查边界。官网动态列表无法直接解析时，应改用代理调度获取完整快照。

任务计划程序若报告退出码 3，应通知核对人员/代理，然后执行 review / sync。不要将其误认为“没有新增”。本 CLI 不会自己调用大模型服务。

## Linux/macOS 调度（仅 CLI）

示例启动脚本放在本机私有目录，变量值自行配置：

```sh
#!/bin/sh
set -eu
: "${RECALL_PROJECT:?set deployment directory}"
: "${RECALL_HOME:?set private state directory}"
: "${RECALL_CONFIG:?set private config path}"
: "${RECALL_OVERLAP:?set overlap date YYYY-MM-DD}"
: "${JDY_API_KEY:?inject API key securely}"
cd "$RECALL_PROJECT"
exec .venv/bin/python -m recall --home "$RECALL_HOME" run \
  --url https://www.samr.gov.cn/zw/zh/ \
  --since "$RECALL_OVERLAP" --config "$RECALL_CONFIG"
```

由系统调度器每天调用该脚本，并监测退出码。重叠日期和分页列表需按现场情况维护，不要把脚本示例当作对官网完整性的保证。

## 恢复与备份

- 定期备份 RECALL_HOME，特别是 ledger、baseline 和尚未完成的 batches。
- 批次 reviewed 后失败，直接 sync；不要再次判定 NEW。
- 无新增时照常写一条日志，但抓取失败只有本地错误记录，不伪造远端“无新增”日志。
- 多台设备不可同时使用独立状态向同一表单处理相同来源；需单一调度者或外部分布式互斥。
- 不要将运行状态放进公开 Git 仓库，即使它暂时没有密钥。
