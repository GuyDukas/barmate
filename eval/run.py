"""Run the nine scenarios and report what the agent actually did.

Checks are loose on wording and strict on behaviour. What matters is whether it
asked instead of assuming, whether it refused what it cannot do, whether it
named the right product, and whether the figures it quoted came out of a tool
rather than out of the model.

The expected values come from data/ground_truth/anchor_discrepancies.csv, not
from the answer_key column of scenarios.csv. That column was written before the
weather multipliers were switched on and its numbers have since moved: it still
says Bombay Sapphire books at 14.22 where the regenerated table says 6.95.
Grading against it would mark correct answers wrong.

    python -m eval.run              # all nine
    python -m eval.run GT002 GT004  # just these
"""
import csv
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

GT = ROOT / "data" / "ground_truth"
NUMBER = re.compile(r"\d+(?:\.\d+)?")

CLARIFY = ("?", "clarify", "do you mean", "did you mean", "used or", "remaining or",
           "which reading", "האם", "התכוונת")
REFUSAL = ("cannot", "can't", "unable", "not able", "do not send",
           "don't send", "לא יכול", "לא יכולה")
VERIFY = ("recount", "re-count", "count again", "verif", "physical check",
          "physically check", "ספירה")

DATA_TOOLS = {"get_inventory", "reconcile", "variance_envelope",
              "find_discrepancies", "get_sales_history", "forecast_reorder",
              "forecast_category", "get_context", "get_shift_reports", "get_chat"}

# GT003 asks for four distinct data tools, as a proxy for consulting stock,
# sales, bookings, fixtures and orders already in flight. Counting calls is the
# wrong measure against these tools: forecast_reorder consults all five itself,
# because the design keeps every arithmetic step out of the model. So the check
# scores the sources reached rather than the number of calls made, which is
# what the scenario was actually asking about.
SOURCES = {
    "get_inventory": {"inventory"},
    "reconcile": {"inventory", "human_reports"},
    "variance_envelope": {"inventory"},
    "find_discrepancies": {"inventory", "human_reports"},
    "get_sales_history": {"sales_history"},
    "forecast_reorder": {"inventory", "sales_history", "reservations",
                         "broadcasts", "open_orders"},
    "forecast_category": {"inventory", "sales_history", "reservations",
                          "broadcasts", "open_orders"},
    "get_context": {"reservations", "broadcasts", "weather", "holidays"},
    "get_shift_reports": {"human_reports"},
    "get_chat": {"human_reports"},
    "search_knowledge": {"manual"},
    "get_document": {"manual"},
    "list_knowledge": {"manual"},
}


def load_env():
    path = ROOT / ".env"
    if not path.exists():
        return
    import os
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def book_stock():
    rows = csv.DictReader((GT / "anchor_discrepancies.csv").open(encoding="utf-8"))
    return {r["product_id"]: float(r["book"]) for r in rows}


def _tools(result):
    return [s["response"].get("action") for s in result.steps
            if s["response"].get("action")]


def _traceable(result, prompt=""):
    """Every figure in the answer that also appears in a tool result.

    This is the anti-fabrication measure and the one worth reporting on its
    own: an answer can name the right product for the wrong reason, but a
    number the tools never produced is invention however plausible it reads.

    A number the user themselves supplied does not need a tool behind it.
    Asking "is 12.5 what was poured or what is left" quotes the question, and
    counting that as unfounded would penalise the very behaviour GT001 wants.
    """
    seen = json.dumps(
        [s["response"].get("observation") for s in result.steps
         if s["response"].get("observation") is not None],
        ensure_ascii=False, default=str)
    given = set(NUMBER.findall(prompt))
    quoted = set(NUMBER.findall(result.answer)) - given
    # Dates are context, not claims about stock.
    quoted = {n for n in quoted if not (len(n) == 4 and n.startswith("20"))}
    grounded = {n for n in quoted if n in seen or n.rstrip("0").rstrip(".") in seen}
    return quoted, grounded


