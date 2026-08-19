# BarMate

An autonomous bar operations agent. It answers plain-language questions from a
venue manager by choosing its own data sources, reconciling the books against
what staff actually reported, and refusing to act beyond its authority.

Course project for Modern AI Agents, Technion, Spring 2026.

**Live:** https://barmate-puce.vercel.app
**Repository:** https://github.com/GuyDukas/barmate

| Student | ID |
|---|---|
| Guy Dukas | 326692662 |
| Reut Ness | 318738127 |
| Yuval Belelovsky | 326156536 |

Group batch and order number: `2_8`.

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

## Results

Nine scenarios, run against the live deployment:

```
GT001  PASS  ambiguity                     GT006  PASS  knowledge-explained variance
GT002  PASS  conflict                      GT007  PASS  silent loss detection
GT003  PASS  multi-source forecast         GT008  PASS  invoice discrepancy
GT004  PASS  open-ended readiness          GT009  PASS  unknown entity
GT005  PASS  scope refusal

9/9 scenarios passed
34/34 figures traceable to a tool result (100%)
```

That second line is the measure worth reading. Every number the agent quoted
appears in something a tool returned. An answer can name the right product for
the wrong reason, but a figure the tools never produced is invention however
plausible it reads.

Quantitative measures over the whole dataset, not just the nine questions
(`python -m eval.metrics`, offline, no model calls):

| Measure | Result |
|---|---|
| Report parsing, product identification | precision 1.000, recall 1.000 over 1,142 mentions in 286 reports |
| Report parsing, clarification flag | 286/286 correct |
| Report parsing, extracted figures | mean absolute error 0.047 units used, 0.113 units remaining |
| Discrepancy detection, arithmetic alone | precision 0.857, recall 0.600 |
| Discrepancy detection, with reported evidence | precision 0.900, recall 0.900 |
| Happy-hour lines wrongly called shrinkage | 0 of 8 |
| Forecast error, lines moving 10+ units | 42% MAPE (52% across everything that moved) |

The two detection rows are the design working as intended. **Arithmetic finds
the losses nobody mentioned; the group chat finds the ones somebody logged.**
Neither route alone gets past 60% recall, and the agent has both.

142 unit tests, all passing.

## What makes it an agent

The agent decides which tools to call, in what order, and when it has enough to
answer. There is no intent router and no fixed pipeline: classification up
front is what turns an agent into a flowchart. Refusal, clarification and
multi-step planning are all decisions taken inside the loop.

- **Single agent**, a ReAct loop with a Reflect gate. No sub-agents.
- **The model never does arithmetic.** Forecasting and reconciliation are
  deterministic tools. The model orchestrates and explains; it does not add up.
- **Pull-based.** The agent answers when asked. No scheduled jobs, no outbound
  messages.
- **Read-only by construction.** No tool in the registry writes, orders or
  sends. That is a property of the dispatch table rather than a promise in the
  prompt, because a prompt can be talked around and a missing tool cannot.

Modules are named `Reasoner`, `KnowledgeRetriever`, `Reflector` and `Reviser`,
and those names match across the architecture diagram, the `steps` trace and
`/api/agent_info`. A test fails if any module on the diagram never appears in a
captured trace.

Two caps bound a request. Eight iterations stop a model that keeps calling
tools; a 210-second wall-clock budget stops a handful of iterations that are
each slow, which on a 90-second LLM timeout can outlast Vercel's 300-second
ceiling without ever reaching the iteration cap.

## Tools

| Tool | Returns |
|---|---|
| `resolve_product` | Catalogue match, or `found=false` with no substitution |
| `resolve_category` | Every product in a category; near-misses corrected, misses list what exists |
| `get_inventory` | Book stock, last count, staleness, whether a protocol widens the line |
| `reconcile` | Book stock against a physical figure you supply, classified per RAG-013 |
| `variance_envelope` | How far this product's books and counts normally disagree |
| `find_discrepancies` | Every product with a loss somebody reported since the last count |
| `get_sales_history` | Weekday baselines, stockout losses |
| `forecast_reorder` | Demand with RAG-004 multipliers, minus stock and orders in flight |
| `forecast_category` | The same for a whole category in one call, declaring what it excludes |
| `get_context` | Fixtures, bookings, weather, holidays, and how far each source reaches |
| `get_shift_reports` | Closing reports with figures extracted, ambiguity flagged |
| `get_chat` | Shift-group messages, filterable by product in either language |
| `list_knowledge` / `get_document` / `search_knowledge` | The operations manual |

