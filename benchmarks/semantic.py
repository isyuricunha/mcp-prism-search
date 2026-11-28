"""Collect or evaluate a fixed semantic corpus; no paid APIs or implicit collection."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import statistics
from pathlib import Path

import evaluate as ev

from mcp_prism_search.models import SearchRequest, SearchResult
from mcp_prism_search.search import (
    _final_results,
    normalize_url_for_dedupe,
    provider_order_results,
    rank_results,
    reciprocal_rank_fusion,
)

CORPUS = Path(__file__).with_name("corpus-semantic-v1.json")
ENGINES = ("keenable", "freeserp")
K = 5


def doc_id(url):
    return hashlib.sha256(normalize_url_for_dedupe(url).encode()).hexdigest()


def rank_runs(case, index):
    query = case["query"]
    batches = [
        provider_order_results(
            [SearchResult.model_validate(row) for row in index[case["id"], engine]["results"]],
            query,
        )
        for engine in ENGINES
    ]
    return {
        "keenable": batches[0][:K],
        "freeserp": batches[1][:K],
        "lexical": _final_results(
            ev.interleave([rank_results(batch, query) for batch in batches]),
            SearchRequest(query=query, max_results=K),
        ),
        "rrf": reciprocal_rank_fusion(batches)[:K],
    }


def pool_documents(cases, index):
    pool = {}
    for case in cases:
        if not case["judgment_pilot"]:
            continue
        docs = {}
        for run in rank_runs(case, index).values():
            for row in run:
                docs.setdefault(
                    doc_id(row.url),
                    {
                        "url": normalize_url_for_dedupe(row.url),
                        "title": row.title,
                        "snippet": row.snippet[:400],
                    },
                )
        pool[case["id"]] = dict(sorted(docs.items()))
    return pool


def metrics(ids, qrels):
    if len(ids) > K or len(set(ids)) != len(ids):
        raise ValueError("Invalid ranking")
    if not set(ids) <= qrels.keys():
        raise ValueError("Unjudged result; create new judgments before scoring changed captures")
    if any(type(g) is not int or g not in (0, 1, 2) for g in qrels.values()):
        raise ValueError("Invalid relevance grade")
    grades = [qrels[doc] for doc in ids]
    relevant = sum(g > 0 for g in grades)
    total_relevant = sum(g > 0 for g in qrels.values())

    def dcg(values):
        return sum((2**g - 1) / math.log2(i + 2) for i, g in enumerate(values))

    ideal = dcg(sorted(qrels.values(), reverse=True)[:K])
    first = next((i + 1 for i, g in enumerate(grades) if g > 0), None)
    return {
        "precision5": relevant / K,
        "pooled_recall5": relevant / total_relevant if total_relevant else 0,
        "mrr5": 1 / first if first else 0,
        "ndcg5": dcg(grades) / ideal if ideal else 0,
    }


def evaluate(cases, captures, qrels):
    index = {(row["id"], row["engine"]): row for row in captures}
    expected = {(case["id"], engine) for case in cases for engine in ENGINES}
    if index.keys() != expected or len(captures) != len(expected):
        raise ValueError("Incomplete or duplicate capture")
    pool = pool_documents(cases, index)
    if pool.keys() != qrels.keys() or any(pool[c].keys() != qrels[c].keys() for c in pool):
        raise ValueError("Judgments must cover the exact blind pool")
    scores = {}
    for case in cases:
        if case["id"] not in qrels:
            continue
        for label, rows in rank_runs(case, index).items():
            scores.setdefault(label, []).append(
                metrics([doc_id(row.url) for row in rows], qrels[case["id"]])
            )
    return {
        "queries_collected": len(cases),
        "queries_judged": len(qrels),
        "documents_judged": sum(len(rows) for rows in qrels.values()),
        "grades": {
            str(g): sum(v == g for rows in qrels.values() for v in rows.values()) for g in (0, 1, 2)
        },
        "availability": {
            engine: {
                "attempts": len(cases),
                "nonempty": sum(bool(index[c["id"], engine]["results"]) for c in cases),
            }
            for engine in ENGINES
        },
        "scores": {
            label: {key: round(statistics.mean(row[key] for row in rows), 6) for key in rows[0]}
            for label, rows in scores.items()
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--collect-keyless", action="store_true")
    parser.add_argument("--pool", type=Path, help="Export blind pool for new judgments")
    parser.add_argument("--qrels", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    cases = json.loads(CORPUS.read_text())
    if args.collect_keyless:
        # Reuse the bounded collector, with the fixed two-engine allowlist.
        ev.ENGINES = ENGINES
        asyncio.run(ev.collect(cases, args.capture))
    captures = [json.loads(line) for line in args.capture.read_text().splitlines()]
    if args.pool:
        index = {(row["id"], row["engine"]): row for row in captures}
        with args.pool.open("x", encoding="utf-8") as handle:
            json.dump(pool_documents(cases, index), handle, ensure_ascii=False, indent=2)
    if args.qrels:
        report = evaluate(cases, captures, json.loads(args.qrels.read_text()))
        output = json.dumps(report, indent=2)
        if args.report:
            with args.report.open("x", encoding="utf-8") as handle:
                handle.write(output + "\n")
        print(output)


if __name__ == "__main__":
    main()
