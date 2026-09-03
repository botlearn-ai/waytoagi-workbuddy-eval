"""区分度检验的放行闸判定(纯函数,供 scripts/calibration.py 调用)。

全部消息只用题序号,不携带 task_id/rubric_item_id/criterion。

pdf 的格式例外(显式声明,非静默替换):pdf 内容流不可改写,shuffled
变体实际是「删除数字密集的一半页」,属截半类改坏,因此对 pdf 任务的
shuffled 闸不用翻转率,改用与 truncated 相同的绝对分差规则
(gold − shuffled ≥ 1.0),并在 notes 里说明。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from fractions import Fraction

from gdpval_eval.judge import JudgeConfig
from gdpval_eval.verdicts import VerdictRecord

LOSS_NOTE_THRESHOLD = Fraction(2, 10)
LOSS_CEILING = Fraction(1)
GOLD_FLOOR = Fraction(3)
ABSOLUTE_GAP = Fraction(1)
FLIP_RATE_FLOOR = Fraction(60, 100)
OVERALL_HARNESS_CEILING = Fraction(2, 100)


@dataclass(frozen=True)
class TaskGateInput:
    ordinal: int
    fmt: str
    gold: Fraction | None
    control: Fraction | None
    truncated: Fraction | None
    shuffled: Fraction | None
    flip_rate: Fraction | None  # None = 预期集内无 gold-MET 条目(非 pdf 时是闸失败)


def task_gate_failures(g: TaskGateInput) -> tuple[list[str], list[str]]:
    """Return (failures, notes) for one task's score gates."""
    failures: list[str] = []
    notes: list[str] = []

    if any(v is None for v in (g.gold, g.control, g.truncated, g.shuffled)):
        failures.append(f"题{g.ordinal}: 存在 task_score=None 的交付物,不可判定")
        return failures, notes

    # 改坏件都经过同一条读写链路;control 是它们的公平零点。
    # gold 与 control 的差是解析/写回的量具损耗:>0.2 记入 notes 供人工过目,
    # 超过 1.0 说明量具本身失真到不可用,才作为闸失败。
    loss = abs(g.gold - g.control)
    if loss > LOSS_CEILING:
        failures.append(f"题{g.ordinal}: |gold−control|={float(loss):.2f} > 1.0,量具失真")
    elif loss > LOSS_NOTE_THRESHOLD:
        notes.append(f"题{g.ordinal}: 量具损耗 |gold−control|={float(loss):.2f}(基准已用 control)")

    if g.gold < GOLD_FLOOR:
        failures.append(f"题{g.ordinal}: gold={float(g.gold):.2f} < 3.0")

    # 绝对分差闸按「该题可检出的变体」施加(红队 F1 修法):哪种改坏对一道题
    # 有杀伤取决于评分点分布——砍半工作簿对评分点集中于单个主表的 xlsx 天然
    # 低杀伤,而数字打乱在同一题上掉 1.5+ 分。要求至少一个变体 ≥1.0,
    # 两个分差都打印,报告透明。
    gap_truncated = g.control - g.truncated
    gap_shuffled = g.control - g.shuffled
    notes.append(
        f"题{g.ordinal}: 分差 control−truncated={float(gap_truncated):.2f}, "
        f"control−shuffled={float(gap_shuffled):.2f}"
    )
    if max(gap_truncated, gap_shuffled) < ABSOLUTE_GAP:
        failures.append(
            f"题{g.ordinal}: 无任何改坏变体与 control 的分差 ≥1.0"
            f"(truncated {float(gap_truncated):.2f} / shuffled {float(gap_shuffled):.2f})"
        )

    if g.fmt == "pdf":
        notes.append(
            f"题{g.ordinal}: pdf 内容流不可改写,shuffled=删数字密集页(截半类),"
            "翻转率闸不适用"
        )
    elif g.flip_rate is None:
        failures.append(f"题{g.ordinal}: gold 在预期集内无 MET 条目,shuffled 闸空转")
    elif g.flip_rate < FLIP_RATE_FLOOR:
        failures.append(f"题{g.ordinal}: shuffled 翻转率 {float(g.flip_rate):.0%} < 60%")

    return failures, notes


def provider_gate_failures(
    records: Iterable[VerdictRecord], cfg: JudgeConfig
) -> list[str]:
    """闸⑤:每条已定态的 text 判定必须由钉定 provider 与钉定 model 服务;
    served 字段缺失同样是失败(无法证明路由未漂移)。消息只带计数。"""
    bad_provider = 0
    bad_model = 0
    missing = 0
    for rec in records:
        if rec.channel != "text" or rec.state is None:
            continue
        if rec.reason in ("over_context", "empty_extraction"):
            continue  # 未发生外部调用的 NO_EVIDENCE
        if rec.served_provider is None or rec.served_model is None:
            missing += 1
            continue
        if rec.served_provider not in cfg.provider_order:
            bad_provider += 1
        if rec.served_model != cfg.model:
            bad_model += 1

    failures: list[str] = []
    if missing:
        failures.append(f"{missing} 条已定态判定缺 served provider/model,路由不可证")
    if bad_provider:
        failures.append(f"{bad_provider} 条判定由非钉定 provider 服务")
    if bad_model:
        failures.append(f"{bad_model} 条判定由非钉定 model 服务")
    return failures


def overall_harness_failure(total_items: int, harness_failures: int) -> str | None:
    if total_items and Fraction(harness_failures, total_items) > OVERALL_HARNESS_CEILING:
        return "全轮 harness 失败率 > 2%"
    return None