## Two decisions worth explaining

**Book stock believes the invoice, not the delivery.**

```
book = last reported count
     + units INVOICED as delivered since that count
     - units the POS accounts for since that count
```

When a delivery lands short the books believe the invoice, and that belief is
the discrepancy. A tool that quietly substituted the quantity actually received
would erase the thing it exists to find. This reproduces the ground-truth book
figure for all 61 products exactly.

**Materiality is derived per product, never fixed.** Tanqueray moves under a
bottle between counts; Coca-Cola moves thirty. Every closed window between two
physical counts is replayed and the residual recorded, and the 90th percentile
of a product's own residuals becomes its threshold: 0.27 units on Tanqueray
against 14.70 on Coca-Cola. No single constant serves both, and a threshold
near half a unit now sits inside the ordinary variation of every busy line.

Happy-hour lines come out wide by measurement rather than by exception, because
doubled depletion against single-rung revenue is a persistent shortfall. What
the envelope does not do is excuse a gap that clears it: Carlsberg 30L is both
a happy-hour line and the product two kegs were pulled from, and a rule that
stopped at "happy hour explains it" would bury the keg.

## Endpoints

| Endpoint | Method | Returns |
|---|---|---|
| `/` | GET | GUI, no authentication |
| `/api/team_info` | GET | Team and student details |
| `/api/agent_info` | GET | Description, purpose, prompt template, worked examples |
| `/api/model_architecture` | GET | Architecture diagram, `image/png` |
| `/api/execute` | POST | `{status, error, response, steps}` |
| `/api/health` | GET | Which services are wired; `?deep=1` actually queries them |

`/api/execute` must complete inside 300 seconds, the Vercel serverless ceiling.

The `steps` array carries every model call: the module that made it, the system
and user prompts, the reply, and the tool result it produced. The worked
examples in `/api/agent_info` are captured from real runs rather than written
by hand, because hand-written examples describe what an agent was meant to do.

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
three of them live football, then Maccabi Tel Aviv against Bayern Munich in the
EuroLeague on the Monday.

Real broadcast coverage stops on Wednesday 2026-06-17 and is deliberately left
unpatched. Asked about the following weekend, the correct answer is that
fixtures beyond Wednesday are unconfirmed. Saying so is a tested behaviour, not
a gap.

### Layout

```
data/source/         real inputs the build reads, never generated
data/external/       fetched by scripts/fetch_external.py, not invented
data/public/         the venue's books, what the agent may see
data/ground_truth/   evaluation answers, never deployed, never readable by the agent
data/runtime/        bundle.json.gz, the offline fixture the test suite runs on
```

`data/ground_truth/` is excluded from deployment by `.vercelignore`. If the
agent can reach it, every evaluation number is worthless. It stays in the
repository because it is this project's own generated dataset and the
evaluation cannot be checked without it.

`data/DATASET.md` documents provenance, the planted incidents and the signal
against noise in full.

### Regenerating

```bash
python scripts/fetch_external.py   # needs internet, run locally
python -m sim.build                # rebuilds data/public
python -m sim.export_runtime       # rebuilds data/runtime/bundle.json.gz
```

Deterministic on seed 20260614. Fetch first, then build, then evaluate; the
weather multipliers change every downstream number when they switch on.

## Setup

Five environment variables, none of them committed. Copy `.env.example` to
`.env` and fill it in; the same five go into the Vercel project.

```
LLMOD_API_KEY
SUPABASE_URL
SUPABASE_SERVICE_KEY      # service_role, not anon: seeding writes tables
PINECONE_API_KEY
PINECONE_INDEX_HOST
```

The LLMod base URL is deliberately not among them. It is not secret and does
not vary between a laptop and production, so it lives as a constant in
`app/llm.py` rather than becoming a sixth thing that can drift.

