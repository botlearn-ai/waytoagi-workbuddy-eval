# 技术栈

Python ≥ 3.11，uv 管理环境；`[tool.uv] package = false`，根目录直接运行脚本。

| 包 | 用途 |
|---|---|
| pyarrow | 读 GDPval parquet |
| httpx[socks] | judge 请求、参考附件和专家答案下载 |
| openpyxl | XLSX 读取 |
| python-docx | 合成 DOCX 测试数据 |
| python-pptx | PPTX 读取 |
| pypdf | PDF 文本提取 |
| defusedxml | DOCX 正文 XML 提取、openpyxl XML 解析保护 |
| pytest、ruff | 测试与检查（dev） |

CSV、JSON、SHA-256 与 Markdown 报告由 Python 标准库处理。

## 目录

```text
evaluate.py  grade.py  extract.py  llm.py
test_evaluate.py  test_grade.py  test_extract.py
data/          gitignored：gdpval.parquet、task_ids.txt、references/、gold/
tasks/         gitignored：三产品独立题包、manifest.json、metrics.csv
submissions/   gitignored：平铺交付物入口
results/       gitignored：按题根目录隔离的报告和判定缓存
.github/workflows/ci.yml
```

`--root` 可指定题包路径；结果目录名包含该路径的摘要，以区分同名根目录。参考附件下载缓存按 URL 摘要隔离，专家答案按题目签名隔离。

## 外部服务与凭证

- Hugging Face：GDPval 数据集与附件下载。
- OpenRouter Chat Completions：judge 调用。
- `OPENROUTER_API_KEY`：凭证；未设置时读取用户配置目录中的 `openrouter.key`。
- 默认模型：`deepseek/deepseek-v4-pro-0813`，provider 为 DeepSeek，禁用回落；`--model` 支持选择该 provider 可服务的模型。

本地脚本运行，无服务端口、数据库或部署服务。
