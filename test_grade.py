"""判分核心的测试。全部使用虚构的合成数据。"""

from __future__ import annotations

import random
from fractions import Fraction
from pathlib import Path

import pytest

import grade
import llm
from grade import Cache, Item, Task, find_deliverable, judge_item, judge_pairwise, task_score_5


def make_task(rubric=(), ordinal=1) -> Task:
    return Task(
        ordinal=ordinal,
        task_id="synthetic-task",
        occupation="Synthetic Occupation",
        prompt="做一份虚构的月度汇总。",
        rubric=tuple(rubric),
        gold_urls=(),
        gold_names=(),
    )


def fake_ask(last_line: str, cost: float = 0.001):
    def _ask(prompt, **kw):
        return llm.Reply(text=f"简短理由。\n{last_line}", cost=cost)

    return _ask


# ---------- 题分公式 ----------

def test_题分按满分基数折算成五分制():
    assert task_score_5(6, 12) == Fraction(5, 2)


def test_满分基数为零时题分记零():
    assert task_score_5(0, 0) == Fraction(0)


def test_得分为负时题分记零而非负分():
    assert task_score_5(-4, 10) == Fraction(0)


def test_满分基数只累计正分条目():
    task = make_task([Item("a", "条件甲", 3), Item("b", "违规乙", -2), Item("c", "条件丙", 1)])
    assert task.total == 4


# ---------- 逐条判定的解析 ----------

def test_末行为MET判为成立(monkeypatch):
    monkeypatch.setattr(llm, "ask", fake_ask("MET"))
    met, cost = judge_item(make_task(), Item("a", "条件甲", 2), "材料", "m", None)
    assert met is True
    assert cost == 0.001


def test_末行为NOT_MET判为不成立(monkeypatch):
    monkeypatch.setattr(llm, "ask", fake_ask("NOT_MET"))
    met, _ = judge_item(make_task(), Item("a", "条件甲", 2), "材料", "m", None)
    assert met is False


def test_末行无关键词报告判分错误(monkeypatch):
    monkeypatch.setattr(llm, "ask", fake_ask("无法判断"))
    with pytest.raises(llm.JudgeError):
        judge_item(make_task(), Item("a", "条件甲", 2), "材料", "m", None)


# ---------- 两两对比的 A/B 位映射 ----------

@pytest.mark.parametrize(
    ("product_in_a", "verdict", "expected", "side"),
    [
        (True, "A", 1.0, "product"),
        (True, "B", 0.0, "product"),
        (False, "A", 0.0, "gold"),
        (False, "B", 1.0, "gold"),
        (True, "TIE", 0.5, "product"),
        (False, "TIE", 0.5, "gold"),
    ],
)
def test_产品放AB哪一位都换算成同一含义的分(monkeypatch, product_in_a, verdict, expected, side):
    """1 表示被测产品更好。产品被放到 B 位时,judge 说 A 必须换算成 0。"""
    monkeypatch.setattr(llm, "ask", fake_ask(verdict))

    class FixedRng:
        def random(self):
            return 0.1 if product_in_a else 0.9

    score, a_side, _ = judge_pairwise(
        make_task(), "产品交付物", "专家交付物", "m", None, FixedRng()
    )
    assert score == expected
    assert a_side == side


def test_对比时两份材料都进了提示词(monkeypatch):
    seen = {}

    def _ask(prompt, **kw):
        seen["prompt"] = prompt
        return llm.Reply("TIE", 0.0)

    monkeypatch.setattr(llm, "ask", _ask)
    judge_pairwise(make_task(), "产品甲的内容", "专家乙的内容", "m", None, random.Random(0))
    assert "产品甲的内容" in seen["prompt"]
    assert "专家乙的内容" in seen["prompt"]


# ---------- 交付物定位 ----------

def test_按题序号找到唯一交付物(tmp_path: Path):
    (tmp_path / "t03.docx").write_bytes(b"x")
    assert find_deliverable(tmp_path, 3).name == "t03.docx"