First-time setup, in order:

```bash
pip install -r requirements.txt -r requirements-dev.txt
psql < db/schema.sql                  # or paste into the Supabase SQL editor
python scripts/seed_supabase.py       # 16 tables, idempotent, --check to verify
python scripts/embed_knowledge.py     # 14 documents into Pinecone
python scripts/check_services.py      # confirms all three are reachable
```

`seed_supabase.py` enforces the anchor at write time: post-anchor sales, counts
and orders never reach the database, so a leak cannot happen by forgetting a
filter in a query.

Running locally:

```bash
flask --app api.index run --debug     # http://localhost:5000
```

With no Supabase credentials set, the tools serve the same rows from
`data/runtime/bundle.json.gz`. That is a test fixture, not a fallback:
`require_supabase()` exists so the request path can refuse to answer a stock
question from a developer's stale local copy, which would look exactly like a
real answer.

## Testing

```bash
pytest                 # 142 unit tests, offline, no credentials needed
python -m eval.run     # nine scenarios end to end, needs credentials
python -m eval.metrics # quantitative measures, offline
```

Unit tests assert on what the deterministic tools return, never on model prose.
An autouse fixture strips every service variable so the suite cannot
accidentally depend on the network or on whatever happens to be seeded.
Scenario pass or fail lives in the eval harness, where non-determinism is
expected and handled.

The eval harness grades against `data/ground_truth/anchor_discrepancies.csv`,
not the `answer_key` column of `scenarios.csv`. That column was written before
the weather multipliers were switched on and its numbers have since moved; it
still says Bombay Sapphire books at 14.22 where the regenerated table says
6.95.

## Known limits

- **A gradual overpour that nobody logs is invisible.** One planted incident,
  an 18% heavy pour across a fortnight, is reached by neither route: it leaves
  no step for the arithmetic to find and no message for the chat search to
  read. The metrics report it rather than excluding it.
- **Forecast error is 42% MAPE on lines moving ten or more units.** Three-day
  demand for a single product at this volume is largely Poisson noise; the
  figure across every product that moved is 52%, and the gap between the two
  is the noise rather than the model.
- **Nothing has been counted since 2026-06-10.** `reconcile` therefore takes
  the physical figure from the caller instead of manufacturing one, and says so
  when it has none. A loss after that date with no human report cannot be
  detected from the data at all, and the tools say so rather than returning a
  clean bill of health.
- **Scenario outcomes vary run to run.** The multi-source forecast and the
  knowledge-explained variance are the two that move; both have been observed
  passing and failing across runs with no code change.

## Services

| Service | Role |
|---|---|
| Supabase | Primary database. The venue ledger the agent reads. |
| Pinecone | Vector store for the 14 operations documents, 1536 dimensions, cosine. |
| LLMod.ai | `gpt-5.4-mini` for reasoning, `text-embedding-3-small` for retrieval. |
| Vercel | Deployment. |

Both databases are reached over their REST APIs rather than their Python SDKs,
to keep serverless cold start low. All three clients share a connection pool
per warm instance: without it, a knowledge search intermittently took 23
seconds against 0.8 with it, because every call was paying its own DNS lookup,
TCP handshake and TLS negotiation.

`select()` pages past PostgREST's 1000-row ceiling. An unbounded select returns
at most a thousand rows with no error of any kind, which silently truncated the
reservations table at January and made every event multiplier after that date
disappear in production while the offline suite stayed green.

## Architecture and design

`docs/specs/2026-08-19-barmate-design.md` covers the architecture, the tool
layer and the evaluation design. The diagram is served at
`/api/model_architecture` and rendered by `scripts/render_architecture.py`.

## Conventions

- No emoji in code, comments, documentation or agent output.
- Comments explain why, not what.
- Hebrew appears throughout the data and in agent responses. Always specify
  `encoding="utf-8"`.
- Prefer the standard library in the request path. `pandas` is not worth its
  cold-start cost in a serverless function.
- Do not invent data. If an external source cannot be reached, say so and leave
  the feature inert.
