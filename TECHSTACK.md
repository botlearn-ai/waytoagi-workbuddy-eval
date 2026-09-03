# TECHSTACK.md

## 语言与运行时

- Python ≥ 3.11,环境与依赖用 uv 管理(`uv sync`),打包后端 hatchling,src layout。

## 依赖

| 包 | 用途 |
|---|---|
| pyarrow | 读 GDPval parquet |
| cryptography | manifest Fernet 加解密 |
| httpx[socks] | 下载与 OpenRouter judge 调用(socks:本机代理环境) |
| openpyxl + defusedxml | xlsx 解析(defusedxml 必装,extract 导入时断言生效,防 XML 实体炸弹) |
| python-docx / python-pptx / pypdf | docx / pptx / pdf 解析与改坏工具 |
| pytest(dev) | 测试 |
| ruff(dev) | lint |

## 外部服务

- HuggingFace:数据集 `openai/gdpval`,下载端点可用 `HF_ENDPOINT` 换镜像(默认 https://huggingface.co)。
- LLM judge(W2 接入):OpenRouter 上的 `deepseek/deepseek-v4-pro-0813`,provider 钉 DeepSeek 官方端点、禁 fallback、temperature 0——完整配置在 `configs/judge.exam_v1.json`,已锁进 manifest meta。调用凭据 `OPENROUTER_API_KEY`。

## 目录结构

```
src/gdpval_eval/    业务模块(见 PROJECT.md 模块地图)
scripts/            CLI:verify_dataset.py、freeze_manifest.py
tests/              单元测试;tests/visible/、tests/hidden/ 为判分盲测
manifests/          加密 manifest、content 指纹、LEDGER.jsonl(tracked)
secrets/            20 题清单、逐题统计预期、manifest 明文副本与 key(gitignored)
data/               下载的 parquet、素材、gold(gitignored)
.claude/plans/      进行中的 plan(tracked)
```

## 环境变量

| 变量 | 用途 |
|---|---|
| `HF_ENDPOINT` | 覆盖 HuggingFace 下载端点(镜像) |
| `GDPVAL_MANIFEST_KEY` | manifest 加密 key(Fernet),不入库 |
| `OPENROUTER_API_KEY` | judge 调用凭据(W2 起用),不入库 |

无数据库、无端口、无部署目标。
