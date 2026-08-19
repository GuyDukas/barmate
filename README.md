# BarMate

An autonomous bar operations agent. It answers plain-language questions from a
venue manager by choosing its own data sources, reconciling the books against
what staff actually reported, and refusing to act beyond its authority.

Course project for Modern AI Agents, Technion, Spring 2026.
Team: Guy, Reut, Yuval.

## The problem

A bar manager arriving for an evening shift has questions that no single report
answers. How much gin is actually behind the bar, and can the number be
trusted? Is tonight busy enough to need another bartender? Did we lose stock
last night, or is the gap something the staff already explained in the group
chat?

Answering any of these means combining the point-of-sale ledger, physical stock
counts, delivery invoices, the rota, the reservation book, tonight's televised
fixtures, the weather, and the venue's own written operating procedures. The
sources disagree with each other, and the disagreements are the interesting
part.

BarMate reasons over that disagreement rather than reporting each source in
isolation.

## What makes it an agent

The agent decides which tools to call, in what order, and when it has enough to
answer. There is no intent router and no fixed pipeline: classification up front
is what turns an agent into a flowchart. Refusal, clarification and multi-step
planning are all decisions taken inside the loop.

- **Single agent**, a ReAct loop with a Reflect gate. No sub-agents.
- **The model never does arithmetic.** Forecasting and reconciliation are
  deterministic tools. The model orchestrates and explains; it does not add up.
- **Pull-based.** The agent answers when asked. No scheduled jobs, no outbound
  messages.

Modules are named `Reasoner`, `KnowledgeRetriever`, `Reflector` and `Reviser`,
and those names match across the architecture diagram, the `steps` trace and
`/api/agent_info`.

## Endpoints

| Endpoint | Method | Returns |
|---|---|---|
| `/` | GET | GUI, no authentication |
| `/api/team_info` | GET | Team and student details |
| `/api/agent_info` | GET | Description, purpose, prompt template, worked examples |
| `/api/model_architecture` | GET | Architecture diagram, `image/png` |
| `/api/execute` | POST | `{status, error, response, steps}` |

`/api/execute` must complete inside 300 seconds, the Vercel serverless ceiling.

## Data

The distinction below is deliberate and is the honest answer to the question of
whether an agent built on synthetic data proves anything.

**Simulated** is the venue's own books: stock, POS sales, physical counts,
delivery orders, reservations, rota, closing reports and the shift group chat.
No real bar's records were used.

**Real** is every external signal. Televised fixtures come from Sport5 and
livegames.co.il with a `source_url` preserved per row. Weather is Open-Meteo
ERA5 for Netanya. Holidays come from Hebcal.

### The anchor

Everything is frozen at **Sunday 2026-06-14, 18:00**, one hour before doors.
The system clock is never used; "tonight" means that evening.

The date is not arbitrary. It is the one date where real broadcast data covers
both tonight and the days ahead: ten listings that Sunday, eight of them live,
two involving Israeli clubs, then Maccabi Tel Aviv against Bayern Munich on the
Monday.

Real broadcast coverage stops on Wednesday 2026-06-17 and is deliberately left
unpatched. Asked about the following weekend, the correct answer is that
fixtures beyond Wednesday are unconfirmed. Saying so is a tested behaviour, not
a gap.

### Layout

```
data/source/         real inputs the build reads, never generated
data/external/       fetched by scripts/fetch_external.py, not invented
data/public/         the venue's books, what the agent may see
data/ground_truth/   evaluation answers, never shipped, never readable by the agent
data/runtime/        bundle.json.gz, what the deployed app loads
```

`data/ground_truth/` is excluded from deployment by `.vercelignore`. If the
agent can reach it, every evaluation number is worthless.

### Regenerating

```bash
python scripts/fetch_external.py   # needs internet, run locally
python -m sim.build                # rebuilds data/public
python -m sim.export_runtime       # rebuilds data/runtime/bundle.json.gz
```

Deterministic on seed 20260614. Fetch first, then build, then evaluate; the
weather multipliers change every downstream number when they switch on.

## Running locally

```bash
pip install -r requirements.txt
export LLMOD_API_KEY=...            # never commit this
flask --app api.index run --debug
```

## Testing

```bash
pytest tests/ -v      # unit tests, assert on tool returns
python -m eval.run    # nine scenarios against ground truth
```

Unit tests assert on what the deterministic tools return, never on model prose.
Scenario pass or fail lives in the eval harness, where non-determinism is
expected and handled.

## Architecture and design

`docs/specs/2026-08-19-barmate-design.md` covers the architecture, the tool
layer and the evaluation design.

## Services

| Service | Role |
|---|---|
| Supabase | Primary database. The venue ledger the agent reads. |
| Pinecone | Vector store for the 14 operations documents, 1536 dimensions, cosine. |
| LLMod.ai | `gpt-5.4-mini` for reasoning, `text-embedding-3-small` for retrieval. |
| Vercel | Deployment. |

Both databases are reached over their REST APIs rather than their Python SDKs,
to keep serverless cold start low.

Required environment variables:

```
LLMOD_API_KEY
SUPABASE_URL
SUPABASE_SERVICE_KEY
PINECONE_API_KEY
PINECONE_INDEX_HOST
```

None of these are committed. `.env` is gitignored.

## Conventions

- No emoji in code, comments, documentation or agent output.
- Comments explain why, not what.
- Hebrew appears throughout the data and in agent responses. Always specify
  `encoding="utf-8"`.
- Prefer the standard library in the request path. `pandas` is not worth its
  cold-start cost in a serverless function.
- Do not invent data. If an external source cannot be reached, say so and leave
  the feature inert.
