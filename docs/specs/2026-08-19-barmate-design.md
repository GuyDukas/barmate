# BarMate design specification

**Date:** 2026-08-19
**Team:** Guy, Reut, Yuval (group 326692662)
**Course:** Modern AI Agents, Technion, Spring 2026
**Deadline:** 2026-08-23 (extension anticipated but not assumed)

---

## 1. What BarMate is

An autonomous operations agent for a bar. A manager or bartender asks it a
question in plain language, in Hebrew or English, and it decides for itself
which of the venue's records to consult, reconciles what the books say against
what the staff reported, and returns a recommendation with its reasoning
visible.

It is pull-based. It never pushes notifications and never takes an action in the
outside world. It reads, reasons and recommends.

## 2. What makes it an agent rather than a pipeline

The course test has three parts.

**Perceive.** Nine data sources, three of them unstructured: free-text closing
reports in mixed Hebrew and English, Hebrew shift-group chat, and a 14-document
operations manual retrieved by similarity.

**Reason.** The agent is given no fixed sequence. "Are we ready for tonight?"
names no table, no product and no date range. Which sources to open, in what
order, and when it has enough to answer are all decisions the model makes and
revises based on what each tool returns.

**Act autonomously.** It chooses tool calls without confirmation, decides when
a question cannot be answered without clarification and asks, and refuses
requests outside its authority.

The single most load-bearing design decision is a negative one: **there is no
intent router.** Classifying a request up front with fixed rules and dispatching
down a branch is precisely what makes a system a pipeline. Scope refusal,
clarification and multi-source planning are all decisions the Reasoner makes in
the loop, on the evidence in front of it.

## 3. Architecture

Single agent. ReAct loop with a Reflect gate. No sub-agents, no
plan-and-execute.

| Module | LLM call | Responsibility |
|---|---|---|
| `Reasoner` | 1 to 5 iterations | Emits a Thought plus either an Action (tool name and arguments) or a draft answer. Tool observations feed the next iteration |
| `KnowledgeRetriever` | 1 embedding call | Embeds the query, ranks the 14 operations documents by cosine similarity, returns the top matches |
| `Reflector` | 1 | Quality gate before the answer is released |
| `Reviser` | 0 or 1 | Repairs the draft against the critique. Runs only when the gate fails |

`ToolExecutor` appears on the architecture diagram but makes no model call and
therefore produces no entry in `steps`, which the brief defines as the list of
LLM calls.

Typical cost: three to four model calls per request.

### The Reflect gate

The Reflector checks four things, and each maps to a failure the dataset can
actually produce:

1. **Traceability.** Every number in the draft appears in a tool result. Catches
   invented figures.
2. **Catalogue discipline.** Every product named exists in the catalogue.
   Catches confidently answering about Macallan, which the venue does not stock.
3. **Ambiguity honesty.** If a source figure was ambiguous, the draft asks
   rather than picks. Catches guessing on "Bacardi Carta Blanca 12.5".
4. **Authority.** No claim to have placed, sent or queued anything.

On failure the critique goes to the Reviser. One repair attempt, then the answer
ships with the unresolved concern stated rather than looping.

### Why the module names matter

The brief requires the same names across the architecture PNG, the `steps`
trace and `/api/agent_info`. These four are the contract. Renaming one means
renaming it in three places.

## 4. Data layer

Flat files loaded into memory at cold start. No database round trips.

The build emits `data/runtime/bundle.json.gz`, 251 KB, containing pre-grouped
indices. Measured cold start is 42 ms. Every request after that is
memory-speed, which protects both the 300-second ceiling and the $13 budget.

**The bundle contains nothing dated after the anchor.** This is enforced at
export time, not by a filter at read time. A visibility rule that depends on
someone remembering to filter is a rule that eventually leaks.

Reservations and the rota are the deliberate exception: they extend ten days
past the anchor, because a booking for next Friday is genuinely known today.
Sales for that Friday are not.

### Deviation from the brief

The brief names Supabase as primary database and Pinecone as vector store. We
use neither, and the write-up should say so plainly rather than hope nobody
notices.

