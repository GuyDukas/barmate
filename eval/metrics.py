"""Quantitative measures against held-out ground truth.

Three things worth a number, none of which the scenario pass rate captures.
The scenarios test whether the agent behaves; these test whether the tools it
leans on are right, over the whole dataset rather than nine questions.

    python -m eval.metrics

Nothing here calls the model, so it costs nothing and runs offline.
"""
import csv
import datetime
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

GT = ROOT / "data" / "ground_truth"


def rows(name):
    return list(csv.DictReader((GT / name).open(encoding="utf-8")))


def pairs(field):
    """'P004=3.0 | P039=2.0' -> {'P004': 3.0, ...}"""
    out = {}
    for part in field.split("|"):
        if "=" in part:
            k, _, v = part.strip().partition("=")
            out[k] = float(v)
    return out


# ------------------------------------------------ 1. report parsing accuracy

def report_parsing():
    """How much of what the staff wrote the parser actually recovers.

    Graded on all 286 closing reports against shift_report_truth.csv: which
    products each report names, the units used and the units left, and whether
    a report that needs clarification is flagged as needing it.
    """
    from app.tools import human

    truth = {r["report_id"]: r for r in rows("shift_report_truth.csv")}
    reports = human.get_shift_reports(limit=None)["reports"]

    tp = fp = fn = 0
    used_errors, left_errors = [], []
    flag_right = flag_wrong = 0

    for rep in reports:
        t = truth[rep["report_id"]]
        expected = {p.strip() for p in t["products_reported"].split("|") if p.strip()}
        got = {c["product_id"] for c in rep["claims"]}
        tp += len(expected & got)
        fp += len(got - expected)
        fn += len(expected - got)

        ambiguous = {p.strip() for p in t["ambiguous_products"].split("|") if p.strip()}
        needs_clarifying = (t["requires_clarification"] == "True")
        flagged = any(c["ambiguous"] for c in rep["claims"])
        flag_right += (flagged == needs_clarifying)
        flag_wrong += (flagged != needs_clarifying)

        used, left = pairs(t["true_units_used"]), pairs(t["true_closing_stock"])
        for c in rep["claims"]:
            if c["product_id"] in ambiguous:
                continue
            if c["units_used"] is not None and c["product_id"] in used:
                used_errors.append(abs(c["units_used"] - used[c["product_id"]]))
            if c["units_remaining"] is not None and c["product_id"] in left:
                left_errors.append(abs(c["units_remaining"] - left[c["product_id"]]))

    precision = tp / (tp + fp) if tp + fp else 0
    recall = tp / (tp + fn) if tp + fn else 0
    print("\n1. Report parsing, 286 closing reports")
    print(f"   product identification   precision {precision:.3f}  recall {recall:.3f}"
          f"  ({tp} found, {fp} spurious, {fn} missed)")
    print(f"   clarification flag       {flag_right}/{flag_right + flag_wrong} correct")
    print(f"   units used               mean abs error {sum(used_errors)/len(used_errors):.3f}"
          f" over {len(used_errors)} figures")
    print(f"   units remaining          mean abs error {sum(left_errors)/len(left_errors):.3f}"
          f" over {len(left_errors)} figures")
    print("   (staff round and estimate, so a small error is the report's, not the parser's)")


# ------------------------------------------------- 2. discrepancy detection

