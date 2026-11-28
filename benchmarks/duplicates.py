"""Offline duplicate-request workload with three free fixture adapters."""

import asyncio
import json
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from mcp_prism_search import providers, runtime, search
from mcp_prism_search.cache import JsonFileCache
from mcp_prism_search.models import SearchResult
from mcp_prism_search.providers.base import ProviderSpec


async def main():
    runtime.install(None)
    calls = []

    def spec(name):
        async def fixture(req, count, timeout):
            calls.append(name)
            await asyncio.sleep(0.05)
            return [
                SearchResult(title="reference", url="https://example.org/doc", snippet="evidence")
            ]

        return ProviderSpec(name, "general", None, name, fixture)

    providers.SPECS = {n: spec(n) for n in ("keenable", "freeserp", "youcom_keyless")}
    report = []
    with TemporaryDirectory() as tmp:
        cache = JsonFileCache(Path(tmp), "search", 3600)
        for concurrency in (1, 4, 16, 32):
            for parallel in (False, True):
                calls.clear()
                args = (
                    {"providers": list(providers.SPECS)} if parallel else {"provider": "keenable"}
                )
                start = time.perf_counter()
                rows = await asyncio.gather(
                    *(
                        search.aggregated_search(
                            f"fixed {concurrency} {parallel}",
                            cache=cache,
                            include_answers=False,
                            **args,
                        )
                        for _ in range(concurrency)
                    )
                )
                report.append(
                    dict(
                        concurrency=concurrency,
                        parallel=parallel,
                        upstream_calls=len(calls),
                        success=sum(bool(r.results) for r in rows),
                        cache_hits=sum(r.cached for r in rows),
                        elapsed_ms=round((time.perf_counter() - start) * 1000, 2),
                    )
                )
    print(json.dumps(report))


if __name__ == "__main__":
    asyncio.run(main())
