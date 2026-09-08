# TECHSTACK.md

Python ≥ 3.11,uv 管环境。`[tool.uv] package = false` —— 这是脚本仓库,不是可安装包。

## 依赖

| 包 | 用途 |
|---|---|
| pyarrow | 读 GDPval parquet |
| httpx[socks] | judge 调用、下载专家交付物 |
| openpyxl | xlsx |
| python-docx | docx |
| python-pptx | pptx |
| pypdf | pdf |
| defusedxml | openpyxl 自动启用,挡 XML 实体展开 |

dev:pytest、ruff。

## 目录

```
grade.py  extract.py  llm.py  test_grade.py
data/          gitignored  gdpval.parquet、task_ids.txt、gold/(专家交付物)
submissions/   gitignored  产品交付物,每产品一个子目录,内含 t01.xlsx …
results/       gitignored  判分 CSV 与判定缓存
```

## 环境变量

`OPENROUTER_API_KEY` —— judge 调用凭证。未设时回落读 `~/.config/openrouter.key`。

judge 默认 `deepseek/deepseek-chat-v3-0324`,用 `--model` 换。