def discrepancy_detection():
    """Does the derived envelope find the planted incidents, and only those?

    The label is whether an incident was actually planted on that product
    between the last count and the anchor, which is the thing a manager cares
    about. It is not the gap_is_material column of anchor_discrepancies.csv:
    that column applies a flat half-unit threshold, which after the demand
    rebuild sits inside the ordinary variation of every busy line.
    """
    from app.tools import inventory

    truth = {r["product_id"]: float(r["physical_stock"])
             for r in rows("anchor_discrepancies.csv")}
    last_count = "2026-06-10"
    anchor = "2026-06-14"

    planted = set()
    for i in rows("incidents.csv"):
        date, end = i["date"], i["end_date"] or i["date"]
        if i["product_id"] and last_count <= end and date <= anchor:
            planted.add(i["product_id"])

    tp = fp = fn = tn = 0
    missed, spurious = [], []
    for pid, physical in truth.items():
        r = inventory.reconcile(pid, physical_stock=physical)
        if not r["ok"]:
            continue
        flagged = r["gap_is_material"]
        real = pid in planted
        if flagged and real:
            tp += 1
        elif flagged and not real:
            fp += 1
            spurious.append(pid)
        elif not flagged and real:
            fn += 1
            missed.append(f"{pid} ({r['name']}, gap {r['gap_units']} vs envelope "
                          f"{r['expected_variance']})")
        else:
            tn += 1

    precision = tp / (tp + fp) if tp + fp else 0
    recall = tp / (tp + fn) if tp + fn else 0
    print(f"\n2. Discrepancy detection, {len(truth)} products against "
          f"{len(planted)} planted incidents")
    print(f"   precision {precision:.3f}  recall {recall:.3f}"
          f"  (tp {tp}, fp {fp}, fn {fn}, tn {tn})")
    if spurious:
        print(f"   false alarms: {', '.join(spurious)}")
    for m in missed:
        print(f"   missed: {m}")

    # Arithmetic is only half the system. A loss somebody wrote down is found
    # by reading the chat, not by the envelope, and the agent has both. This is
    # the number that describes what it can actually surface.
    reported = {r["product_id"] for r in inventory.find_discrepancies()["logged"]}
    combined = {pid for pid in truth
                if inventory.reconcile(pid, physical_stock=truth[pid])["gap_is_material"]
                or pid in reported}
    c_tp = len(combined & planted)
    c_fp = len(combined - planted)
    print(f"   with reported evidence:  precision {c_tp / len(combined):.3f}  "
          f"recall {c_tp / len(planted):.3f}  (tp {c_tp}, fp {c_fp})")
    # Keyed on the incident inside the window, not merely on the product:
    # Absolut also carries a miscount from February, and reporting that one
    # would describe the wrong event entirely.
    detail = {i["product_id"]: i for i in rows("incidents.csv")
              if last_count <= (i["end_date"] or i["date"]) and i["date"] <= anchor}
    for pid in sorted(planted - combined):
        i = detail.get(pid, {})
        print(f"   still invisible: {pid} -- {i.get('type', 'unknown')} "
              f"({i.get('date', '?')}), logged in chat: "
              f"{i.get('logged_whatsapp', '?')}")
    print("   arithmetic catches the silent step losses; the chat catches the "
          "logged ones.\n   What neither reaches is a gradual overpour nobody "
          "wrote down: it leaves no\n   step to find and no message to read.")

    # The specificity that matters most: a happy-hour line must not be called
    # theft, because that accuses staff of following the protocol.
    protocol = [pid for pid in truth
                if inventory._is_happy_hour_line(inventory.db.products_by_id()[pid])
                and pid not in planted]
    wrong = [pid for pid in protocol
             if inventory.reconcile(pid, physical_stock=truth[pid])["gap_is_material"]]
    print(f"   happy-hour lines with no incident: {len(protocol)}, "
          f"{len(wrong)} wrongly flagged as shrinkage")


# ------------------------------------------------------- 3. forecast error

def forecast_error(horizon=3):
    """Predicted demand against what the venue actually got through.

    Actual depletion comes from the held-out post-anchor tail of
    true_inventory.csv, which the agent never sees:

        used = closing stock the day before the horizon
             - closing stock on its last day
             + everything received in between
    """
    from app.tools import sales

    stock = defaultdict(dict)
    for r in rows("true_inventory.csv"):
        stock[r["product_id"]][r["date"]] = float(r["true_closing_stock"])

    received = defaultdict(float)
    for r in rows("deliveries_received.csv"):
        received[(r["product_id"], r["date"])] += float(r["units_received"])

    anchor = datetime.date.fromisoformat("2026-06-14")
    start = (anchor - datetime.timedelta(days=1)).isoformat()
    end = (anchor + datetime.timedelta(days=horizon - 1)).isoformat()
    window = [(anchor + datetime.timedelta(days=i)).isoformat() for i in range(horizon)]

    errors, busy = [], []
    for pid in sorted(stock):
        if start not in stock[pid] or end not in stock[pid]:
            continue
        delivered = sum(received.get((pid, d), 0.0) for d in window)
        actual = stock[pid][start] - stock[pid][end] + delivered
        if actual < 1.0:
            continue  # a line that barely moved makes percentage error meaningless
        r = sales.forecast_reorder(pid, horizon_days=horizon)
        if not r["ok"]:
            continue
        error = abs(r["gross_need"] - actual) / actual
        errors.append(error)
        # On a line selling half a bottle over three days, one extra pour is a
        # 40% error and means nothing. The high-volume subset is where the
        # forecast is being asked a question it can answer.
        if actual >= 10:
            busy.append(error)

    def report(label, values):
        if not values:
            return
        values = sorted(values)
        print(f"   {label:<28} MAPE {100 * sum(values) / len(values):5.1f}%   "
              f"median {100 * values[len(values) // 2]:5.1f}%   n={len(values)}")

    print(f"\n3. Forecast error over {horizon} days, against held-out true stock")
    report("every product that moved", errors)
    report("lines moving 10+ units", busy)
    print("   (the true stock tail is ground truth the agent never reads. Per-product "
          "demand\n    over three days is mostly Poisson noise on a quiet line, which "
          "is why the\n    busy subset is the honest measure of the forecast itself)")


def main():
    print("BarMate quantitative metrics, anchor 2026-06-14")
    report_parsing()
    discrepancy_detection()
    forecast_error()
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
