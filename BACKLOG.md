# BACKLOG

## 执行中 · Plan: gdpval-exam-w1   (→ .claude/plans/gdpval-exam-w1.md)

### Wave 1 — 无依赖 · 可并行
- [x] T1  评分点内容哈希   file: src/gdpval_eval/hashing.py — 7 例绿,已验收
- [x] T2  加解密           file: src/gdpval_eval/crypto.py — 4 例绿,已验收
- [x] T3  计分公式(盲测)  file: src/gdpval_eval/scoring.py — visible 3/3 + hidden 22/22,已验收
- [x] T4  判定通道粗分     file: src/gdpval_eval/channels.py — 6 例绿,已验收
- [x] T5  数据集下载解析   file: src/gdpval_eval/dataset.py — 7 例绿,已验收
- [x] T6  考卷校验         file: src/gdpval_eval/exam.py — 8 例绿,已验收

### Wave 2 — 依赖 T1 T2 T4
- [x] T7  manifest 构建/冻结/验证  file: src/gdpval_eval/manifest.py — 9 例绿,已验收

### Wave 3 — 依赖 T5 T6 T7
- [x] T8  CLI 接线  file: src/gdpval_eval/cli.py + scripts/ 三薄壳 — 4 例绿,已验收

### Wave 4 — 依赖 T8
- [x] T9  集成验证:真实数据 891 条对账 PASS → exam_v1 冻结(6621e5…ccee5)→ verify ok → 全量 70 绿 + ruff 净;reachability 与 impl-reviewer 终审在 commit 后收尾

## 待办(repo 配好 remote 后转 GitHub issues)
- [ ] load_tasks 对非钉死来源的 parquet 无行数/体积上限(zip-bomb/OOM 面);当前输入锁定官方 HF revision,风险低。若未来接受任意 parquet,补流式读取与体积上限(impl-reviewer 终审 LOW 项)
- [ ] rubric_item_id 去重逻辑在 exam.py 与 manifest.py 各实现一份(异常类型不同);两校验点在 freeze 链路上串行执行构成双保险,暂不合并(impl-reviewer 终审 MEDIUM 项,交用户裁决)
