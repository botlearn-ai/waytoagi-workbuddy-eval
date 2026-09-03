"""区分度检验(spec U8):gold + 改坏交付物真实判分,六道放行闸。

输出只用题序号/题内序号;判定存档写进 gitignored 的 runs/。
用法见 DEVFLOW.md;退出码 0=放行,1=任一闸不过,2=环境错误,3=熔断。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from fractions import Fraction
from pathlib import Path

import pyarrow.parquet as pq

from gdpval_eval.assets import download_task_files
from gdpval_eval.degrade import make_degraded
from gdpval_eval.grade import CallBudget, GradeAbortError, grade_deliverable
from gdpval_eval.judge import judge_item, load_judge_config
from gdpval_eval.models import ItemState, Task
from gdpval_eval.verdicts import VerdictStore

GOLD = "calib-gold"
VARIANT_PRODUCTS = {
    "roundtrip_control": "calib-control",
    "truncated": "calib-truncated",
    "shuffled_numbers": "calib-shuffled",
}


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="calibration", description=__doc__)
    p.add_argument("--manifest-plain", type=Path, default=Path("secrets/exam_v1.manifest.json"))
    p.add_argument("--parquet", type=Path, default=Path("data/gdpval.parquet"))
    p.add_argument("--task-ordinals", type=str, default="1,4,5,8")
    p.add_argument("--expected-sets", type=Path,
                   default=Path("secrets/calibration_expected_sets.exam_v1.json"))
    p.add_argument("--checker-params", type=Path,
                   default=Path("secrets/checker_params.exam_v1.json"))
    p.add_argument("--runs-root", type=Path, default=Path("runs"))
    p.add_argument("--gold-dir", type=Path, default=Path("data/gold"))
    p.add_argument("--max-judge-calls", type=int, default=800)
    p.add_argument("--key-file", type=Path, default=Path("secrets/openrouter.key"))
    return p


def _api_key(args) -> str | None:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key and args.key_file.exists():
        key = args.key_file.read_text(encoding="utf-8").strip()
    return key or None


def _load_task_row(rows: dict, task_id: str) -> Task:
    r = rows[task_id]
    return Task(
        task_id=r["task_id"], sector=r["sector"], occupation=r["occupation"],
        prompt=r["prompt"],
        reference_files=tuple(r["reference_files"] or ()),
        reference_file_urls=tuple(r["reference_file_urls"] or ()),
        deliverable_files=tuple(r["deliverable_files"] or ()),
        deliverable_file_urls=tuple(r["deliverable_file_urls"] or ()),
        rubric=(),
    )


def _smoke(cfg, api_key: str) -> bool:
    outcome = judge_item(cfg, api_key, "The material mentions a fox.",
                         "Synthetic smoke check.", "The quick brown fox.")
    ok = outcome.state in (ItemState.CONDITION_MET, ItemState.CONDITION_NOT_MET)
    print(f"smoke: state={'ok' if ok else outcome.incomplete_reason} "
          f"provider={outcome.served_provider} model={outcome.served_model}")
    return ok


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    api_key = _api_key(args)
    if api_key is None:
        print("error: no OPENROUTER_API_KEY and no key file", file=sys.stderr)
        return 2
    manifest = json.loads(args.manifest_plain.read_text(encoding="utf-8"))
    if manifest["meta"]["status"] != "frozen":
        print("error: manifest is not frozen", file=sys.stderr)
        return 2
    exam_version = manifest["content"]["exam_version"]
    cfg = load_judge_config(manifest["meta"])
    checker_params = json.loads(args.checker_params.read_text(encoding="utf-8"))
    expected_sets = json.loads(args.expected_sets.read_text(encoding="utf-8"))
    ordinals = [int(x) for x in args.task_ordinals.split(",") if x.strip()]

    rows = {r["task_id"]: r for r in pq.read_table(args.parquet).to_pylist()}

    if not _smoke(cfg, api_key):
        print("smoke call failed — not starting the big loop", file=sys.stderr)
        return 1

    budget = CallBudget(args.max_judge_calls)
    store = VerdictStore(args.runs_root / "calibration")
    gate_failures: list[str] = []
    total_cost = 0.0
    all_counts = {"items": 0, "harness_failures": 0}
    served_ok = True

    for ordi in ordinals:
        mtask = manifest["content"]["tasks"][ordi - 1]
        row = rows[mtask["task_id"]]
        rub = json.loads(row["rubric_json"])
        criterion_by_id = {x["rubric_item_id"]: x["criterion"] for x in rub}
        task = _load_task_row(rows, mtask["task_id"])

        gold_paths = download_task_files(task, args.gold_dir / f"t{ordi}", kind="deliverable")
        gold = gold_paths[0]
        variants = make_degraded(gold, args.runs_root / "calibration" / f"degraded_t{ordi}")

        results: dict[str, object] = {}
        for product, path in [(GOLD, gold)] + [
            (VARIANT_PRODUCTS[v], p) for v, p in variants.items()
        ]:
            try:
                res = grade_deliverable(
                    manifest, ordi, criterion_by_id, row["prompt"], Path(path),
                    product=product, attempt=1, api_key=api_key,
                    checker_params=checker_params, store=store, call_budget=budget,
                )
            except GradeAbortError:
                print(f"题{ordi} {product}: 连续判定失败,熔断", file=sys.stderr)
                return 3
            results[product] = res
            total_cost += res.cost
            n_items = len(mtask["items"])
            all_counts["items"] += n_items
            all_counts["harness_failures"] += int(res.harness_failure_ratio * n_items)
            score = "None" if res.task_score is None else f"{float(res.task_score.score_5):.2f}"
            print(f"题{ordi} {product}: score={score} resolved={res.resolved} "
                  f"incomplete={res.incomplete} no_evidence={res.no_evidence} "
                  f"cost=${res.cost:.2f}")
            if res.harness_failure_ratio > Fraction(5, 100):
                gate_failures.append(f"题{ordi} {product}: harness 失败率 >5%")

        def _score(product: str, _results=results) -> Fraction | None:
            ts = _results[product].task_score
            return None if ts is None else ts.score_5

        g, c = _score(GOLD), _score("calib-control")
        t, s = _score("calib-truncated"), _score("calib-shuffled")
        if any(v is None for v in (g, c, t, s)):
            gate_failures.append(f"题{ordi}: 存在 task_score=None 的交付物,不可判定")
            continue
        if abs(g - c) > Fraction(2, 10):
            gate_failures.append(f"题{ordi}: |gold−control|={float(abs(g - c)):.2f} > 0.2")
        if g < 3:
            gate_failures.append(f"题{ordi}: gold={float(g):.2f} < 3.0")
        if g - t < 1:
            gate_failures.append(f"题{ordi}: gold−truncated={float(g - t):.2f} < 1.0")

        fmt = mtask["deliverable_formats"]
        if fmt == ["pdf"]:
            if g - s < 1:
                gate_failures.append(f"题{ordi}: gold−shuffled(pdf 删页)={float(g - s):.2f} < 1.0")
        else:
            exp = expected_sets[str(ordi)]["shuffled_expected_candidates"]
            exp_ids = {e["rubric_item_id"] for e in exp}
            by_id_gold = {r.rubric_item_id: r.state for r in store.load(GOLD, 1, exam_version)
                          if r.task_id == mtask["task_id"]}
            by_id_shuf = {r.rubric_item_id: r.state
                          for r in store.load("calib-shuffled", 1, exam_version)
                          if r.task_id == mtask["task_id"]}
            met_in_exp = [i for i in exp_ids if by_id_gold.get(i) == ItemState.CONDITION_MET]
            flipped = [i for i in met_in_exp
                       if by_id_shuf.get(i) == ItemState.CONDITION_NOT_MET]
            if met_in_exp:
                rate = Fraction(len(flipped), len(met_in_exp))
                print(f"题{ordi} shuffled 翻转率: {len(flipped)}/{len(met_in_exp)}")
                if rate < Fraction(60, 100):
                    gate_failures.append(f"题{ordi}: shuffled 翻转率 {float(rate):.0%} < 60%")
            else:
                gate_failures.append(f"题{ordi}: gold 在预期集内无 MET 条目,shuffled 闸空转")

        gold_not_met = [i + 1 for i, it in enumerate(mtask["items"])
                        if by_state_lookup(store, GOLD, 1, exam_version, mtask["task_id"],
                                           it["rubric_item_id"]) == ItemState.CONDITION_NOT_MET]
        print(f"题{ordi} gold 未达标条目(题内序号): {gold_not_met}")

    if all_counts["items"] and Fraction(
        all_counts["harness_failures"], all_counts["items"]
    ) > Fraction(2, 100):
        gate_failures.append("全轮 harness 失败率 > 2%")
    for product in [GOLD, *VARIANT_PRODUCTS.values()]:
        for rec in store.load(product, 1, exam_version):
            if rec.channel == "text" and rec.state is not None and rec.served_provider is not None:
                if rec.served_provider not in cfg.provider_order:
                    served_ok = False
    if not served_ok:
        gate_failures.append("存在非钉定 provider 服务的判定")

    print(f"总成本: ${total_cost:.2f} | judge 调用: {budget.used}/{budget.limit}")
    if gate_failures:
        print("放行闸未通过:")
        for f in gate_failures:
            print(f"  - {f}")
        return 1
    print("区分度检验通过:六道闸全绿,判分侧放行")
    return 0


def by_state_lookup(store, product, attempt, exam_version, task_id, rubric_item_id):
    for rec in store.load(product, attempt, exam_version):
        if rec.task_id == task_id and rec.rubric_item_id == rubric_item_id:
            return rec.state
    return None


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
