# PROJECT.md

## 目的

对豆包工作(字节)、千问办公 QwenWork(阿里)、WorkBuddy(腾讯云)三个 AI 办公产品做横向测评。题目取自 OpenAI 公开数据集 GDPval(huggingface.co/datasets/openai/gdpval)中的 20 道真实工作任务,每题 5 分、满分 100 分,判分由程序完成:确定性检查 + 文本/视觉 LLM 裁决。完整方案见飞书文档 `VFKNdKCbOoiMNlxwdNtcGTUonod`(内部)。

产品没有公开 API,首轮从产品界面人工提交、程序判分;本仓库承载全部可自动化的部分,目标是外部人 clone 后凭交付物文件与自己的 judge API key 复现判分。

## 功能状态

| 阶段 | 内容 | 状态 |
|---|---|---|
| W1 数据管线 | parquet 下载(revision 钉 `11e7900cdcac61bc4daf59e65feb238acda98fbf`)、考卷校验、评分点哈希、manifest 冻结加密、三个 CLI | 完成;exam_v1 已冻结(status=frozen,judge 见 configs/judge.exam_v1.json),content 指纹见 manifests/ |
| 判分核心 | 计分公式(三状态 + 保护规则,盲测 22 例)、判定通道粗分 | 完成;通道分派待 W1 人工复核 891 条 |
| W0 产品试跑 / W2 LLM 裁决通道与通用检查器 / W3 提交与报告 / W4 自动提交 | — | 未开始 |

## 核心数据模型

- **Task**:GDPval 一道题。`task_id, occupation, prompt, 素材/gold 文件清单, rubric: list[RubricItem]`。
- **RubricItem**:一条评分点。`rubric_item_id(上游 id,全局唯一,判定的身份键), criterion(文本,不入库), score(int,可为负,不为 0), tags(上游未文档化,原样存档)`。内容哈希 `sha256(criterion + \x1f + 分值)` 只作漂移探测,不作身份——上游同一题内存在完全相同的 (criterion, score) 对。
- **判定三状态**:`CONDITION_MET(计分条件成立;负分条目即违规成立、扣分生效) / CONDITION_NOT_MET / NO_EVIDENCE`。NO_EVIDENCE 仅限 harness 侧拿不到证据(渲染失败、超上下文);产品没做的内容记 CONDITION_NOT_MET。「判分未完成」不是状态,走重判,不进计分函数。
- **计分**(全程 int/Fraction 精确运算):满分基数 = Σ正分;判定分母 = 满分基数 − 无证据正分;earned = Σ(CONDITION_MET 正分,单条计入 ≤ 满分基数 15%) + 扣分(合计 ≥ −满分基数 30%);题分 = 5 × max(0, earned) / 判定分母。两条保护规则同以满分基数为基、互相独立(以判定分母为基会导致分数对判定结果非单调)。分母为 0 → 题按 0 分计入总分并标注;无证据正分占满分基数 > 20% → needs_review,不进自动汇总。
- **判定通道**:`DETERMINISTIC / TEXT / VISION`,每条评分点走一条,冻结进 manifest;通道理由为枚举(不携带 criterion 派生文本)。
- **manifest**:考卷的冻结载体,`content`(exam 版本、上游 revision、parquet sha256、披露政策、每题 {task_id, 职业, 格式列表, items:[{rubric_item_id, content_hash, score, channel, tags}]})与 `meta`(frozen_at、judge、状态 `frozen`/`draft-pending-A2`)分离;指纹 = content 规范化 JSON 的 sha256,meta 变更(如 A2 拍板)不动指纹。Fernet 加密入库,指纹与追加式 LEDGER.jsonl tracked,明文副本与 key 在 gitignored `secrets/`。

## 模块地图(src/gdpval_eval/)

| 模块 | 职责 |
|---|---|
| models.py | Task / RubricItem / 枚举等共享 dataclass |
| dataset.py | 下载 parquet、解析为 Task 列表 |
| hashing.py | 评分点内容哈希 |
| exam.py | 20 题清单载入与选题规则校验 |
| channels.py | 判定通道规则粗分 |
| scoring.py | 计分公式(盲测保护) |
| crypto.py | manifest 加解密 |
| manifest.py | canonical_json、manifest 构建/冻结/验证、ledger |
| extract.py | 四格式交付物解析(文本+结构事实,防炸弹上限) |
| checkers.py | 参数化确定性检查器(六个;参数存 secrets/) |
| judge.py | OpenRouter 文本裁决客户端(nonce 数据区、白名单解析、served 校验) |
| verdicts.py | 判定存储(写一次、flock 单写者、runs/ gitignored) |
| grade.py | 单交付物判分编排(断点续跑、有界并发、熔断) |
| degrade.py | 校验用改坏量具(control/truncated/shuffled,变化断言) |
| assets.py | gold/素材文件下载 |
| calibration_gates.py | 区分度检验放行闸(纯函数) |
| cli.py | 三个 CLI 的参数解析与组装 |
| scripts/ | verify_dataset / freeze_manifest / verify_manifest / calibration |
