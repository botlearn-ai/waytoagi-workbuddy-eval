# BACKLOG

## 执行中 · Plan: gdpval-exam-w1   (→ .claude/plans/gdpval-exam-w1.md)

### Wave 1 — 无依赖 · 可并行
- [ ] T1  评分点内容哈希   file: src/gdpval_eval/hashing.py
        spec: rubric_item_hash(不清理前像,锁死尾随空格/弯引号)
        验收: 单元测试 ≤8 例全绿
- [ ] T2  加解密           file: src/gdpval_eval/crypto.py
        spec: generate_key / encrypt_bytes / decrypt_bytes
        验收: 往返一致,错 key 报 ManifestCryptoError
- [ ] T3  计分公式(盲测)  file: src/gdpval_eval/scoring.py
        spec: score_task(三状态语义、保护规则基=满分基数、Fraction 精确、D=0 政策、needs_review)
        验收: hidden tests 全绿(run-hidden-tests.sh)
- [ ] T4  判定通道粗分     file: src/gdpval_eval/channels.py
        spec: assign_channel → ChannelDecision(reason 为枚举)
        验收: 每通道 ≥2 代表句判对
- [ ] T5  数据集下载解析   file: src/gdpval_eval/dataset.py
        spec: download_parquet(revision 必填) / sha256_file / load_tasks(全字段 RubricItem)
        验收: 合成 fixture 三路(正常/缺列/坏行)
- [ ] T6  考卷校验         file: src/gdpval_eval/exam.py
        spec: load_exam_task_ids / validate_exam(四类硬失败,输出用题序号)
        验收: 统计正确 + 硬失败路径

### Wave 2 — 依赖 T1 T2 T4
- [ ] T7  manifest 构建/冻结/验证  file: src/gdpval_eval/manifest.py
        spec: canonical_json / build_manifest(content-meta 分离) / freeze(refreeze 闸+ledger) / verify
        验收: 指纹复算一致、篡改检出、哨兵泄露测试

### Wave 3 — 依赖 T5 T6 T7
- [ ] T8  CLI 接线  file: scripts/verify_dataset.py, freeze_manifest.py, verify_manifest.py
        验收: --help 可用,报告过哨兵测试

### Wave 4 — 依赖 T8
- [ ] T9  集成验证(架构师):真实数据 891 条对账 → 冻结 → verify → reachability(N/N 且 N≥1)
