# 测评操作手册

## 1. 安装环境和准备题库

在仓库根目录运行：

```bash
uv sync
```

`data/gdpval.parquet` 保存 GDPval 的 220 题全集；`data/task_ids.txt` 每行一个题目 ID，顺序对应第 1、2、… 题。正式测评期间保留此顺序。题目选择与题包保存在 gitignored 目录。

首次获取题库：

```bash
mkdir -p data
curl -L --fail -o data/gdpval.parquet \
  "https://huggingface.co/datasets/openai/gdpval/resolve/11e7900cdcac61bc4daf59e65feb238acda98fbf/data/train-00000-of-00001.parquet"
```

## 2. 一键展开三产品题包

```bash
uv run python evaluate.py prepare
```

默认生成以下目录；`t_01` 表示题目清单的第 1 行：

```text
tasks/
  manifest.json                脚本维护的题目与输入校验记录
  metrics.csv                  人工填写，三产品 × 每题一行
  豆包工作/
    t_01/
      START.txt                桌面办公工具的启动指令
      prompt.txt               完整原始题面
      reference_files/         本题参考附件，保留原始文件名
      output/                  本题最终答案，可包含多个文件
    t_02/
    ...
  QwenWork/
    t_01/ ...
  WorkBuddy/
    t_01/ ...
```

参考附件下载到 `data/references/` 后复制到各题目录，三个产品各自编辑独立副本。每个题包包含题面和参考附件，评分条件和专家答案由判分端读取。下载失败可重跑；完整附件复用本地缓存。重复备题保留已有答案与人工记录；输入发生改动时停止并提示核对。

新一轮测评用另一个根目录：

```bash
uv run python evaluate.py prepare --root tasks/round-02
```

后续判分使用相同的 `--root tasks/round-02`。自定义到 `tasks/` 以外的路径时，将该目录加入本机 Git 忽略规则。更换题目或调整题序后，用新根目录重新备题。

## 3. 人工操作每道题

1. 开跑前统一各产品的版本、模型档位、时限、联网范围、可用技能、追问与重试规则，在人工记录的 `notes` 中注明设置和例外。
2. 使用专门的测评配置，关闭跨会话记忆及历史引用。每个产品的每道题新建一个会话，并选择该题目录作为 working directory，例如 `tasks/WorkBuddy/t_01/`。
3. 把该目录 `START.txt` 的内容复制到办公工具发送。工具将读取原始题面和附件，将最终文件写入 `output/`。网页型工具则新建会话，上传该题全部参考附件，粘贴完整 `prompt.txt`；完成后将下载的最终文件放回该题的 `output/`。
4. 人工检查最终交付物能打开，并确认来自当前题的会话。`output/` 接收 XLSX、DOCX、PPTX、PDF，可包含多个最终文件。系统元数据与 Office 临时锁文件自动略过。过程文件、备份、截图与说明放在题目录的其他位置；最终交付格式按原题要求。
5. 在 `metrics.csv` 更新当前产品、当前题对应的一行。保存 CSV 后执行判分。

工作空间授权到单题目录，参考附件保留原样，编辑时使用副本。记忆关闭、新会话和独立目录共同减少串题；严格的跨目录访问隔离由文件权限或独立虚拟机提供。脚本核对题包和会话记录，产品实际读取了哪些上下文需由操作人确认。

WorkBuddy 官方说明：[工作空间与权限](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Permission-Modes)、[记忆设置](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Memory)。其他产品按当前界面检查相应设置。

## 4. 人工填写 metrics.csv

用 Excel、Numbers 或文本编辑器打开 `tasks/metrics.csv`，保存为 UTF-8 CSV，保留列名、行身份和全部行。支持调整行顺序。

| 列 | 填法 |
|---|---|
| `product` | 保留备题值：豆包工作、QwenWork、WorkBuddy |
| `task` | 保留题序号，1..N |
| `status` | `pending` 未开始；`running` 进行中；`completed` 已收取答案；`failed` 人工确认超时、拒绝或无法交付 |
| `session` | 本题会话链接或真实 ID；completed、failed 必填；同一产品每道题使用不同会话 |
| `elapsed_seconds` | 本题从发送指令到最终交付或失败的墙钟耗时，单位秒，可填小数 |
| `tokens` | 产品显示的本题输入与输出 token 合计，非负整数 |
| `notes` | 软件版本、模型档位、时限、人工介入、失败原因等 |

空白表示未知，`0` 表示确认消耗为零。产品只显示积分或金额时，token 留空，在 notes 记录原始单位。各产品采用一致的耗时起止点；有重试时，在 notes 记录次数及时间是否包括重试。

合成示例：

