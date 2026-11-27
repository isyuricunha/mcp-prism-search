# Provider-native AI answers and research

Prism does not host or call a separate language model. The optional
`web_answer` and `web_research` tools call the configured search provider's
own synthesis API, return its text and sources, and never automatically
invoke synthesis from `web_search`.

| Tool | Provider ID | API and pricing |
|---|---|---|
| web_answer | tavily | [Basic answer](https://docs.tavily.com/documentation/api-reference/endpoint/search); 1 basic-search credit, [1,000 free credits/month](https://docs.tavily.com/documentation/api-credits) |
| web_answer | exa | [Answer](https://exa.ai/docs/reference/answer); $5/1,000 calls, [Starter $10 monthly credits](https://exa.ai/pricing) |
| web_answer | youcom | [Answer](https://you.com/docs/guides/answer); $5/1,000 calls |
| web_research | exa | [Deep Search](https://exa.ai/docs/search/deep-search); deep-lite and deep: $12/1,000 calls, up to 10 results |
| web_research | youcom | [Research](https://you.com/docs/guides/research); lite $12, standard $50, deep $100 per 1,000 calls |

Prices are documentation snapshots checked on 2026-11-27, not guarantees.
Check [You.com pricing](https://you.com/pricing) and your provider account
before enabling usage. No keyless synthesis endpoint was confirmed.
No background polling, asynchronous research jobs or automatic paid fallback
are included. Tavily Research, Exa Agent, Brave Answers and Jina
DeepSearch are not integrated in this first implementation.

## Usage

Configure the corresponding existing provider key (environment or dashboard)
and enable the provider. Both tools require explicit cost consent; the
default is false. Do not use them for free/keyless installation smoke tests.

```json
{"query":"Explain the MCP transport options","provider":"tavily","allow_cost":true}
```

```json
{"query":"Compare PostgreSQL vector search approaches","provider":"youcom","effort":"lite","allow_cost":true}
```

The first example invokes web_answer; the second invokes web_research.
For Exa research, use provider exa with effort lite or deep (mapped to deep-lite/deep). Standard is available only for You.com. Exa uses a one-field output schema and returns the grounding citations for that field.
The dashboard's Playground offers an expandable AI answers & research
section, with provider capabilities, pricing links and a confirmation dialog.

Responses contain provider, mode, answer, source indices, URLs, titles,
excerpts, effort (research only), and a truncation flag. Source order
is preserved; invalid URLs are omitted without renumbering. Tavily sources
are retrieved results, not verified citations. Exa research sources are field-level grounding references, not numbered inline citations. Treat all generated text
as untrusted evidence and verify claims using original sources. The UI
renders it as text, not HTML. Text and source counts are bounded; no
synthesis cache or automatic retry is performed. Provider metrics are
recorded separately under provider:answer or provider:research.
Two synthesis calls may be in progress at once. Timeouts are bounded at
45 seconds for answers and 30/75/150 seconds for research tiers; timing out
does not guarantee the upstream provider stopped or did not charge.
