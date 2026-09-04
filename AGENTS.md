# AGENTS.md

AI 办公产品横向测评仓库(题源 GDPval)。本仓库将来公开发布,写入任何 tracked 文件前先过下面的保密铁律。

## 文档地图

| 文档 | 内容 |
|---|---|
| [PROJECT.md](PROJECT.md) | 项目目的、功能状态、核心数据模型、模块地图 |
| [PATTERNS.md](PATTERNS.md) | 设计范式、错误处理约定、命名、测试组织 |
| [TECHSTACK.md](TECHSTACK.md) | 技术栈、依赖、目录结构、环境变量 |
| [DEVFLOW.md](DEVFLOW.md) | 开发、测试、冻结 manifest、人工提交的命令与 CI |
| [docs/SUBMISSION_PROTOCOL.md](docs/SUBMISSION_PROTOCOL.md) | 人工提交操作规程:备料、限时与追问额度、异常处置、收尾校验 |

进行中的 plan 在 `.claude/plans/`,当前待办在 `BACKLOG.md`。

## 本 repo 铁律

1. **保密**:题面(prompt)、评分点文本(criterion)、gold 交付物内容、20 题 task_id 清单及其逐题统计,一律不得出现在 tracked 文件里——包括代码、测试 fixture、注释、文档、日志样例。**评分点哈希与 rubric_item_id 同级保密**:数据集公开,sha256(criterion+分值) 可被彩虹表逐条反查,哈希即明文。它们只存在于 gitignored 的 `secrets/`、`data/` 和加密后的 manifest 中;git-tracked 的机密派生物仅限加密 manifest、content 指纹、ledger。报告、异常消息、CLI 输出用题序号/题内序号指代。测试一律使用虚构的合成数据。
2. **评分点文本只在内存流转**:不写入日志、异常消息、CLI 输出。
3. **判分逻辑改动走盲测分离**:`tests/hidden/` 对实现者不可见,用 `~/.claude/scripts/run-hidden-tests.sh <repo> <unit>` 验证。
4. **manifest 一经冻结不可改**:改考卷 = 升版本号重新冻结,指纹文件一并更新。
5. Python 环境用 uv(见 DEVFLOW.md)。
