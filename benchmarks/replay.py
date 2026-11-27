import argparse
import asyncio
import json
import statistics
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import evaluate as ev

from mcp_prism_search import providers, runtime, search
from mcp_prism_search.cache import JsonFileCache
from mcp_prism_search.models import SearchResult


async def main(args):
    cases = json.loads(ev.CORPUS.read_text())
    captures = [json.loads(line) for line in args.capture.read_text().splitlines()]
    index = {(x["id"], x["engine"]): x for x in captures}
    queries = {c["query"]: c["id"] for c in cases}
    runtime.install(None)
    providers.build_registry()
    providers.SPECS = {name: providers.SPECS[name] for name in ev.ENGINES}
    for engine in ev.ENGINES:

        async def fixture(req, count, timeout, engine=engine):
            row = index[(queries[req.query], engine)]
            if row["error"]:
                raise RuntimeError("captured upstream error")
            return [SearchResult.model_validate(x) for x in row["results"]]

        providers.SPECS[engine] = replace(providers.SPECS[engine], search=fixture)
    scores = {}
    with TemporaryDirectory() as tmp:
        cache = JsonFileCache(Path(tmp), "search", 3600)
        for case in cases:
            for name in (*ev.ENGINES, "fusion"):
                kwargs = {"providers": list(ev.ENGINES)} if name == "fusion" else {"provider": name}
                result = await search.aggregated_search(
                    case["query"],
                    max_results=10,
                    cache=cache,
                    include_answers=False,
                    safe_search="off",
                    time_filter="any",
                    allowed_domains=[],
                    blocked_domains=[],
                    preferred_domains=[],
                    **kwargs,
                )
                score = ev.metrics(result.results, case)
                scores.setdefault((case["split"], name), []).append(score)
                if result.results and all(
                    not index[(case["id"], e)]["error"]
                    for e in (ev.ENGINES if name == "fusion" else [name])
                ):
                    cached = await search.aggregated_search(
                        case["query"],
                        max_results=10,
                        cache=cache,
                        include_answers=False,
                        safe_search="off",
                        time_filter="any",
                        allowed_domains=[],
                        blocked_domains=[],
                        preferred_domains=[],
                        **kwargs,
                    )
                    assert cached.cached and cached.results == result.results
    report = {}
    for (split, name), rows in scores.items():
        report.setdefault(split, {})[name] = {
            k: round(statistics.mean(row[k] for row in rows), 6) for k in rows[0]
        }
    if args.report:
        with args.report.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Replay captured responses through actual Prism aggregation and cache."
    )
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    asyncio.run(main(parser.parse_args()))
