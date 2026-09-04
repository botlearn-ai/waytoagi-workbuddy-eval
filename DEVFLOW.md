# DEVFLOW.md

## 本地开发

```bash
uv sync                      # 建 .venv 并装依赖(含 dev)
uv run pytest                # 全量测试(integration 标记默认排除)
uv run pytest -m integration # 真实下载数据的集成测试(需网络)
uv run ruff check .          # lint
```

## 判分盲测

```bash
bash ~/.claude/scripts/run-hidden-tests.sh <repo-root> <unit>   # 只回 PASSED: X/Y
```

## W1 数据管线

```bash
# 1. 下载 parquet(revision 钉 commit sha)并按 secrets/ 里的清单与预期校验考卷
uv run python scripts/verify_dataset.py \
  --task-ids secrets/exam_v1_task_ids.txt \
  --expect secrets/exam_v1_expected.json \
  --revision <上游 commit sha>

# 2. 冻结加密 manifest(key 存 secrets/manifest.key,不入库;输出已存在时须 --refreeze)
export GDPVAL_MANIFEST_KEY=$(cat secrets/manifest.key)
uv run python scripts/freeze_manifest.py \
  --task-ids secrets/exam_v1_task_ids.txt \
  --out manifests/ --revision <上游 commit sha>

# 3. 第三方校验冻结产物
uv run python scripts/verify_manifest.py \
  --enc manifests/exam_v1.manifest.enc \
  --fingerprint manifests/exam_v1.fingerprint.sha256
```

## W2b 人工提交(60 次:20 题 × 3 产品)

操作规程见 [docs/SUBMISSION_PROTOCOL.md](docs/SUBMISSION_PROTOCOL.md)。产物落在 gitignored 的 `submissions/`。

```bash
# 1. 备料:每题落地题面、素材、spec.json。幂等,可反复跑
uv run python scripts/prepare_inbox.py --product <产品代号>

# 2. 每题三步(机器盖时间戳与追问计数,独占创建防覆盖)
uv run python scripts/log_submission.py --product <产品代号> --ordinal <题序号> start
uv run python scripts/log_submission.py --product <产品代号> --ordinal <题序号> followup
uv run python scripts/log_submission.py --product <产品代号> --ordinal <题序号> finish \
  --outcome delivered --note "" --product-build "<版本号>" \
  --deliverable-filename "<主交付物文件名>"

# 3. 收尾校验 + 跨产品去重。退出码 0 可进判分 / 1 有拦停项 / 2 前置错误
uv run python scripts/check_outbox.py --product <产品代号>
```

判分器按题序号取件的唯一入口是 `submission_check.resolve_deliverable`,它重算 sha256
与校验时记录的比对,不符即拒绝——换交付物要 `--attempt` 加 1,不要在同一轮里覆盖。

## CI

`.github/workflows/ci.yml`,push 与 PR 触发,两个 job:
- **lint**:`uv run ruff check .`
- **test**:`uv run pytest`(裸命令全量发现,tests/、tests/visible/、tests/hidden/ 全部可达;integration 标记在 pyproject 里默认排除)

新增测试目录后跑 `~/.claude/scripts/check-ci-reachability.sh <repo>` 验证可达。

## 分支与提交

- 直接在 main 上做本地 commit,粒度为 plan 级;push 仅在用户明确要求时执行。
- manifest 与指纹文件一起 commit;改考卷 = 升 exam 版本重冻结,不改旧文件。
