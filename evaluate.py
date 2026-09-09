"""办公软件测评：prepare 备题，run 判分并生成 CSV / Markdown 报告。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
from pathlib import Path, PurePosixPath
from time import sleep
from urllib.parse import urlparse

import httpx

import grade
import llm
from extract import MAX_CHARS, ExtractError, extract

PRODUCTS = ("豆包工作", "QwenWork", "WorkBuddy")
DOWNLOADS = Path("data/references")
RESULTS = Path("results")
MAX_REFERENCE_CHARS = MAX_CHARS
METRIC_FIELDS = ("product", "task", "status", "session", "elapsed_seconds", "tokens", "notes")
HANDOFF = (
    "请阅读当前工作目录中的 prompt.txt，按其中的完整任务要求执行。\n"
    "参考附件位于当前目录的 reference_files/，原题中的参考附件路径对应此目录。\n"
    "使用参考附件副本进行编辑，原始参考附件保留原样。\n"
    "最终交付文件保存到当前目录的 output/，原题中的交付目录对应此目录。\n"
    "请保留题目指定的文件格式和文件名，完成后列出最终交付文件。\n"
)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_path(root: Path, name: str) -> Path:
    parts = PurePosixPath(name).parts
    if not parts or name.startswith("/") or ".." in parts or "\\" in name or ":" in name:
        raise ValueError("材料路径应为目录内的相对路径")
    path = root.joinpath(*parts)
    for p in (root, path, *path.parents):
        if p.is_symlink():
            raise ValueError("测评目录应使用独立的普通文件和目录")
    return path


def reference_path(name: str) -> str:
    safe_path(Path("."), name)
    return f"reference_files/{PurePosixPath(name).name}"


def download(url: str, path: Path) -> None:
    if urlparse(url).scheme != "https" or not urlparse(url).hostname:
        raise ValueError("参考附件下载地址应为 HTTPS")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    for attempt in range(3):
        try:
            with httpx.stream("GET", url, follow_redirects=True, timeout=300) as response:
                response.raise_for_status()
                with tmp.open("wb") as f:
                    for chunk in response.iter_bytes():
                        f.write(chunk)
            tmp.replace(path)
            return
        except httpx.HTTPError as exc:
            reason = (
                f"HTTP {exc.response.status_code}"
                if isinstance(exc, httpx.HTTPStatusError)
                else type(exc).__name__
            )
            if attempt < 2:
                sleep(attempt + 1)
    raise ValueError(f"附件下载失败（{reason}），请检查网络并重新运行 prepare")


def manifest_tasks(tasks):
    if not tasks or [t.ordinal for t in tasks] != list(range(1, len(tasks) + 1)):
        raise ValueError("题序号应从 1 开始连续排列")
    if len({t.task_id for t in tasks}) != len(tasks):
        raise ValueError("题目应互不重复")
    return [{"ordinal": t.ordinal, "signature": grade.task_signature(t)} for t in tasks]


def read_manifest(root, tasks):
    data = json.loads(safe_path(root, "manifest.json").read_text())
    identities = [{k: t[k] for k in ("ordinal", "signature")} for t in data["tasks"]]
    if data["products"] != list(PRODUCTS) or identities != manifest_tasks(tasks):
        raise ValueError("题目清单与已备题目录不一致，请为这套题使用新的 --root")
    return data


def prepare(root: Path, tasks) -> None:
    identities = manifest_tasks(tasks)
    for task in tasks:
        names = [reference_path(name).casefold() for name in task.reference_names]
        if len(names) != len(set(names)):
            raise ValueError(f"第{task.ordinal}题: 附件存在同名文件，请先核对材料")
    root.mkdir(parents=True, exist_ok=True)
    if safe_path(root, "manifest.json").exists():
        read_manifest(root, tasks)
    records = []
    cleanup = []
    for task, identity in zip(tasks, identities, strict=True):
        inputs = {"prompt.txt": task.prompt.encode(), "START.txt": HANDOFF.encode()}
        for name, url in zip(task.reference_names, task.reference_urls, strict=True):
            relative = reference_path(name)
            if relative in inputs:
                raise ValueError(f"第{task.ordinal}题: 参考附件路径重复")
            cached = safe_path(DOWNLOADS / grade.fingerprint(url), Path(relative).name)
            if not cached.exists():
                download(url, cached)
            inputs[relative] = cached.read_bytes()
        hashes = {name: hashlib.sha256(content).hexdigest() for name, content in inputs.items()}
        for product in PRODUCTS:
            packet = safe_path(root, f"{product}/t_{task.ordinal:02d}")
            for name in task.reference_names:
                relative = reference_path(name)
                original = (
                    name if name.startswith("reference_files/") else f"reference_files/{name}"
                )
                old = safe_path(packet, original)
                if original != relative and old.exists():
                    if file_hash(old) != hashes[relative]:
                        raise ValueError(f"{product} 第{task.ordinal}题: 已有参考附件发生改动")
                    cleanup.append((old, packet / "reference_files"))
            for name, content in inputs.items():
                dest = safe_path(packet, name)
                if dest.exists() and file_hash(dest) != hashes[name]:
                    raise ValueError(
                        f"{product} 第{task.ordinal}题: 输入已改动，请使用新的题包目录"
                    )
                if not dest.exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(content)
            safe_path(packet, "reference_files").mkdir(exist_ok=True)
            safe_path(packet, "output").mkdir(exist_ok=True)
        records.append({**identity, "files": hashes})
        print(f"已备第{task.ordinal}题，三个产品各一份")
    manifest = {"products": list(PRODUCTS), "tasks": records}
    temporary = safe_path(root, "manifest.json.part")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    temporary.replace(safe_path(root, "manifest.json"))
    for old, reference in cleanup:
        old.unlink()
        parent = old.parent
        while parent != reference:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
    if not safe_path(root, "metrics.csv").exists():
        rows = [
            {
                **dict.fromkeys(METRIC_FIELDS, ""),
                "product": product,
                "task": task.ordinal,
                "status": "pending",
            }
            for product in PRODUCTS
            for task in tasks
        ]
        grade.write_csv(root / "metrics.csv", rows)
    read_metrics(root, tasks)
    print(f"备题完成：{len(tasks)} 题 × {len(PRODUCTS)} 产品；人工记录：{root / 'metrics.csv'}")


def read_metrics(root: Path, tasks) -> dict:
    expected = {(p, t.ordinal) for p in PRODUCTS for t in tasks}
    found = {}
    sessions = set()
    with safe_path(root, "metrics.csv").open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != list(METRIC_FIELDS):
            raise ValueError("metrics.csv 的列应保留备题时的列名与顺序")
        for n, row in enumerate(reader, start=2):
            try:
                row = {k: v.strip() for k, v in row.items()}
                key = row["product"], int(row["task"])
                if key not in expected or key in found:
                    raise ValueError
                if row["status"] not in {"pending", "running", "completed", "failed"}:
                    raise ValueError
                if row["status"] in {"completed", "failed"} and not row["session"]:
                    raise ValueError
                if row["session"]:
                    session = row["product"], row["session"]
                    if session in sessions:
                        raise ValueError
                    sessions.add(session)
                elapsed = row["elapsed_seconds"]
                if elapsed and (not math.isfinite(float(elapsed)) or float(elapsed) < 0):
                    raise ValueError
                tokens = row["tokens"]
                if tokens and (not tokens.isascii() or not tokens.isdecimal()):
                    raise ValueError
                if tokens:
                    int(tokens)
                found[key] = row
            except (ValueError, TypeError, AttributeError):
                raise ValueError(f"metrics.csv 第{n}行: 身份、状态、会话或数值无效") from None
    if set(found) != expected:
        raise ValueError("metrics.csv 应为每个产品的每道题保留恰好一行")
    return found


def check_inputs(packet: Path, record: dict) -> None:
    for name, digest in record["files"].items():
        path = safe_path(packet, name)
        if not path.is_file() or file_hash(path) != digest:
            raise ValueError("题面或参考附件与备题记录不一致，请核对本题材料")
    actual = {
        p.relative_to(packet).as_posix() for p in task_files(safe_path(packet, "reference_files"))
    }
    expected = {n for n in record["files"] if n.startswith("reference_files/")}
    if actual != expected:
        raise ValueError("参考附件目录的文件集合与备题记录不一致")


def task_files(directory: Path) -> list[Path]:
    files = []
    for path in directory.rglob("*"):
        if path.name in {".DS_Store", "Thumbs.db"} or path.name.startswith(("._", "~$")):
            continue
        if path.is_symlink():
            raise ValueError("测评目录应使用独立的普通文件和目录")
        if path.is_file():
            files.append(path)
    return sorted(files)


def bundle_text(files: list[Path], base: Path, max_chars: int = MAX_CHARS) -> str:
    chunks = []
    for path in files:
        safe_path(base, path.relative_to(base).as_posix())
        text = (
            path.read_text(encoding="utf-8-sig")
            if path.suffix.lower() == ".txt"
            else extract(path, max_chars=max_chars)
        )
        if not text.strip():
            raise ExtractError("文件未提取到文本，请人工核对或使用支持的可读文件")
        if len(text) >= max_chars:
            raise ExtractError("文件文本达到提取上限，请人工核对完整性")
        chunks.append(f"[文件 {path.relative_to(base).as_posix()}]\n{text}")
    result = "\n\n".join(chunks)
    if len(result) > max_chars:
        raise ExtractError("材料合计文本超过提取上限")
    return result


def score_task(task, packet, record, cache, model, modes, client):
    check_inputs(packet, record)
    output = safe_path(packet, "output")
    files = task_files(output)
    if not files:
        raise ValueError("output/ 中没有最终交付文件")
    if any(p.suffix.lower() not in {".xlsx", ".docx", ".pptx", ".pdf"} for p in files):
        raise ValueError("output/ 应只包含最终 XLSX、DOCX、PPTX、PDF 文件")
    doc = bundle_text(files, output)
    reference = bundle_text(
        [safe_path(packet, n) for n in record["files"] if n.startswith("reference_files/")],
        packet,
        MAX_REFERENCE_CHARS,
    )
    artifacts = {p.relative_to(output).as_posix(): file_hash(p) for p in files}
    values = {
        "rubric_score": "",
        "pairwise_score": "",
        "judge_cost_usd": 0.0,
        "output_sha256": grade.fingerprint(artifacts),
    }
    for mode in modes:
        gold = grade.gold_text(task) if mode == "pairwise" else ""
        identity = grade.cache_key(
            task, model, mode, record["files"], artifacts, doc, reference, gold
        )
        if mode == "rubric":
            earned = 0
            for item in task.rubric:
                key = f"{identity}:{item.item_id}"
                rec = cache.get(key)
                if rec is None:
                    met, cost = grade.judge_item(task, item, doc, model, client, reference)
                    rec = cache.put(key, met=met, cost=cost)
                earned += item.score if rec["met"] else 0
                values["judge_cost_usd"] += rec["cost"]
            values["rubric_score"] = round(float(grade.task_score_5(earned, task.total)), 6)
        else:
            rec = cache.get(identity)
            if rec is None:
                rng = random.Random(grade.task_signature(task))
                score, side, cost = grade.judge_pairwise(
                    task, doc, gold, model, client, rng, reference
                )
                rec = cache.put(identity, score=score, a_side=side, cost=cost)
            values["pairwise_score"] = rec["score"]
            values["judge_cost_usd"] += rec["cost"]
    return values


def report_dir(root: Path) -> Path:
    return RESULTS / f"{root.name}-{grade.fingerprint(str(root.resolve()))[:10]}"


def evaluate(root: Path, tasks, *, model: str, modes: tuple, limit=None) -> list[dict]:
    manifest = read_manifest(root, tasks)
    metrics = read_metrics(root, tasks)
    destination = report_dir(root)
    rows = []
    with httpx.Client() as client:
        for product in PRODUCTS:
            cache = grade.Cache(destination / f"{product}.cache.jsonl")
            for task, record in zip(tasks, manifest["tasks"], strict=True):
                row = {
                    **metrics[(product, task.ordinal)],
                    "task": task.ordinal,
                    "rubric_score": "",
                    "pairwise_score": "",
                    "judge_cost_usd": "",
                    "output_sha256": "",
                }
                if limit is not None and task.ordinal > limit:
                    row["status"] = "not_evaluated"
                elif row["status"] == "failed":
                    for mode in modes:
                        row[f"{mode}_score"] = 0.0
                    row["judge_cost_usd"] = 0.0
                elif row["status"] == "completed":
                    try:
                        print(f"{product} 第{task.ordinal}题: 判分中", flush=True)
                        packet = safe_path(root, f"{product}/t_{task.ordinal:02d}")
                        row.update(score_task(task, packet, record, cache, model, modes, client))
                        row["status"] = "graded"
                    except (
                        ValueError,
                        OSError,
                        ExtractError,
                        llm.JudgeError,
                        httpx.HTTPError,
                    ) as exc:
                        row["status"] = "error"
                        # 已知校验错误用固定文案；底层异常只带类型以保护题目身份。
                        reason = (
                            str(exc)
                            if type(exc) in {ValueError, ExtractError, llm.JudgeError}
                            else type(exc).__name__
                        )
                        row["notes"] = "；".join(filter(None, [row["notes"], reason]))
                rows.append(row)
                print(f"{product} 第{task.ordinal}题: {row['status']}")
    write_reports(destination, rows, len(tasks), modes, model)
    return rows


def write_reports(destination: Path, rows: list[dict], task_count: int, modes, model) -> None:
    leaderboard = []
    for product in PRODUCTS:
        entries = [r for r in rows if r["product"] == product]
        done = sum(r["status"] in {"graded", "failed"} for r in entries)
        complete = done == task_count and len(entries) == task_count
        result = dict(
            rank="",
            product=product,
            status="complete" if complete else "incomplete",
            scored_tasks=done,
            total_tasks=task_count,
            rubric_total="",
            rubric_max=5 * task_count,
            pairwise_win_rate="",
        )
        if complete:
            if "rubric" in modes:
                result["rubric_total"] = round(sum(float(r["rubric_score"]) for r in entries), 3)
            if "pairwise" in modes:
                result["pairwise_win_rate"] = round(
                    sum(float(r["pairwise_score"]) for r in entries) / task_count, 6
                )
        for metric, number in (("elapsed_seconds", float), ("tokens", int)):
            known = [number(r[metric]) for r in entries if r[metric] != ""]
            result[f"{metric}_recorded"] = len(known)
            result[f"{metric}_total"] = sum(known) if len(known) == task_count else ""
        leaderboard.append(result)
    primary = "rubric_total" if "rubric" in modes else "pairwise_win_rate"
    leaderboard.sort(key=lambda r: (r[primary] == "", -float(r[primary] or 0)))
    previous, rank = None, 0
    for position, row in enumerate(leaderboard, start=1):
        if row[primary] != "":
            if row[primary] != previous:
                rank, previous = position, row[primary]
            row["rank"] = rank
    grade.write_csv(destination / "scores.csv", rows)
    grade.write_csv(destination / "leaderboard.csv", leaderboard)
    lines = [
        "# 办公软件测评报告",
        "",
        f"Judge：`{model}`；口径：{', '.join(modes)}。",
        f"每产品 {task_count} 题；rubric 每题 5 分，总分 {5 * task_count}。",
        "pairwise 为相对专家答案的胜率（平局计半胜）；该实现使用文本 judge。",
        "排行榜按 rubric 总分排序；仅运行 pairwise 时按胜率排序；同分并列。",
        "完整评测的产品参与排名；未评完的总分留空。人工确认失败计零分。",
        "耗时单位为秒，token 为人工填写的产品消耗；缺失项留空，0 表示确认值为零。",
        "judge_cost_usd 为当前答案已有有效判定的费用，含缓存；属于判分费用。",
        "图片、布局、图表及公式可用性需人工复核，文本提取不能完整评价视觉质量。",
        "",
        "## 排行榜",
        "",
        "| 排名 | 产品 | 已评/题数 | rubric 总分 | 专家对比胜率 | 总耗时（秒） | 总 token |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in leaderboard:
        win = f"{r['pairwise_win_rate']:.1%}" if r["pairwise_win_rate"] != "" else "—"
        lines.append(
            f"| {r['rank'] or '—'} | {r['product']} | {r['scored_tasks']}/{task_count} | "
            f"{r['rubric_total'] if r['rubric_total'] != '' else '—'} | {win} | "
            f"{r['elapsed_seconds_total'] if r['elapsed_seconds_total'] != '' else '—'} | "
            f"{r['tokens_total'] if r['tokens_total'] != '' else '—'} |"
        )
    lines.extend(
        [
            "",
            "## 逐题结果",
            "",
            "| 产品 | 题序号 | 状态 | rubric /5 | pairwise | 耗时（秒） | token |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for r in rows:
        cells = [
            r[k]
            for k in (
                "product",
                "task",
                "status",
                "rubric_score",
                "pairwise_score",
                "elapsed_seconds",
                "tokens",
            )
        ]
        lines.append("| " + " | ".join(str(c) if c != "" else "—" for c in cells) + " |")
    lines.extend(
        ["", "逐题备注、会话和答案校验值见 scores.csv；人工指标填写覆盖数见 leaderboard.csv。"]
    )
    (destination / "report.md").write_text("\n".join(lines) + "\n")
    print(f"报告：{destination / 'report.md'}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "run"))
    parser.add_argument("--root", type=Path, default=Path("tasks"), help="三产品题包的根目录")
    parser.add_argument("--mode", choices=("both", "rubric", "pairwise"), default="both")
    parser.add_argument("--model", default=llm.DEFAULT_MODEL)
    parser.add_argument("--limit", type=int, help="只判前 N 题，其余在报告中标为未评")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit 应为正整数")
    try:
        tasks = grade.load_tasks()
        if args.command == "prepare":
            prepare(args.root, tasks)
        else:
            modes = ("rubric", "pairwise") if args.mode == "both" else (args.mode,)
            rows = evaluate(args.root, tasks, model=args.model, modes=modes, limit=args.limit)
            return int(any(r["status"] == "error" for r in rows))
    except (ValueError, OSError, KeyError) as exc:
        message = str(exc) if type(exc) is ValueError else type(exc).__name__
        print(f"测评未运行完成：{message}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
