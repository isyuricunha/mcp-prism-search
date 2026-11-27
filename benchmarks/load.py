"""Isolated end-to-end MCP load benchmark, synthetic provider, no external network."""

import argparse
import asyncio
import json
import math
import subprocess
import time
import uuid
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters


def options():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args()


FIXTURE = """
import asyncio
from dataclasses import replace
from mcp_prism_search import providers
from mcp_prism_search.models import SearchResult
from mcp_prism_search.server import main
async def fixture(request,count,timeout):
    await asyncio.sleep(.02)
    return [
        SearchResult(title=f"{request.query} reference {i}",
                     url=f"https://example.org/docs/{i}",
                     snippet="Deterministic evidence " * 20)
        for i in range(min(count, 10))
    ]
providers.build_registry()
providers.SPECS["keenable"]=replace(providers.SPECS["keenable"],search=fixture)
main()
"""


def run(*args):
    return subprocess.check_output(args, text=True, timeout=10).strip()


def metrics(name):
    code = """
import json
from pathlib import Path
p = Path("/sys/fs/cgroup")
s = dict(x.split() for x in (p / "cpu.stat").read_text().splitlines())
print(json.dumps({
    "cpu_usec": int(s["usage_usec"]),
    "memory_current": int((p / "memory.current").read_text()),
    "memory_peak": int((p / "memory.peak").read_text()) if (p / "memory.peak").exists() else None,
}))
"""
    return json.loads(run("docker", "exec", name, "python", "-c", code))


def disk(name):
    return int(run("docker", "exec", name, "du", "-sb", "/tmp/prism-bench-cache").split()[0])


def pct(a, p):
    a = sorted(a)
    return round(a[math.ceil(len(a) * p) - 1], 3)


async def main(args):
    name = "prism-bench-load-" + uuid.uuid4().hex[:8]
    params = StdioServerParameters(
        command="docker",
        args=[
            "run",
            "--rm",
            "-i",
            "--name",
            name,
            "--network",
            "none",
            "--memory",
            "512m",
            "--cpus",
            "2",
            "-e",
            "MCP_CACHE_DIR=/tmp/prism-bench-cache",
            "-e",
            "MCP_AI_SEARCH_ENABLED=false",
            "-e",
            "MCP_LOG_LEVEL=WARNING",
            "--entrypoint",
            "python",
            args.image,
            "-c",
            FIXTURE,
        ],
    )
    phases = []
    async with Client(params) as c:
        idle = await asyncio.to_thread(metrics, name)

        async def phase(concurrency, warm):
            n = 120
            sem = asyncio.Semaphore(concurrency)

            async def call(i):
                async with sem:
                    start = time.perf_counter()
                    response = await c.call_tool(
                        "web_search",
                        {
                            "query": f"fixture {concurrency} document {i}",
                            "provider": "keenable",
                            "max_results": 10,
                            "include_answers": False,
                        },
                    )
                    data = response.structured_content or {}
                    return (
                        (time.perf_counter() - start) * 1000,
                        not response.is_error and len(data.get("results", [])) == 10,
                        bool(data.get("cached")),
                    )

            before = await asyncio.to_thread(metrics, name)
            start = time.perf_counter()
            rows = await asyncio.gather(*(call(i) for i in range(n)))
            duration = time.perf_counter() - start
            after = await asyncio.to_thread(metrics, name)
            phase = {
                "concurrency": concurrency,
                "warm": warm,
                "n": n,
                "ok": sum(x[1] for x in rows),
                "hits": sum(x[2] for x in rows),
                "rps": round(n / duration, 2),
                "p50_ms": pct([x[0] for x in rows], 0.5),
                "p95_ms": pct([x[0] for x in rows], 0.95),
                "p99_ms": pct([x[0] for x in rows], 0.99),
                "cpu_seconds": round((after["cpu_usec"] - before["cpu_usec"]) / 1e6, 3),
                "memory_mib": round(after["memory_current"] / 2**20, 2),
                "cgroup_peak_mib": round(after["memory_peak"] / 2**20, 2)
                if after["memory_peak"]
                else None,
                "disk_bytes": await asyncio.to_thread(disk, name),
            }
            phases.append(phase)

        for concurrency in [1, 4, 8, 16]:
            await phase(concurrency, False)
            await phase(concurrency, True)
    report = {"image": args.image, "fixture_delay_ms": 20, "idle": idle, "phases": phases}
    with args.report.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    asyncio.run(main(options()))
