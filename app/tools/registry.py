"""Tool dispatch.

Every failure returns as data, never as an exception. An exception ends the
request; an error observation lets the agent notice what went wrong, say so,
and carry on with what it does have.

Nothing here writes. There is no tool that places an order, sends a message or
changes a record, and that is a property of this table rather than a promise
in the prompt -- a prompt can be talked around, a missing tool cannot.
"""
import inspect

from app.tools import catalog, context, human, inventory, knowledge, sales

TOOLS = {
    "resolve_product": catalog.resolve_product,
    "resolve_category": catalog.resolve_category,
    "get_inventory": inventory.get_inventory,
    "reconcile": inventory.reconcile,
    "variance_envelope": inventory.variance_envelope,
    "find_discrepancies": inventory.find_discrepancies,
    "get_sales_history": sales.get_sales_history,
    "forecast_reorder": sales.forecast_reorder,
    "get_context": context.get_context,
    "get_shift_reports": human.get_shift_reports,
    "get_chat": human.get_chat,
    "list_knowledge": knowledge.list_knowledge,
    "get_document": knowledge.get_document,
    "search_knowledge": knowledge.search_knowledge,
}

SCHEMAS = {
    "resolve_product":
        "resolve_product(query: str) -> catalogue matches, or found=false for a "
        "product the venue does not carry. Resolve a name to a product_id before "
        "asking anything else about it; never invent an id.",
    "resolve_category":
        "resolve_category(category: str) -> every product in a category, e.g. "
        "'gin', 'draught_beer', 'wine', 'soft_drink'.",
    "get_inventory":
        "get_inventory(product_id: str) -> book stock at the anchor, the last "
        "physical count and its date, days since that count and whether it is "
        "stale. Book stock believes the delivery invoice, so it can be wrong in "
        "exactly the way a short delivery makes it wrong.",
    "reconcile":
        "reconcile(product_id: str, physical_stock: float = None) -> book stock "
        "against a physical figure you supply, classified per RAG-013, with the "
        "chat and shift-report evidence for that product. The physical figure "
        "comes from a human: a recount, a bartender's claim, a shift report. "
        "Called without one it returns the position and says no gap can be "
        "computed, because nothing has been counted since 2026-06-10.",
    "variance_envelope":
        "variance_envelope(product_id: str) -> how far this product's books and "
        "its counts normally disagree, from its own history. Use it to judge "
        "whether a gap is meaningful; a fixed threshold is noise on a busy line "
        "and a crisis on a quiet one.",
    "find_discrepancies":
        "find_discrepancies() -> every product with a loss somebody reported "
        "since the last count, across the catalogue. Losses nobody mentioned "
        "cannot be found this way and the result says so.",
    "get_sales_history":
        "get_sales_history(product_id: str, weekday: str = None, weeks: int = 8) "
        "-> sample count, mean and max units sold, and demand lost to stockouts. "
        "Units are POS servings, not stock units.",
    "forecast_reorder":
        "forecast_reorder(product_id: str, horizon_days: int = 3, weeks: int = 8) "
        "-> demand over the horizon with the RAG-004 multipliers applied, minus "
        "stock on hand and orders already in flight, rounded to the supplier "
        "minimum. Recommendation only: BarMate cannot place or transmit orders.",
    "get_context":
        "get_context(date_from: str, date_to: str) -> per day: real broadcast "
        "listings with source URLs, confirmed bookings and covers, weather and "
        "any holiday, plus how far each source reaches. Dates past a source are "
        "marked unconfirmed rather than reported as quiet.",
    "get_shift_reports":
        "get_shift_reports(date_from: str = None, date_to: str = None, "
        "product_id: str = None, limit: int = 10) -> closing reports with the "
        "figures extracted per product. A bare number with no word for used or "
        "left comes back ambiguous with both readings; ask, do not pick one.",
    "get_chat":
        "get_chat(date_from: str = None, date_to: str = None, "
        "product_id: str = None, limit: int = 40) -> shift-group messages. "
        "Written in Hebrew; the product filter matches either language.",
    "list_knowledge":
        "list_knowledge() -> the 14 operations documents by id and title. Costs "
        "nothing. Use it when you can name the document you want.",
    "get_document":
        "get_document(doc_id: str) -> one operations document in full, e.g. "
        "'RAG-005'.",
    "search_knowledge":
        "search_knowledge(query: str, top_k: int = 3) -> operations documents "
        "ranked by meaning, with the passage text. Costs one embedding call.",
}


def _public_parameters(fn):
    """Arguments the model may pass.

    Several tools take an underscore-prefixed argument so two calls can share
    one database fetch. That is plumbing: offering it to the model invites a
    crash and tells it nothing it could use.
    """
    return {name for name in inspect.signature(fn).parameters
            if not name.startswith("_")}


def catalogue_for_prompt():
    lines = "\n".join(f"- {SCHEMAS[name]}" for name in TOOLS)
    return (lines + "\n\nEvery tool reads. None of them writes, orders, sends "
            "or changes anything, so BarMate cannot place an order however it "
            "is asked.")


def run_tool(name, args):
    fn = TOOLS.get(name)
    if fn is None:
        return {"ok": False, "tool": name,
                "error": f"unknown tool '{name}'. "
                         f"Available: {', '.join(sorted(TOOLS))}"}
    if not isinstance(args, dict):
        return {"ok": False, "tool": name,
                "error": f"arguments for {name} must be an object, got "
                         f"{type(args).__name__}"}

    allowed = _public_parameters(fn)
    unknown = sorted(set(args) - allowed)
    if unknown:
        return {"ok": False, "tool": name,
                "error": f"unexpected argument(s) {unknown} for {name}. "
                         f"Accepts: {sorted(allowed)}"}
    try:
        return fn(**args)
    except TypeError as e:
        return {"ok": False, "tool": name,
                "error": f"bad arguments for {name}: {e}"}
    except Exception as e:
        return {"ok": False, "tool": name,
                "error": f"{name} failed: {type(e).__name__}: {e}"}
