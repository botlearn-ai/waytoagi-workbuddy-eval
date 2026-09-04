# BACKLOG

(无进行中 plan;W1、W2、W2b 已完成,见 .claude/plans/ 各 plan 的 Status)

## 正式判分前必须完成
- [ ] 891 条判定通道人工复核(方案 §4.3 的搭建期人工步骤;机器粗分与架构师初审笔记在 secrets/channel_review_notes.md,含 7 条待裁决的 deterministic 误分;复核若改通道 → 升版 exam_v1.1 重冻结,ledger 存证)
- [ ] W0 产品试跑(需人工操作三产品):素材上传实测 + 格式支持对照表;传不上去的题同职业换题

## 待办(repo 配好 remote 后转 GitHub issues)
- [ ] W3 跑分执行器:自动循环重判 pass 直到无 incomplete 或无进展(校验实测 DeepSeek 偶发空响应与推理预算耗尽,重判机制即为此设计)。**每轮必须重新调 `submission_check.resolve_deliverable` 取件,不得缓存 Path**——`VerdictRecord` 不含交付物指纹,取件时的 sha256 重算是「同一轮里换件导致两份文件的判定拼成一分」的唯一防线(W2b 终审)。声明了非交付 outcome 的题走 `NoDeliverableDeclared`,按 0 分计入并标注
- [ ] 视觉通道(42 条 VISION 首轮记无证据;需另选多模态 judge,属新决策)
- [ ] extract.py 依赖 openpyxl 私有 `_charts` 属性测图表存在;升级 openpyxl 可能静默失效(终审 LOW)
- [ ] assets.py 流式下载中的网络级异常未包装为 DatasetDownloadError,残留 .part(无害,重下覆盖;终审 LOW)
- [ ] verdicts.py 对 product/exam_version 无路径分隔符消毒(当前只有可信内部调用方;终审 LOW)
- [ ] load_tasks 对非钉死来源 parquet 无行数/体积上限(W1 终审 LOW)
- [ ] rubric_item_id 去重逻辑在 exam.py 与 manifest.py 双实现(W1 终审 MEDIUM,暂按双保险保留)
- [ ] submission.py 的「无匹配 parquet 行」与 submission_log.py 的畸形目录名/畸形 JSON 三条错误路径无测试(W2b 终审 LOW,实现已人工逐行核过)
