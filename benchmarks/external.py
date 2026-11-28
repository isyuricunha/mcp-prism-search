"""Bounded comparison of official FreeSerp MCP and local Prism MCP (keyless only)."""

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import evaluate as ev
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from mcp_prism_search.models import SearchResult
from mcp_prism_search.search import valid_result_url

FIXTURE = """
from mcp_prism_search import providers
from mcp_prism_search.server import main
providers.build_registry()
providers.SPECS = {n: providers.SPECS[n] for n in ("keenable", "freeserp")}
main()
"""


def extract_rows(result):
    if result.is_error:
        raise ValueError("MCP tool error")
    data = result.structured_content
    if not isinstance(data, dict) or "results" not in data:
        data = next((json.loads(item.text) for item in result.content if item.type == "text"), None)
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise ValueError("Invalid result schema")
    if data.get("engine_fallback") or data.get("ok") is False or data.get("error"):
        raise ValueError("Degraded or failed web result")
    return [
        SearchResult(title=row["title"], url=row["url"], snippet=row.get("snippet") or "")
        for row in data["results"]
        if isinstance(row, dict)
        and isinstance(row.get("url"), str)
        and valid_result_url(row["url"])
        and isinstance(row.get("title"), str)
        and row["title"].strip()
    ][:5]


async def compare():
    cases = json.loads(ev.CORPUS.read_text())[:8]
    rows = []
    with TemporaryDirectory() as cache:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-c", FIXTURE],
            env={
                "MCP_CACHE_DIR": cache,
                "MCP_STATE_DIR": str(Path(cache) / "state"),
                "MCP_AI_SEARCH_ENABLED": "false",
                "MCP_TRANSPORT": "stdio",
                "MCP_LOG_LEVEL": "WARNING",
            },
        )
        async with (
            Client(params) as prism,
            Client("https://freeserp.ai/mcp", read_timeout_seconds=15) as external,
        ):
            tools = await external.list_tools()
            assert any(t.name == "freeserp_search" for t in tools.tools)
            for i, case in enumerate(cases):
                calls = [
                    (
                        "freeserp_mcp",
                        external,
                        "freeserp_search",
                        {"q": case["query"], "index": "web", "size": 5},
                    ),
                    (
                        "prism_freeserp",
                        prism,
                        "web_search",
                        {
                            "query": case["query"],
                            "provider": "freeserp",
                            "max_results": 5,
                            "include_answers": False,
                        },
                    ),
                    (
                        "prism_selected",
                        prism,
                        "web_search",
                        {
                            "query": case["query"],
                            "providers": ["keenable", "freeserp"],
                            "max_results": 5,
                            "include_answers": False,
                        },
                    ),
                ]
                # Rotate order to reduce systematic upstream edge-cache bias.
                calls = calls[i % 3 :] + calls[: i % 3]
                for label, client, name, arguments in calls:
                    start = time.perf_counter()
                    error = None
                    try:
                        async with asyncio.timeout(20):
                            docs = extract_rows(await client.call_tool(name, arguments))
                    except Exception as exc:
                        docs = []
                        error = type(exc).__name__
                    rows.append(
                        {
                            "id": case["id"],
                            "engine": label,
                            "ms": round((time.perf_counter() - start) * 1000, 2),
                            "error": error,
                            "ok": bool(docs),
                            "mrr5": ev.metrics(docs, case)["mrr10"],
                            "hit5": ev.metrics(docs, case)["hit5"],
                        }
                    )
                    await asyncio.sleep(1.1)
    summary = {}
    for label in ("freeserp_mcp", "prism_freeserp", "prism_selected"):
        group = [row for row in rows if row["engine"] == label]
        summary[label] = {
            "n": len(group),
            "ok": sum(row["ok"] for row in group),
            "p50_ms": round(statistics.median(row["ms"] for row in group), 2),
            "max_ms": max(row["ms"] for row in group),
            **{
                key: round(statistics.mean(row[key] for row in group), 6)
                for key in ("mrr5", "hit5")
            },
        }
    return {"summary": summary, "rows": rows}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-keyless", action="store_true", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        raise FileExistsError("Choose a fresh report filename")
    result = asyncio.run(compare())
    with args.report.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
    print(json.dumps(result["summary"], indent=2))
