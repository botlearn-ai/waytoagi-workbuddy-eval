# PROJECT.md

## 目的

对豆包工作(字节)、千问办公(阿里)、WorkBuddy(腾讯云)做横向测评。题目取自 OpenAI 公开数据集 GDPval(huggingface.co/datasets/openai/gdpval)220 题中选出的 20 题。三个产品没有公开 API,由人在产品界面上做题、把交付物存成文件,判分由本仓库的脚本完成。

## 判分的两种口径

| 口径 | 依据 | 输出 | 调用量(每产品) |
|---|---|---|---|
| `rubric` | 数据集自带的 `rubric_json` 评分点,逐条判成立/不成立 | 百分制(20 题 × 5 分) | 891 次 |
| `pairwise` | 与数据集自带的专家交付物两两对比 | 胜率(取值 0 / 0.5 / 1) | 20 次 |

`pairwise` 是 GDPval 官方论文采用的方法,grader 取值 `{0, 0.5, 1}`,1 表示被测产品的交付物更好。官方论文全文未提及 rubric,但该字段确实在数据集里,由出题专家手写(`author_type=human`),20 题合计 891 条。两种口径共用同一套文件解析与 judge 调用,结果互为对照。

## 功能状态

| 内容 | 状态 |
|---|---|
| 四格式交付物转文本(xlsx/docx/pptx/pdf) | 完成 |
| rubric 逐条判分 + 百分制汇总 | 完成,23 例测试 |
| pairwise 对比判分 + 胜率汇总 | 完成,23 例测试 |
| 判定缓存(重跑不重复付费) | 完成 |
| 三产品实际做题 | 未开始,一道题未考 |

## 核心数据模型

- **Task** — 一道题。`ordinal`(题序号 1..20,对外指代用它)、`task_id`、`occupation`、`prompt`、`rubric: tuple[Item]`、专家交付物的 URL 与文件名。`total` 属性 = 全部正分之和。
- **Item** — 一条评分点。`item_id`、`criterion`、`score`(可为负;20 题里有 2 条负分)。
- **题分** — `5 × max(0, 得分) / 满分基数`。满分基数为 0 时记 0 分。
- **交付物命名** — `<dir>/t01.xlsx`、`t02.docx`,按题序号,扩展名随题目要求。一题一个文件。

## 模块

| 文件 | 职责 | 行数 |
|---|---|---|
| `grade.py` | 加载题目、两种判分口径、缓存、汇总、写 CSV、CLI | 304 |
| `extract.py` | 四格式交付物转文本 | 105 |
| `llm.py` | judge 调用(OpenRouter),provider 钉定、重试、花费 | 96 |
| `test_grade.py` | 判分核心测试,合成数据,23 例 | 181 |

judge 取 `deepseek/deepseek-v4-pro-0813`,provider 钉 DeepSeek 官方端点且禁回落:阿里也是 OpenRouter 上该模型的托管方,而阿里是被测厂商,不能给自己判分。
