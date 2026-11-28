"""Regression coverage for the offline relevance evaluation contract."""
import importlib
import math
from pathlib import Path

import pytest


@pytest.fixture
def evaluator(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "benchmarks"))
    return importlib.import_module("semantic")


def test_semantic_metrics_known_ranks(evaluator):
    qrels = {"a": 2, "b": 1, "c": 0, "d": 2}
    got = evaluator.metrics(["c", "a", "b"], qrels)
    expected_dcg = 3 / math.log2(3) + 1 / math.log2(4)
    ideal = 3 + 3 / math.log2(3) + 1 / math.log2(4)
    assert got == pytest.approx({
        "precision5": 2 / 5,
        "pooled_recall5": 2 / 3,
        "mrr5": .5,
        "ndcg5": expected_dcg / ideal,
    })


@pytest.mark.parametrize("ids,qrels", [
    (["unknown"], {"a": 2}),
    (["a", "a"], {"a": 2}),
    (["a"], {"a": 3}),
    (["a"], {"a": True}),
])
def test_semantic_metrics_reject_invalid_data(evaluator, ids, qrels):
    with pytest.raises(ValueError):
        evaluator.metrics(ids, qrels)


def test_semantic_metrics_empty_run(evaluator):
    assert evaluator.metrics([], {"a": 2}) == {
        "precision5": 0, "pooled_recall5": 0, "mrr5": 0, "ndcg5": 0,
    }
