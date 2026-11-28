"""Compare static fallback orders on the same captured keyless responses."""

import argparse
import itertools
import json
import statistics
from pathlib import Path

import evaluate as ev

from mcp_prism_search.models import SearchResult
from mcp_prism_search.search import provider_order_results


def compare(cases, captures):
    index = {(row["id"], row["engine"]): row for row in captures}
    report = {}
    for order in itertools.permutations(ev.ENGINES):
        groups = {}
        for case in cases:
            elapsed = 0
            attempts = 0
            rows = []
            for engine in order:
                entry = index[(case["id"], engine)]
                elapsed += entry["ms"]
                attempts += 1
                rows = provider_order_results(
                    [SearchResult.model_validate(row) for row in entry["results"]],
                    case["query"],
                )
                if rows:
                    break
            groups.setdefault(case["split"], []).append(
                {
                    **ev.metrics(rows, case),
                    "attempts": attempts,
                    "adapter_ms": elapsed,
                    "success": bool(rows),
                }
            )
        report[" > ".join(order)] = {
            split: {k: round(statistics.mean(row[k] for row in rows), 6) for k in rows[0]}
            for split, rows in groups.items()
        }
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    cases = json.loads(ev.CORPUS.read_text())
    captures = [json.loads(line) for line in args.capture.read_text().splitlines()]
    output = json.dumps(compare(cases, captures), indent=2)
    if args.report:
        with args.report.open("x", encoding="utf-8") as handle:
            handle.write(output + "\n")
    print(output)
