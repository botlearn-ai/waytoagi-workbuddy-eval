# AGENTS.md

GDPval 办公产品测评：独立题包、人工记录、两种判分口径与排行榜。Python 脚本平铺在仓库根目录。

## 文档地图

| 文档 | 内容 |
|---|---|
| [PROJECT.md](PROJECT.md) | 目的、两种判分口径、功能状态、数据模型、模块 |
| [PATTERNS.md](PATTERNS.md) | 设计约定 |
| [TECHSTACK.md](TECHSTACK.md) | 依赖、目录、环境变量 |
| [DEVFLOW.md](DEVFLOW.md) | 备题、会话隔离、人工记录、判分报告与开发命令 |

当前待办在 [BACKLOG.md](BACKLOG.md)。

## 本 repo 铁律

1. **规模纪律**:沿用平铺脚本，备题与汇总使用 evaluate.py，判分复用 grade.py。两种判分口径为专家交付物两两对比与 rubric 逐条加权。新增机制先核对用户授权范围。
2. **保密只挡一件事**:题面、素材、专家交付物、rubric 全文都是 OpenAI 公开开源的,不是机密。唯一不入库的是**选了哪 20 题**——防产品方针对性优化。题号清单在 gitignored 的 `data/task_ids.txt`,报告与 stdout 用题序号(1..20)指代。

   判据:写任何逐题派生量或引用任何 criterion 原文之前,先在公开的 220 题全集上算候选集大小,为 1 即等同写出题号。踩过的两次——某题某通道占正分的精确百分比在全集里唯一命中;一条 criterion 原文若只属一题,引用它就等于点名该题。通用模板句(在多题重复出现的那类)不构成泄露,但落笔前仍要算一次。**举例说明这条判据时,不要把踩过的那个具体数值或原句抄进来。**
3. **判分改动必测**:`grade.py` 与 `evaluate.py` 的判分逻辑改动先写测试。测试用合成数据,不依赖真实 parquet。
4. Python 环境用 uv(见 DEVFLOW.md)。