The ledger is 2 MB and entirely static behind a frozen anchor. A database would
add network latency and failure modes to serve data that never changes. The
knowledge corpus is 14 documents; a vector index over 14 vectors is a dictionary
with extra steps. Document embeddings are precomputed at build time and ranked
in memory, which still exercises the embedding model on the query and still
demonstrates retrieval, at one API call instead of a hosted index.

This is a defensible engineering judgement, not a shortcut, and it should be
argued rather than concealed.

## 5. Tool layer

Nine tools. All deterministic. All arithmetic lives here, never in the model.

| Tool | Returns |
|---|---|
| `resolve_product` | Catalogue match, or `unknown` rather than a guess |
| `get_inventory` | Book stock, last count date, staleness in days |
| `get_sales_history` | Aggregates by product, window, weekday |
| `get_open_orders` | In-flight orders, expected versus actual, delay history |
| `get_context` | Real broadcasts, weather, holidays, reservations for a date range |
| `get_shift_reports` | Reports touching a product or date, with parsed claims |
| `get_chat` | Shift-group messages in a window |
| `search_knowledge` | Ranked operations documents |
| `forecast_reorder` | Baseline times multipliers, safety stock, case rounding |
| `reconcile` | Book versus expected depletion, classified against RAG-013 |

`reconcile` is what makes conflict detection possible. `forecast_reorder` is
what keeps the model out of the arithmetic. `resolve_product` returning
`unknown` is the anti-fabrication guard, and it is a feature: refusing to invent
a stock figure for a product the venue does not carry is the correct behaviour,
not a limitation.

## 6. Endpoints

Four, names exact.

- `GET /api/team_info` — static
- `GET /api/agent_info` — description, purpose, prompt template, worked
  examples with full `steps` traces
- `GET /api/model_architecture` — PNG, module names matching the trace
- `POST /api/execute` — `{status, error, response, steps}`

Plus a GUI at root: textarea, Run Agent button, final response, full expandable
step trace. No authentication.

## 7. Evaluation

Nine scenarios in `data/ground_truth/scenarios.csv`, each with a
machine-checkable `answer_key` and `passes_if` condition. GT001 to GT005 are the
original course scenarios with figures corrected to what the data contains.
GT006 to GT009 add knowledge-explained variance, silent loss detection, invoice
discrepancy and unknown-entity refusal.

Beyond pass or fail, three quantitative measures:

- **Report parsing accuracy** against `shift_report_truth.csv`, 286 reports
- **Discrepancy detection** recall and precision against
  `anchor_discrepancies.csv`, where ground truth marks which of the 61 products
  carry a material gap
- **Forecast error** against the held-out post-anchor tail

The third is why the simulation runs six days past the anchor.

### The discriminator

Four products show gaps of 1.1 to 1.6 units at the anchor that are not
shrinkage. They are happy-hour lines, and RAG-005 specifies double physical
depletion between 18:00 and 20:30. An agent that skips retrieval reports them as
theft; one that retrieves RAG-005 explains them.

This was not designed in. It fell out of modelling the protocol honestly, and it
separates grounded reasoning from pattern matching better than anything written
on purpose. GT006 tests it.

## 8. Budget

$13 total across the group. At three to four calls per request on
`gpt-5.4-mini`, with the context held down by pre-grouped tool returns rather
than raw table dumps, the constraint is comfortable. The realistic risk is not
per-request cost but a runaway loop, so the Reasoner is hard-capped at five
iterations and the Reviser at one attempt.

## 9. Honest limitations

- Weather and holiday multipliers stay inert until `scripts/fetch_external.py`
  runs from a machine with general internet access. Nothing is estimated in
  their place.
- Real broadcast coverage ends 2026-06-17. Asked about the following weekend,
  the correct answer says fixtures are unconfirmed.
- The 1.5x football multiplier is a policy rule from RAG-004, not a coefficient
  estimated from history. The agent should say which it is.
- The venue's own books are simulated. Every external signal is real and cited.
  This distinction belongs in the write-up, stated plainly.
