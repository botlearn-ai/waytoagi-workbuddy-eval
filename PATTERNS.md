# PATTERNS.md

## 平铺脚本

四个文件在仓库根目录,不做 package、不建 `src/`。`import llm`、`from extract import extract` 直接生效。参照 x-bench-evals 的形态。

## 题序号是对外身份

`ordinal`(1..20)用于 stdout、CSV、报告、异常消息。`task_id` 只在 `load_tasks` 内部用于对上 parquet 行。这样输出物不泄露选了哪 20 题。

## judge 回复只看末行

judge 可以先写理由,最后一行必须是单个词(`MET`/`NOT_MET`,或 `A`/`B`/`TIE`)。解析只取 `splitlines()[-1]`,正则加词边界。解析不出来时取保守值:rubric 口径记不成立,pairwise 记平手。

## 两两对比要随机 A/B 位

LLM 对先出现的选项有位置偏好。`judge_pairwise` 按 `rng.random() < 0.5` 决定产品交付物放 A 还是 B,再把 judge 的 `A`/`B` 换算回「产品是否更好」。换算方向是最容易写反的地方,测试逐组合覆盖。落缓存时记下 `a_side` 供复查。

## 判定缓存

`results/<product>.<mode>.cache.jsonl`,一行一条判定,追加写。key 是 `"<题序号>:<item_id>"`(pairwise 口径只用题序号)。跑之前整个读进内存,命中即跳过。作用是重跑不重复付费——891 条 × 3 产品的调用量下,中断重跑的代价是真金白银。

## 异常消息不带文件内容

`ExtractError` 只写格式名、页数、上限这类数字。交付物内容与 judge 原文只进 gitignored 的 `results/`。

## 上限只保留会真实触发的那几条

`MAX_CHARS`(400k,judge 上下文)、`_MAX_CELLS`、`_MAX_PDF_PAGES`。交付物是自己从产品导出的文件,不是不可信上传,zip 炸弹那一层检查已刻意去掉。
