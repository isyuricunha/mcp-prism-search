"""Offline rank evaluation and opt-in keyless collection for fixed public queries."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import statistics
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import unquote, urlsplit

from mcp_prism_search import providers, runtime
from mcp_prism_search.models import SearchRequest, SearchResult
from mcp_prism_search.search import (
    _dedupe_results,
    _final_results,
    rank_results,
    reciprocal_rank_fusion,
)

CORPUS = Path(__file__).with_name("corpus-v1.json")
ENGINES = ("keenable", "freeserp", "youcom_keyless")
DEPTH = 10


def target_match(url, case):
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    path = re.sub(r"/+", "/", unquote(parsed.path)).casefold()
    target = case["host"].lower()
    return (host == target or host.endswith("." + target)) and (
        case["path_contains"].casefold() in path
    )


def interleave(batches):
    return [
        batch[i]
        for i in range(max(map(len, batches), default=0))
        for batch in batches
        if i < len(batch)
    ]


def metrics(rows, case):
    rank = next((i for i, row in enumerate(rows[:10], 1) if target_match(row.url, case)), 0)
    # One known target per query; unknown documents are not judged irrelevant.
    return {
        "mrr10": 1 / rank if rank else 0,
        "target_ndcg10": 1 / math.log2(rank + 1) if rank else 0,
        "hit1": int(rank == 1),
        "hit5": int(0 < rank <= 5),
        "hit10": int(rank > 0),
    }


def evaluate(cases, captures):
    by_case = defaultdict(dict)
    for row in captures:
        by_case[row["id"]][row["engine"]] = row
    scores = defaultdict(list)
    availability = defaultdict(list)
    for case in cases:
        request = SearchRequest(query=case["query"], max_results=DEPTH)
        batches = []
        for engine in ENGINES:
            capture = by_case[case["id"]].get(engine)
            if capture is None:
                raise ValueError("Missing capture: " + case["id"] + "/" + engine)
            raw = [SearchResult.model_validate(row) for row in capture["results"]]
            batches.append(raw)
            availability[engine].append(capture)
            for strategy, rows in (
                ("original", _dedupe_results(raw)),
                ("current", _final_results(rank_results(raw, request.query), request)),
            ):
                scores[(case["split"], engine + "/" + strategy)].append(metrics(rows, case))
        candidates = {
            "fusion/current": _final_results(
                interleave([rank_results(batch, request.query) for batch in batches]), request
            ),
            "fusion/round_robin": _dedupe_results(interleave(batches)),
            "fusion/rrf60": reciprocal_rank_fusion(batches),
        }
        # Fixed preferred order proxy, not live adaptive health routing.
        for label, order in (
            ("fallback/keenable_first", (0, 1, 2)),
            ("fallback/youcom_first", (2, 0, 1)),
        ):
            candidates[label] = next((_dedupe_results(batches[i]) for i in order if batches[i]), [])
        for label, rows in candidates.items():
            scores[(case["split"], label)].append(metrics(rows, case))
    output = {}
    for (split, label), rows in sorted(scores.items()):
        output.setdefault(split, {})[label] = {
            key: round(statistics.mean(row[key] for row in rows), 6) for key in rows[0]
        }

    def percentile(values, p):
        return sorted(values)[math.ceil(len(values) * p) - 1]

    output["availability"] = {
        engine: {
            "n": len(rows),
            "ok": sum(bool(row["results"]) for row in rows),
            "p50_ms": percentile([row["ms"] for row in rows], 0.5),
            "p95_ms": percentile([row["ms"] for row in rows], 0.95),
            "p99_ms": percentile([row["ms"] for row in rows], 0.99),
        }
        for engine, rows in availability.items()
    }
    return output


async def collect(cases, destination):
    # Fixed allowlist, never access configured keyed APIs or panel state.
    runtime.install(None)
    providers.build_registry()
    assert all(not providers.SPECS[name].needs for name in ENGINES)
    if destination.exists():
        raise FileExistsError("Choose a fresh capture path; no automatic retries")

    async def engine_worker(engine, handle):
        for case in cases:
            request = SearchRequest(query=case["query"], max_results=DEPTH)
            start = time.perf_counter()
            error = None
            try:
                async with asyncio.timeout(12):
                    results = await providers.run_provider(engine, request, DEPTH, 12)
            except Exception as exc:
                results = []
                error = type(exc).__name__
            entry = {
                "id": case["id"],
                "engine": engine,
                "ms": round((time.perf_counter() - start) * 1000, 2),
                "error": error,
                "results": [row.model_dump() for row in results],
            }
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            handle.flush()
            await asyncio.sleep(1.1)

    with destination.open("x", encoding="utf-8") as handle:
        await asyncio.gather(*(engine_worker(engine, handle) for engine in ENGINES))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument(
        "--collect-keyless",
        action="store_true",
        help="Opt in to 50 live requests per engine; check shared quotas first",
    )
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    cases = json.loads(CORPUS.read_text())
    if args.collect_keyless:
        asyncio.run(collect(cases, args.capture))
    captures = [json.loads(line) for line in args.capture.read_text().splitlines()]
    report = evaluate(cases, captures)
    output = json.dumps(report, indent=2)
    if args.report:
        with args.report.open("x", encoding="utf-8") as handle:
            handle.write(output + "\n")
    print(output)


if __name__ == "__main__":
    main()