def test_找不到交付物时报错(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        find_deliverable(tmp_path, 7)


def test_同一题匹配到多个文件时报错(tmp_path: Path):
    (tmp_path / "t04.docx").write_bytes(b"x")
    (tmp_path / "t04.pdf").write_bytes(b"x")
    with pytest.raises(FileNotFoundError, match="只能有一个"):
        find_deliverable(tmp_path, 4)


# ---------- 缓存 ----------

def test_判过的条目重跑时命中缓存不再调用(tmp_path: Path):
    path = tmp_path / "c.jsonl"
    Cache(path).put("1:item-a", met=True, score=2)
    assert Cache(path).get("1:item-a")["met"] is True


def test_缓存未命中返回None(tmp_path: Path):
    assert Cache(tmp_path / "c.jsonl").get("1:item-a") is None


def test_缓存追加写不覆盖已有记录(tmp_path: Path):
    path = tmp_path / "c.jsonl"
    c = Cache(path)
    c.put("1:a", met=True, score=1)
    c.put("1:b", met=False, score=2)
    reloaded = Cache(path)
    assert reloaded.get("1:a")["met"] is True
    assert reloaded.get("1:b")["met"] is False


# ---------- 端到端:逐条判定加总成题分 ----------

def test_一题judge全说成立时拿满分(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(llm, "ask", fake_ask("MET"))
    monkeypatch.setattr(grade, "extract", lambda p: "材料文本")
    monkeypatch.setattr(grade, "RESULTS", tmp_path)
    (tmp_path / "t01.docx").write_bytes(b"x")
    task = make_task([Item("a", "条件甲", 3), Item("b", "条件乙", 2)])
    total = grade.run_rubric([task], tmp_path, "synthetic", "m", None)
    assert total == 5.0


def test_一题judge全说不成立时零分(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(llm, "ask", fake_ask("NOT_MET"))
    monkeypatch.setattr(grade, "extract", lambda p: "材料文本")
    monkeypatch.setattr(grade, "RESULTS", tmp_path)
    (tmp_path / "t01.docx").write_bytes(b"x")
    task = make_task([Item("a", "条件甲", 3), Item("b", "条件乙", 2)])
    assert grade.run_rubric([task], tmp_path, "synthetic", "m", None) == 0.0


def test_交付物缺失的题记零分并留备注(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(grade, "RESULTS", tmp_path)
    task = make_task([Item("a", "条件甲", 3)])
    assert grade.run_rubric([task], tmp_path, "synthetic", "m", None) == 0


@pytest.mark.parametrize("reply", ["", "A or B", "possibly A", "unknown"])
def test_对比回复畸形时报告错误(monkeypatch, reply):
    monkeypatch.setattr(llm, "ask", lambda *a, **k: llm.Reply(reply, 0))
    with pytest.raises(llm.JudgeError):
        judge_pairwise(make_task(), "answer", "gold", "m", None, random.Random(0))


@pytest.mark.parametrize("mode", ["rubric", "pairwise"])
def test_旧入口更换答案或模型后重新判分(monkeypatch, tmp_path, mode):
    monkeypatch.setattr(grade, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(grade, "extract", lambda p: p.read_text())
    monkeypatch.setattr(grade, "gold_text", lambda t: "gold")
    path = tmp_path / "t01.docx"
    path.write_text("first")
    calls = []

    def ask(*args, **kwargs):
        calls.append(1)
        return llm.Reply("MET" if mode == "rubric" else "TIE", 0)

    monkeypatch.setattr(llm, "ask", ask)
    task = make_task([Item("a", "condition", 2)])

    def run(model):
        if mode == "rubric":
            grade.run_rubric([task], tmp_path, "synthetic", model, None)
        else:
            grade.run_pairwise([task], tmp_path, "synthetic", model, None, 0)

    run("m1")
    run("m1")
    assert len(calls) == 1
    path.write_text("second")
    run("m1")
    run("m2")
    assert len(calls) == 3


@pytest.mark.parametrize("bad_text", [None, ""])
def test_专家文件无法读取时整体报错(monkeypatch, tmp_path, bad_text):
    from dataclasses import replace

    task = replace(make_task(), gold_names=("one.docx", "two.docx"),
                   gold_urls=("https://example.org/one", "https://example.org/two"))
    monkeypatch.setattr(grade, "GOLD_DIR", tmp_path)
    folder = tmp_path / grade.task_signature(task)
    folder.mkdir()
    for name in task.gold_names:
        (folder / name).write_bytes(b"synthetic")

    def extract(path):
        if path.name == "one.docx":
            return "valid part"
        if bad_text is None:
            raise grade.ExtractError("synthetic error")
        return bad_text

    monkeypatch.setattr(grade, "extract", extract)
    with pytest.raises(grade.ExtractError):
        grade.gold_text(task)
