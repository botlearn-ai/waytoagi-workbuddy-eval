# BACKLOG

## 执行中 · Plan: gdpval-grading-w2   (→ .claude/plans/gdpval-grading-w2.md)

### W0 — 架构师,先行 commit
- [x] .gitignore 加 runs/;pyproject 加 httpx[socks]/openpyxl/python-docx/python-pptx/pypdf/defusedxml

### Wave 1 — 并行
- [x] U1  交付物解析 — 8 例绿(实体炸弹拦截实测),已验收
- [x] U3  LLM 裁决(盲测)— visible 4/4 + hidden 31/31,已验收
- [x] U4  判定存储(盲测)— visible 3/3 + hidden 25/25,已验收
- [x] U6  任务文件下载 — 4 例绿,已验收

### Wave 2 — 并行,依赖 U1
- [x] U2  确定性检查器(盲测)— visible 3/3 + hidden 34/34;架构师追加 text_excludes/sheet_exists_ci(5 例),已验收
- [x] U7  改坏工具 — 8 例绿(量具自检:token 多重集合判定),已验收

### Wave 3 — 依赖 U1-U4
- [ ] U5  判分编排(盲测) file: src/gdpval_eval/grade.py(先查已判、并发 6、熔断、空文本守卫)

### Wave 4 — 依赖 U5 U6 U7,架构师
- [ ] U8  区分度检验 scripts/calibration.py:4 题 × 4 份,六道放行闸,真实 API

## 待办(repo 配好 remote 后转 GitHub issues)
- [ ] 891 条判定通道人工复核(阻塞正式判分;复核若改通道 → 升版重冻,ledger 存证)
- [ ] W2b 人工提交工具包:inbox/outbox + 校验脚本 + 协议手册(prose 流程)
- [ ] 视觉通道(42 条 VISION 首轮记无证据;需另选多模态 judge,属新决策)
- [ ] load_tasks 对非钉死来源 parquet 无行数/体积上限(前轮 impl-reviewer LOW 项)
- [ ] rubric_item_id 去重逻辑在 exam.py 与 manifest.py 双实现(前轮 MEDIUM 项,暂按双保险保留)
