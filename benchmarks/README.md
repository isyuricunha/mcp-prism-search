# Prism Search benchmarks

The suite is separate from ordinary tests and production operation. No live
requests are made by default. Live collection explicitly selects only
Keenable, FreeSerp and the public You.com MCP, with no keys or native AI.
Check shared quotas before collecting; You.com publishes a 100 queries/day
allowance. There are 50 requests per engine, no automatic retries, a
12-second call budget and at least 1.1 seconds between calls to each engine.

## Reproduce

From the repository root, use the normal installed development environment
(Python 3.12+ and the locked requirements, as in the main README).
Store captures outside the repo; they contain third-party text.

```bash
mkdir -p /tmp/prism-bench
# Explicit opt-in to live public searches. Use a new filename for each run.
.venv/bin/python benchmarks/evaluate.py --collect-keyless \
  --capture /tmp/prism-bench/capture.jsonl \
  --report /tmp/prism-bench/rank.json
# Offline comparison of the same returned documents:
.venv/bin/python benchmarks/evaluate.py --capture /tmp/prism-bench/capture.jsonl
# Offline replay through actual orchestration and cache:
.venv/bin/python benchmarks/replay.py --capture /tmp/prism-bench/capture.jsonl
# Reproduce isolated synthetic load (Linux Docker, cgroup v2):
docker build -t prism-benchmark:local .
.venv/bin/python benchmarks/load.py --image prism-benchmark:local \
  --report /tmp/prism-bench/load.json
```

Use a clean environment with default server settings for comparable runs.
Replay replaces the registry with fixture-only adapters and uses a temporary
cache. The load container has no network, 2 CPU cores and 512 MiB memory;
120 cold and 120 repeated calls at each of 1, 4, 8 and 16 concurrency.
Memory is cgroup-accounted memory, not process RSS. Throughput and latency
include MCP serialization and client processing; CPU includes sampling
overhead. The fixture sleeps 20 ms per uncached search. This is not a
measurement of real upstream throughput.

## 50-query relevance corpus (2026-11-27)

The corpus includes technical documentation, standards, science and public
information in English and Portuguese. There is one predeclared host/path
target per query, with an alternating 25/25 development/validation split.
Eight queries overlap the exploratory pilot. The split is not an independent
test of general semantic search: the experiment is small, engineering-heavy,
and the validation scores informed the choice of algorithm.

The evaluator treats a normalized host/path match as a known-target hit.
MRR@10 is the mean reciprocal rank of the first matching result, or zero on
a miss. Target nDCG@10 is 1/log2(rank+1) for that same target (zero on a miss).
Hit@k is the proportion with a target in the first k results. Unjudged
documents may be relevant. We deliberately do not report general precision,
recall or graded nDCG: those require broader relevance judgments.

All algorithms receive identical captured lists (up to 10 documents per
provider). Historical baseline is commit `d37c23b`, represented by the
lexical scorer still used for legacy search; the JSON labels it "current"
as it was current at collection time. RRF uses the published formula with
k=60, stable ties and one vote per canonical URL per provider. A separate
replay runs the new production pipeline against the same capture, including
cache validation and simulated captured failures.

| Strategy | Development MRR@10 | Validation MRR@10 |
|---|---:|---:|
| Keenable, previous | 0.518 | 0.576 |
| Keenable, original | 0.773 | 0.626 |
| FreeSerp, previous | 0.180 | 0.426 |
| FreeSerp, original | 0.180 | 0.408 |
| You.com, previous | 0.463 | 0.484 |
| You.com, original | 0.603 | 0.604 |
| Fusion, previous | 0.396 | 0.531 |
| Fusion, round robin | 0.773 | 0.630 |
| Fusion, RRF | 0.736 | 0.700 |

RRF improved validation MRR by 31.7% and target nDCG from 0.588 to 0.736;
Hit@10 improved from 76% to 84%. Across all 50 queries MRR increased from
0.464 to 0.718. Original order was slightly worse for FreeSerp; the consistent
single-provider policy avoids overriding upstream judgment. The existing
fallback preference remains unchanged: You.com-first replay is promising,
but its availability and daily quota warrant a larger evaluation.

| Provider | Nonempty / attempts | P50 ms | P95 ms | P99 ms |
|---|---:|---:|---:|---:|
| Keenable | 50/50 | 545 | 913 | 1105 |
| FreeSerp | 50/50 | 1072 | 3492 | 4501 |
| You.com public MCP | 36/50 | 1086 | 1307 | 1437 |

Times are adapter-level, including failures, not end-to-end MCP times.
Fourteen You.com calls returned errors; the capture does not identify the
upstream reason. P99 is the maximum of 50 samples, not a robust tail estimate.

