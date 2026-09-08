# DEVFLOW.md

## 装环境

```bash
uv sync
export OPENROUTER_API_KEY=...
```

## 备数据(一次性)

```bash
# GDPval parquet(220 题,含题面、rubric、专家交付物 URL)。revision 钉死以防数据集变动
mkdir -p data
curl -L -o data/gdpval.parquet \
  "https://huggingface.co/datasets/openai/gdpval/resolve/11e7900cdcac61bc4daf59e65feb238acda98fbf/data/train-00000-of-00001.parquet"
```

`data/task_ids.txt` —— 选中的 20 个 task_id,一行一个,顺序即题序号 1..20。这个文件不入库。

## 放产品交付物

每个产品一个目录,文件按题序号命名,扩展名随题目要求:

```
submissions/doubao/t01.xlsx
submissions/doubao/t02.docx
...
```

## 跑判分

```bash
# 按数据集自带评分点逐条判,输出百分制
uv run python grade.py rubric --dir submissions/doubao --product doubao

# 与 GDPval 专家交付物两两对比,输出胜率(官方口径)
uv run python grade.py pairwise --dir submissions/doubao --product doubao

# 先试两题
uv run python grade.py rubric --dir submissions/doubao --product doubao --limit 2
```

结果写 `results/<product>.<mode>.csv`;判定落 `results/<product>.<mode>.cache.jsonl`,重跑自动跳过已判条目,不重复付费。

`pairwise` 首次运行会下载该题的专家交付物到 `data/gold/tNN/`。

## 测试与检查

```bash
uv run pytest -q
uv run ruff check .
```

CI 在 push 与 PR 上跑这两条。

## 分支

在 main 上开发。commit 打本地,push 由用户明确要求。
