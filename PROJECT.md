# 项目与当前功能

对豆包工作、千问办公（QwenWork）、WorkBuddy 做横向测评。题库使用 OpenAI 公开 GDPval 数据集的 220 题，当前本地清单选择 20 题。人在各产品界面完成独立任务，Python 脚本备题、校验输入、读取交付物、调用 judge 并汇总报告。

## 测评流程

`evaluate.py prepare` 为三个产品分别创建 `tasks/<产品>/t_NN/`，包含 START.txt、原始题面、参考附件与 output 目录。参考附件按原始文件名直接放在 reference_files 第一层。输入摘要与题目映射写入根目录 manifest.json；人工耗时、token、会话和完成状态填写到 metrics.csv。

`evaluate.py run` 校验映射与材料，从各题 output 读取最终文件，调用判分函数，生成 scores.csv、leaderboard.csv 和 report.md。每道题可交付多个办公文件。

| 口径 | 判定方法 | 汇总 |
|---|---|---|
| rubric | 原题、参考附件文本、答案对照逐条 rubric | 每题 5 分，20 题共 100 分 |
| pairwise | 原题、参考附件文本、产品答案与专家答案对比 | 0/0.5/1，平均为相对专家答案的胜率 |

pairwise 借鉴 GDPval 的两两比较口径，当前使用自选文本 judge。两种口径分别保留；完整评测产品参与排名，默认按 rubric 总分，同分并列。

## 功能状态

- 三产品独立题包、参考附件下载缓存、幂等备题、人工 CSV 模板已实现。
- 输入一致性、题目身份、重复会话、人工数值校验已实现。
- 四格式答案文本解析、两种评分、内容绑定缓存、逐题与排行榜报告已实现。
- 判分异常和未完成题留空；人工确认产品运行失败计零分。
- 合成任务覆盖备题、真实办公文件解析、模拟 judge、缓存失效、汇总和边界。
- 产品实际做题与真实 judge 试跑由操作人按 DEVFLOW.md 执行。

## 数据模型

- `Task`：ordinal、内部 task_id、occupation、原始 prompt、rubric、参考附件与专家交付物名称及 URL。
- `Item`：item_id、criterion、整数 score，可为负。满分基数为全部正分之和；题分为 `5 × max(0, 得分) / 满分基数`，满分基数为零时记零。
- `manifest.json`：产品列表、题序号、题目签名、输入相对路径及 SHA-256。
- `metrics.csv`：product、task、status、session、elapsed_seconds、tokens、notes。空指标为未知，零为确认值。
- `scores.csv`：按产品与题序号关联输入、人工指标、评分状态、两种分数、答案摘要与 judge 费用。

## 模块

| 文件 | 职责 |
|---|---|
| evaluate.py | 备题、材料下载、人工记录校验、按目录评分、排行榜与报告、CLI |
| grade.py | 题目加载、判分公式、judge 提示词、缓存、专家答案、平铺交付物 CLI |
| extract.py | XLSX、DOCX、PPTX、PDF 文本提取 |
| llm.py | OpenRouter 调用、provider 约束、重试、费用 |
| test_grade.py、test_evaluate.py、test_extract.py | 合成数据测试 |

默认 judge 为 `deepseek/deepseek-v4-pro-0813`，provider 固定 DeepSeek 并关闭回落。当前判分为文本判分，视觉质量与文件可用性需人工验收。