## Recorded results

- [Ranking comparison and latency](results/2026-11-27-ranking-report.json)
- [Production pipeline replay](results/2026-11-27-ranking-production-replay.json)
- [Earlier eight-query exploratory pilot](results/2026-11-27-pilot.json)
- [Earlier synthetic load baseline](results/2026-11-27-load.json)
- [New ranking candidate load](results/2026-11-27-load-candidate.json)

Both isolated load runs returned 960/960 successful responses and 480/480
expected cache hits. The new candidate reached 224.27 cold and 339.41 warm
requests per second (previous: 171.27 and 278.59); peak cgroup memory was
69.40 MiB (previous: 69.21 MiB). One run per build on the same LAB host
does not establish causality or statistical significance. The fixture uses
one provider, not concurrent multi-provider RRF.

Aggregate JSON only is checked in: third-party snippets and private operator
captures are not redistributed. The exploratory pilot includes direct You.com
comparison from a separate live pass, not a controlled same-response timing
comparison. The original pilot scores are retained as recorded.
No paid provider was contacted. No claim of broad search superiority is made.

## References

- Cormack, Clarke and Buettcher, [Reciprocal rank fusion outperforms Condorcet
  and individual rank learning methods](https://doi.org/10.1145/1571941.1572114),
  SIGIR 2009.
- [ir-measures retrieval evaluation measures](https://ir-measur.es/en/latest/measures.html).
- [You.com MCP documentation and public allowance](https://you.com/docs/agents/mcp-server).
- [Linux cgroup v2 documentation](https://docs.kernel.org/admin-guide/cgroup-v2.html).


## Concurrent identical searches and selected-provider load

```bash
.venv/bin/python benchmarks/duplicates.py
.venv/bin/python benchmarks/routing.py --capture /tmp/prism-bench/capture.jsonl
.venv/bin/python benchmarks/load.py --mode selected --image prism-benchmark:local \
  --report /tmp/prism-bench/selected-load.json
```

The duplicate workload calls the real aggregator directly with three
synthetic keyless providers, each delaying 50 ms. Every burst starts with
a unique cold key. The baseline is commit `950aeff`; the candidate
coordinates identical pending retrievals on the same loop and cache key.
No external network or paid services are used.

| Identical requests | Provider calls before | Provider calls after |
|---:|---:|---:|
| 4, single provider | 4 | 1 |
| 16, single provider | 16 | 1 |
| 32, single provider | 32 | 1 |
| 4, three providers | 12 | 3 |
| 16, three providers | 48 | 3 |
| 32, three providers | 96 | 3 |

Followers re-read the normal cache after the leader finishes. This does
not cache failures or partial parallel responses, suppress cancellation, or
share keyed/AI operations. Failed or cancelled leaders release followers
to perform independent retrieval. This is process/event-loop local,
not distributed locking. Requests without a positive TTL bypass it.

The selected MCP load fixture has three independent adapters, each with
10 results (five shared URLs), 20 ms delay, 2 CPU cores, 512 MiB memory
and no network. At concurrency 1/4/8/16, all 960 calls succeeded and all
480 expected warm calls hit cache. Maximum observed cold/warm throughput
was 111.35/345.94 req/s; peak cgroup memory was 70.34 MiB.
This is a new workload baseline, not a controlled comparison to the
earlier single-provider load. The older candidate image was unavailable.
Docker loads require a locally built image and never implicitly pull it.

## Static fallback replay

All six permutations of the three providers are evaluated on the same
50-query capture. Adapter elapsed times include failed attempts and are
summed until the first nonempty result. This is a static replay, not a
live measurement of the adaptive health mechanism.

| First choice, second choice | Mean MRR@10 | Mean adapter ms | Mean attempts |
|---|---:|---:|---:|
| Keenable, FreeSerp | 0.700 | 586 | 1.00 |
| FreeSerp, Keenable | 0.294 | 1359 | 1.00 |
| You.com, Keenable | 0.798 | 1126 | 1.28 |

All three orders reached a nonempty result on all 50 cases. You.com first
improves known-target ranking but consumes more requests and had 14 captured
failures. Keenable returned 50/50 results. The production preference order
is unchanged until broader relevance, quota and availability evidence exists.

Aggregate results:
- [Duplicate workload](results/duplicate-requests-v3.json)
- [Selected MCP load](results/selected-load-v3.json)
- [Static fallback replay](results/routing-v3.json)

Synchronization reference: [Python asyncio synchronization primitives](https://docs.python.org/3/library/asyncio-sync.html).

- [Selected MCP load, final-code repeat](results/selected-load-v3-repeat.json)