```csv
product,task,status,session,elapsed_seconds,tokens,notes
WorkBuddy,1,completed,synthetic-session-01,135.5,12400,首次交付
```

脚本检查非负数、有限耗时、整数 token、完整行集合及会话重复。只更新耗时或 token 后重新运行判分，会复用相同答案的缓存并刷新报表。

## 5. 判分和生成排行榜

配置 OpenRouter 凭证（可用环境变量，也可保存于用户配置目录的 `openrouter.key`）：

```bash
export OPENROUTER_API_KEY='替换为自己的 key'
```

先完成每产品前两题，再验证两种判分口径与真实费用：

```bash
uv run python evaluate.py run --limit 2
```

全部题目跑完后：

```bash
uv run python evaluate.py run
```

默认同时运行 rubric 与 pairwise。按需单独运行：

```bash
uv run python evaluate.py run --mode rubric
uv run python evaluate.py run --mode pairwise
```

默认 judge 为 `deepseek/deepseek-v4-pro-0813`，provider 固定 DeepSeek。`--model` 可指定该 provider 支持的其他模型。真实调用收费；20 题每产品最多涉及 891 条 rubric 判定及 20 次专家对比，缓存命中可减少调用。

每个题根目录对应独立的 `results/<根目录名>-<路径摘要>/`，终端会输出具体位置：

| 文件 | 内容 |
|---|---|
| `scores.csv` | 每产品每题的两种分数、状态、耗时、token、会话、备注、答案校验值和 judge 费用 |
| `leaderboard.csv` | 排名、完整性、总分、胜率、总耗时、总 token 及人工指标填写覆盖数 |
| `report.md` | 可阅读的排行榜、逐题结果及评分口径 |
| `<产品>.cache.jsonl` | 可中断续跑的逐条 judge 判定 |

评分状态与总分：

- `graded`：当前材料与答案已成功判分。
- `failed`：人工确认产品运行失败，所选口径计零分。
- `pending`、`running`、`not_evaluated`：未完成或不在本次 limit 内，分数留空。
- `error`：输入被改动、缺答案、格式不支持、提取失败、judge 出错等，分数留空，原因在 notes。任一题为 error 时命令退出码为 1，其他可判题仍执行并导出报告。

rubric 每题 5 分，N 题满分为 5N。pairwise 每题取 0、0.5、1，1 表示产品答案更好；汇总取平均值。完整评测的产品才参与排名，未完成整套题的总分留空；`--limit 2` 保留整套题的分母与未评状态。

两种口径并存时按 rubric 总分排名；单独运行 pairwise 时按胜率排名；同分并列。每一项人工指标在全部题目填写后才显示总计，覆盖数显示已填题数。judge 费用与产品 token 分列，缓存费用是当前有效判定的历史调用费用；失败调用和已被替换答案的费用以 OpenRouter 账单为准。

报告是该题根目录最近一次 run 的结果。使用相同的 mode 重新运行可更新人工指标；切换 mode 或 limit 后报表反映新的运行范围。

## 6. 改答案、重评与核对

同一题修正最终文件后重新运行，缓存由题目、材料、交付文件内容、评分提示词和 judge 配置共同确定，变化的答案会重判。改动 reference_files 或 prompt 时会报告输入不一致；核对原始材料后重新创建独立题包再测。

整轮重试用新的 `--root`，保留原轮的答案与人工指标。每轮都由人确认：题序号 → 工作目录 → 会话 → 最终文件。脚本可检测输入变化与重复会话；人工将其他题的答案复制到 output 时，文件来源仍需操作人核对。

当前 judge 使用提取文本，包含原题、参考材料和产品答案；pairwise 另加入专家答案。图表、图片、页面布局、扫描 PDF、Excel 公式实际计算和文件可用性需人工复核。单份/单组提取文本上限 2,000,000 字符，Excel 上限 200,000 单元格，PDF 上限 500 页；文本为空或达到上限时本题报错。模型实际上下文容量也受所选模型限制。人工验收意见填在 notes；最终发布横评报告前抽查 judge 判定。

## 开发与旧交付物入口

```bash
uv run pytest -q
uv run ruff check .
```

CI 在 push 与 PR 上运行上述检查，根目录 `test_*.py` 自动纳入现有 pytest job。

已有平铺交付物可继续使用 `grade.py`：

```bash
uv run python grade.py rubric --dir submissions/doubao --product doubao
uv run python grade.py pairwise --dir submissions/doubao --product doubao
```

此入口按 `t01.xlsx` 等文件名查找，每题一个文件，缺失或解析失败计零分。三产品目录流程使用 `evaluate.py`，具有材料校验、人工记录和未完成状态。两种入口的缓存均随答案或 judge 配置变化而失效。

在 main 上开发；commit 保存在本地，push 由用户明确要求。
