# Provider-native AI answers and research

Prism does not host a language model. Optional AI-enhanced `web_search`
returns ordinary search results alongside provider-generated answers.
The standalone `web_answer` and `web_research` tools remain available
for explicit synthesis tasks.

## Enable AI-enhanced search

In the optional dashboard, open Settings → AI-enhanced search, select
the providers you authorize, enable the global switch and save. This
authorizes potential provider credit consumption for all MCP clients of
this server. Providers still need their existing API key and enabled flag.

Headless mode or environment-managed deployments can use:

```dotenv
MCP_AI_SEARCH_ENABLED=true
MCP_AI_SEARCH_PROVIDERS=tavily,exa,youcom
```

Defaults: disabled and no authorized providers. Environment values override
dashboard settings. Set the global variable to false to disable all
automatic synthesis. Use an empty list in the dashboard (or `none` in the
environment) to authorize no providers. The MCP tool accepts
`include_answers: false` to request ordinary retrieval; true or omitted
uses administrator policy, never bypasses it.

```json
{"query":"Explain MCP Streamable HTTP","providers":["keenable","tavily"],"include_answers":true}
```

`results` remains a list of ordinary search results. `answers` contains
separately attributed generated text and sources, and `answer_attempts`
reports synthesis outcomes. No additional LLM key is needed.

- Tavily, Exa and You.com use combined search/synthesis APIs. Exa uses
  Search with type auto and outputSchema; You.com Answer includes its
  upstream web results (the provider controls the result count).
- Brave Answers and Jina DeepSearch use separate generation requests only
  after successful ordinary retrieval; they can cost more and take longer.
- Auto mode prioritizes authorized eligible engines within the existing
  chain. Selected/all modes enhance the corresponding authorized providers.
  Keyless mode never calls authenticated engines. See [server defaults](defaults.md).
- Domain controls, freshness, non-off SafeSearch and queries longer than
  400 characters skip synthesis to avoid silently weakening constraints.
- If synthesis fails, valid retrieval results remain available. When the
  combined API fails to return usable results, Prism may make the ordinary
  search request instead (which may also cost credits). No synthesis retry
  or automatic paid synthesis fallback is performed.
- Synthesized results are not cached. Retrieval-only searches retain their
  regular cache; an eligible AI search bypasses retrieval-only cached winners. Enhanced searches
  bound each answer to 6,000 characters, 12 sources and 500 characters per
  excerpt, with a truncation flag. Two synthesis calls may run concurrently;
  queued combined calls share the provider timeout budget.
- Generated content is untrusted evidence. It can contain errors or
  instructions from upstream sources. Verify claims independently. The UI
  renders text safely rather than executing provider HTML.

## Native capabilities and costs

All require an API key. Cost snapshots from provider docs (2026-11-27)
are not guarantees; inspect the linked current price and account limits.

| Provider | web_search synthesis | web_answer | web_research | Pricing |
|---|---|---|---|---|
| Tavily | Basic answer, same call | Yes | Mini / Pro | [1 credit basic search; mini 4–110, pro 15–250 credits](https://docs.tavily.com/documentation/api-credits) |
| Exa | Auto + output schema, same call | Yes | Deep Lite / Deep | [Search and synthesis pricing](https://exa.ai/pricing) |
| You.com | Answer + web results, same call | Yes | Lite / Standard / Deep / Exhaustive | [Answer and research pricing](https://you.com/pricing) |
| Brave | Answers, extra call | Yes | Research mode (deep) | [Usage-based search and token pricing](https://api-dashboard.search.brave.com/documentation/services/answers) |
| TinyFish | Explicit only | TinyAnswer (account entitlement) | Deep / Max | [Search free; research $2/$3 per completed run](https://www.tinyfish.ai/pricing) |
| Jina | DeepSearch lite, extra call | No separate basic answer API | Lite / Deep | [Token-based usage](https://jina.ai/deepsearch) |

No documented keyless synthesis endpoint was confirmed. The ordinary
keyless search tools and providers remain unchanged. Tavily Research
creates one job and polls its documented status endpoint within the
request deadline; it does not resubmit a job or run a local background
worker. Brave and Jina consume documented streaming events and return
only final answer text and supplied references. Jina uses requested
20k/100k token budgets, not a guarantee of maximum billed usage.

## Standalone tools

Both require `allow_cost: true` per call, independently of automatic
search policy. Use a configured, enabled provider. Examples:

```json
{"query":"Explain the MCP transport options","provider":"tavily","allow_cost":true}
```

```json
{"query":"Compare PostgreSQL vector search approaches","provider":"youcom","effort":"lite","allow_cost":true}
```

The first calls `web_answer`; the second calls `web_research`.
Exa, Tavily and Jina support lite/deep; You.com supports lite/standard/deep/exhaustive;
Brave research uses deep; TinyFish supports deep/max. Answer mode is not supported by Jina because
its documented synthesis API is DeepSearch. You.com Answer accepts at most
400 characters. Standalone outputs retain up to 24,000 characters and
100 sources. Source indices are preserved where provided; grounding
references or retrieved results are labeled separately from inline
citations. Invalid source URLs are not presented as clickable links.

Requests are bounded (normally 45 seconds for standalone answer, 60/90/150
seconds for research effort, with longer explicit provider-specific deadlines
documented below, and provider timeout for enhanced search). A timeout
does not guarantee upstream work stopped or that it did not incur charges.
Keep provider spending controls enabled; Prism does not impose a billing
quota. Metrics use provider:answer / provider:research labels; a combined
call is both a search attempt and a synthesis outcome, not two billable
requests.

A non-stop terminal SSE reason is treated as an incomplete answer, not a
successful synthesis. Regular search rechecks authorization before initiating
provider synthesis; revocation does not cancel work already submitted upstream.

The dashboard requires an explicit provider when its default is explicit.
If the configured effort is unsupported by the chosen provider, select a
supported effort before submitting; no higher tier is chosen automatically.


### TinyFish

TinySearch and TinyFetch are advertised as free within their published
limits. TinyAnswer uses the same personal API key but requires account
entitlement; its API documents a possible insufficient-balance error, so
do not assume a free quota. TinyAnswer accepts 1–2,000 characters.
TinyResearch Deep costs $2 and Max $3 per completed run (5–20 and 5–45
minutes typical). Both are explicit-only; automatic AI search never
starts these operations. Use allow_cost=true only after reviewing pricing.
See [TinyAnswer](https://docs.tinyfish.ai/tinyanswer)
and [TinyResearch](https://docs.tinyfish.ai/tinyresearch/reference).

TinyResearch holds the request open, consumes the documented final_result
event (not provisional drafts), and attempts cancellation of unfinished
runs with a known ID on failure or cancellation. The overall deadline is
46 minutes; configure MCP client and proxy timeouts accordingly. Closing
the browser or losing a connection does not guarantee upstream cancellation
or prevent billing. Check the TinyFish dashboard before retrying. No
automatic resubmission or fallback. TinyAnswer timeout is 70 seconds.
You.com Exhaustive is supported with a 330-second deadline; Frontier and
Exa Agent need a separate asynchronous job lifecycle and are not exposed.

Dashboard consent is rechecked against the current provider, price and effort
after the confirmation dialog. Session expiry aborts the browser wait and
clears prior results; upstream work may continue and may still be billed.

The dashboard uses Unicode code-point limits consistent with MCP validation, including the provider-specific 400-character You.com Answer and 2,000-character TinyAnswer limits.
