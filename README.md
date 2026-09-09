# GDPval 办公产品横向测评

用 OpenAI 公开的 GDPval 题库，对豆包工作、千问办公（QwenWork）和 WorkBuddy 的办公交付物做横向测评。当前测评从 220 题中选取 20 题，覆盖 XLSX、DOCX、PPTX、PDF 文件交付。

操作人在各产品中完成任务、收取文件并记录耗时和 token；脚本负责生成独立题包、校验材料、调用模型判分，以及汇总排行榜。产品做题环节由人工操作。

## 评分口径

| 口径 | 方法 | 汇总 |
|---|---|---|
| `rubric` | 按题库评分条件逐条判定并加权，含扣分项 | 每题 5 分，20 题满分 100 分 |
| `pairwise` | 将产品交付物与题库专家交付物做两两比较 | 胜 / 平 / 负记为 1 / 0.5 / 0，取平均胜率 |

默认同时运行两种口径，按 rubric 总分排名；仅运行 pairwise 时按胜率排名，同分并列。整套题均已判分或经人工确认失败的产品才参与排名。人工确认失败计零分，未完成或判分异常留空。

当前 judge 通过 OpenRouter 调用，代码默认模型为 `deepseek/deepseek-v4-pro-0813`，provider 固定为 DeepSeek。判分依据提取文本；图表、页面布局、Excel 公式实际计算和文件可用性由人工复核。

## 快速开始

需要 Python 3.11+、uv，以及判分时使用的 OpenRouter API key。以下命令均在仓库根目录运行。

### 1. 安装依赖、准备题库

```bash
uv sync
mkdir -p data
curl -L --fail -o data/gdpval.parquet \
  "https://huggingface.co/datasets/openai/gdpval/resolve/11e7900cdcac61bc4daf59e65feb238acda98fbf/data/train-00000-of-00001.parquet"
```

在本地准备 `data/task_ids.txt`：每行一个题库中的 `task_id`，各行互不重复，行顺序对应题序号。参与现有测评时使用本地测评清单；开展自己的测评时自行选题。此文件由操作人提供，仓库中的脚本按该清单备题。

题库和素材公开；**本轮选中的题目身份保留在本地**，以免产品方针对性优化。`data/`、`tasks/`、`results/`、`submissions/` 已加入 Git 忽略规则，对外用题序号指代题目。

### 2. 生成三产品题包

```bash
uv run python evaluate.py prepare
```

```text
tasks/
  manifest.json          题目映射与输入校验记录
  metrics.csv            人工记录表，每产品每题一行
  豆包工作/t_01/
  QwenWork/t_01/
  WorkBuddy/t_01/
    START.txt            桌面工具启动指令
    prompt.txt           原始题面
    reference_files/     参考附件，保留原始文件名
    output/              最终交付物，可放多个文件
```

每个产品均生成清单内的全部题目。重复备题会核验原始输入，并保留已有答案和人工记录。

### 3. 在产品中做题并记录

1. 统一各产品的模型档位、时限、联网与重试规则，关闭跨会话记忆及历史引用。
2. 每产品每题新建独立会话。桌面工具以单题目录为工作目录，发送 `START.txt`；网页工具上传该题参考附件并发送完整 `prompt.txt`。
3. 将最终 XLSX、DOCX、PPTX 或 PDF 文件放入该题 `output/`，人工检查文件能正常打开。
4. 更新 `tasks/metrics.csv`：填写 `status`、`session`、`elapsed_seconds`、`tokens` 和 `notes`，保留原有产品、题序号与全部行。

`status` 可填 `pending`、`running`、`completed`、`failed`；后两者必填会话链接或真实 ID，同一产品各题会话保持唯一。耗时单位为秒，token 为非负整数；指标空白表示未知，`0` 表示确认值。详细记录要求见 [测评操作手册](DEVFLOW.md)。

### 4. 判分并查看报告

```bash
export OPENROUTER_API_KEY='替换为自己的 key'

# 各产品完成前两题并填写记录后，先试跑，核对判定和费用
uv run python evaluate.py run --limit 2

# 完成整套题后，生成完整排行榜
uv run python evaluate.py run
```

真实 judge 调用产生费用。相同材料、答案与判分配置会复用缓存；修改答案后重跑会重新判定受影响的内容。`--limit 2` 保留整套题的分母，其余题标记为未评。

终端会输出报告位置，文件保存在 `results/<题包根目录名>-<路径摘要>/`：

| 文件 | 内容 |
|---|---|
| `report.md` | 排行榜、逐题结果与评分口径 |
| `scores.csv` | 每产品每题的分数、状态、人工指标与 judge 费用 |
| `leaderboard.csv` | 总分、胜率、耗时与 token 总计及填写覆盖数 |
| `<产品>.cache.jsonl` | 支持中断续跑的判定缓存 |

每次运行更新该题包的报告。判分异常记为 `error`，命令退出码为 1，其他可判题仍会执行并导出结果。

按需使用 `--mode rubric` 或 `--mode pairwise` 单独判分。新一轮测评可在 `prepare` 和 `run` 中都指定 `--root tasks/round-02`，为该轮保留独立题包、人工记录和结果。

## 开发与文档

```bash
uv run pytest -q
uv run ruff check .
```

项目采用根目录平铺 Python 脚本：`evaluate.py` 负责备题与报告，`grade.py` 负责判分与缓存，`extract.py` 提取办公文件文本，`llm.py` 调用 judge。测试使用合成数据；CI 执行 pytest 与 Ruff。

| 文档 | 内容 |
|---|---|
| [DEVFLOW.md](DEVFLOW.md) | 完整操作步骤、人工记录、重评、开发命令及平铺交付物入口 |
| [PROJECT.md](PROJECT.md) | 功能状态、评分模型与模块职责 |
| [PATTERNS.md](PATTERNS.md) | 输入校验、状态、缓存与文件处理约定 |
| [TECHSTACK.md](TECHSTACK.md) | 依赖、目录与外部服务 |
| [AGENTS.md](AGENTS.md) | 仓库协作规则 |
| [BACKLOG.md](BACKLOG.md) | 当前待办 |