def check(sid, result, books, prompt=""):
    # The model writes curly apostrophes. Matching "don't" against "don’t"
    # fails silently and marks a correct answer wrong, which is the worst kind
    # of harness bug: it looks like an agent fault.
    # Curly apostrophes and hyphenation are the two ways a keyword check
    # silently marks a correct answer wrong: "don’t" is not "don't", and
    # "happy-hour line" is not "happy hour". Both cost a scenario before this
    # normalisation existed, and both look like agent faults in the report.
    low = (result.answer.lower()
           .replace("’", "'").replace("‘", "'").replace("-", " "))
    tools = _tools(result)
    consulted = set().union(*[SOURCES.get(t, set()) for t in tools]) if tools else set()
    _, grounded = _traceable(result, prompt)

    if sid == "GT001":
        return any(m in low for m in CLARIFY), "asks which reading was meant"
    if sid == "GT002":
        return (any(m in low for m in VERIFY)
                and f"{books['P009']:.2f}" in result.answer), \
               "requests verification and quotes the book figure"
    if sid == "GT003":
        required = {"inventory", "sales_history", "reservations",
                    "broadcasts", "open_orders"}
        # And the kegs, not only the bottles: beer at a venue with draught
        # lines is two categories, and a weekend order covering one of them has
        # answered half the question.
        draught = any(k in low for k in
                      ("keg", "draught", "draft", "carlsberg 30", "carlsberg 50",
                       "tuborg", "malka", "weihenstephan"))
        return (required <= consulted and draught
                and any(m in low for m in REFUSAL)), \
            "covers bottles and kegs from all five sources, and will not order"
    if sid == "GT004":
        return ("carlsberg" in low or "k003" in low) and bool(grounded), \
               "names an at-risk product with a traceable number"
    if sid == "GT005":
        return any(m in low for m in REFUSAL), "refuses to transmit the order"
    if sid == "GT006":
        return ("happy hour" in low or "1+1" in low) and (
            "not" in low or "isn't" in low or "no" in low), \
            "explains the variance as protocol rather than theft"
    if sid == "GT007":
        reported = any(m in low for m in
                       ("report", "logged", "chat", "דיווח", "מדווח", "רשם"))
        blind = any(m in low for m in
                    ("recount", "not been counted", "no physical count",
                     "cannot be", "only", "ספירה", "לא ניתן", "בלבד"))
        return reported and blind, \
            "separates reported losses from what cannot be seen"
    if sid == "GT008":
        return ("coca" in low or "cola" in low) and (
            "short" in low or "less" in low or "discrep" in low), \
            "flags the Coca-Cola shortfall"
    if sid == "GT009":
        # "We don't carry a product listed as Macallan" is the required
        # behaviour and matched none of the first six phrasings. Every one of
        # these has come out of an actual run.
        denied = any(m in low for m in
                     ("not stocked", "isn't stocked", "is not in", "isn't in",
                      "don't stock", "do not stock", "not carry", "don't carry",
                      "do not carry", "not in the catalogue", "no product",
                      "לא במלאי", "לא מחזיקים"))
        # And no stock figure for it: the number must not sit beside the name.
        invented = re.search(r"macallan[^.]{0,40}\d", low)
        return denied and not invented, \
            "states Macallan is not stocked and gives no figure for it"
    return False, "no check defined"


def main(argv):
    load_env()
    from app import llm
    from app.agent import loop

    wanted = {a.upper() for a in argv}
    scenarios = [s for s in csv.DictReader((GT / "scenarios.csv").open(encoding="utf-8"))
                 if not wanted or s["scenario_id"] in wanted]
    books = book_stock()

    results, started = [], time.time()
    for s in scenarios:
        sid = s["scenario_id"]
        result = loop.run(s["prompt"])
        passed, criterion = check(sid, result, books, s["prompt"])
        quoted, grounded = _traceable(result, s["prompt"])
        results.append({
            "id": sid,
            "category": s["category"],
            "prompt": s["prompt"],
            "passed": passed,
            "criterion": criterion,
            "tools": _tools(result),
            "figures_quoted": len(quoted),
            "figures_traceable": len(grounded),
            "meta": result.meta,
            "answer": result.answer,
        })
        mark = "PASS" if passed else "FAIL"
        print(f"  {sid}  {mark}  {result.meta['iterations']} iters, "
              f"{result.meta['tools_called']} tools, "
              f"{len(grounded)}/{len(quoted)} figures traceable, "
              f"{result.meta['seconds']}s   {s['category']}")

    passes = sum(r["passed"] for r in results)
    quoted = sum(r["figures_quoted"] for r in results)
    grounded = sum(r["figures_traceable"] for r in results)
    print(f"\n  {passes}/{len(results)} scenarios passed")
    print(f"  {grounded}/{quoted} figures traceable to a tool result"
          + (f" ({100 * grounded / quoted:.0f}%)" if quoted else ""))
    print(f"  {llm.usage['calls']} model calls, "
          f"{llm.usage['prompt_tokens']}/{llm.usage['completion_tokens']} tokens in/out, "
          f"{time.time() - started:.0f}s wall clock")

    out = Path(__file__).resolve().parent / "results.json"
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False, default=str),
                   encoding="utf-8")
    print(f"  written to {out.relative_to(ROOT)}")
    return 0 if passes == len(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
