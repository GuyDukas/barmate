"""
Planted incidents.

These are inputs to the simulation, not annotations on top of it. When an
incident says a bottle was dropped, the simulator actually destroys the stock,
so the ledger, the counts and the reconciliation arithmetic all agree without
anything being hardcoded.

Each incident carries the true magnitude and a record of where, if anywhere, a
human mentioned it. That pairing is what makes RAG-013's reconciliation matrix
detectable: an unlogged loss looks different from a logged one.

Incident types
--------------
miscount        A physical count is recorded wrong by more than ordinary noise.
                Book stock diverges from reality until someone recounts.
breakage        Stock destroyed. No POS revenue.
overpour        Every pour of a product runs heavy for a stretch of shifts.
comp            Stock given away. Zero-value POS line.
keg_rma         A keg swapped out before empty because it was faulty. The
                remaining volume is supplier credit, not sales.
delivery_short  Fewer units arrived than the order says.
walkout         A table left without paying. Stock gone, ticket unpaid.
"""
from datetime import date

# Threshold from RAG-005: a comp above 10 chasers or one full bottle needs
# manager approval.
COMP_THRESHOLD_SERVINGS = 10

INCIDENTS = [
    # ------------------------------------------------------------------
    # The anchor week. These are the incidents the demo scenarios turn on.
    # ------------------------------------------------------------------
    dict(
        incident_id="INC-041", type="unrecorded_removal", date=date(2026, 6, 12),
        product_id="P009", leave_units=0.3,
        detail="Bombay Sapphire cleared off the shelf for a private function "
               "and never written down anywhere. The books still carry the "
               "full case; the shelf is bare.",
        logged_whatsapp=False, logged_shift_report=False,
        scenario="GT002", rag_scenario="B",
    ),
    dict(
        incident_id="INC-050", type="unrecorded_removal", date=date(2026, 6, 13),
        product_id="K003", leave_units=1.15,
        detail="Two Carlsberg 30L kegs pulled from the outside cold room after "
               "a coolant fault and not recorded. The outside line goes into "
               "a heavy football night on barely one keg.",
        logged_whatsapp=True, logged_shift_report=False,
        scenario="GT004", rag_scenario="B",
    ),
    dict(
        incident_id="INC-042", type="overpour", date=date(2026, 6, 5),
        end_date=date(2026, 6, 13), product_id="P014", staff_id="S04",
        magnitude_pct=0.18,
        detail="Tomer pouring Absolut roughly 18 percent heavy across his "
               "shifts. POS counts match, physical depletion does not.",
        logged_whatsapp=False, logged_shift_report=False,
        scenario=None, rag_scenario="C",
    ),
    dict(
        incident_id="INC-043", type="breakage", date=date(2026, 6, 11),
        product_id="P016", magnitude_units=1.0, staff_id="S03",
        detail="Grey Goose bottle dropped at the inside bar. Mentioned in the "
               "shift group but never entered anywhere.",
        logged_whatsapp=True, logged_shift_report=False,
        scenario=None, rag_scenario="B",
    ),
    dict(
        incident_id="INC-044", type="breakage", date=date(2026, 6, 12),
        product_id="P026", magnitude_units=1.0, staff_id="S06",
        detail="Havana Club broken during a rush. Nobody reported it at all.",
        logged_whatsapp=False, logged_shift_report=False,
        scenario=None, rag_scenario="B",
    ),
    dict(
        incident_id="INC-045", type="comp", date=date(2026, 6, 12),
        product_id="P019", magnitude_servings=10, staff_id="S01",
        authorised_by="S01", exceeds_threshold=False,
        detail="Jameson chasers sent to a table by Roei. Shift manager, within "
               "the standard comp allowance.",
        logged_whatsapp=True, logged_shift_report=True,
        scenario=None, rag_scenario="A",
    ),
    dict(
        incident_id="INC-046", type="comp", date=date(2026, 6, 13),
        product_id="P031", magnitude_servings=14, staff_id="S06",
        authorised_by=None, exceeds_threshold=True,
        detail="Fourteen Patron shots comped by a junior bartender. Over the "
               "ten-serving limit and without manager approval.",
        logged_whatsapp=True, logged_shift_report=False,
        scenario=None, rag_scenario="B",
    ),
    dict(
        incident_id="INC-047", type="keg_rma", date=date(2026, 6, 12),
        product_id="K005", remaining_fraction=0.42,
        detail="Weihenstephan keg foaming badly, swapped out with 42 percent "
               "left. That volume is an IBBL credit, not sales.",
        logged_whatsapp=True, logged_shift_report=True,
        scenario=None, rag_scenario="C",
    ),
    dict(
        incident_id="INC-048", type="delivery_short", date=date(2026, 6, 10),
        product_id="P043", ordered_units=240, received_units=216,
        detail="CBC invoice says ten cases of Coca-Cola, nine arrived.",
        logged_whatsapp=True, logged_shift_report=False,
        scenario=None, rag_scenario="B",
    ),
    dict(
        incident_id="INC-049", type="walkout", date=date(2026, 6, 13),
        product_id="P021", magnitude_servings=6,
        detail="Table left without settling. Six Johnnie Walker shots poured "
               "against an open ticket.",
        logged_whatsapp=True, logged_shift_report=False,
        scenario=None, rag_scenario="B",
    ),

    # ------------------------------------------------------------------
    # Background. Without these the final fortnight would be conspicuously
    # the only interesting stretch in the dataset, which would let an agent
    # score well by always looking in the same place.
    # ------------------------------------------------------------------
    dict(incident_id="INC-001", type="breakage", date=date(2025, 9, 19),
         product_id="P002", magnitude_units=1.0, staff_id="S04",
         detail="Heineken case dropped in the cold room.",
         logged_whatsapp=True, logged_shift_report=True, scenario=None, rag_scenario="A"),
    dict(incident_id="INC-002", type="miscount", date=date(2025, 10, 8),
         product_id="P038", magnitude_units=-4.0,
         detail="House red undercounted during a rushed close.",
         logged_whatsapp=False, logged_shift_report=False, scenario=None, rag_scenario="B"),
    dict(incident_id="INC-003", type="comp", date=date(2025, 10, 30),
         product_id="P011", magnitude_servings=12, staff_id="S02",
         authorised_by="S02", exceeds_threshold=True,
         detail="Hendrick's poured for a private booking, approved by Yael.",
         logged_whatsapp=True, logged_shift_report=True, scenario=None, rag_scenario="A"),
    dict(incident_id="INC-004", type="overpour", date=date(2025, 11, 14),
         end_date=date(2025, 11, 27), product_id="P020", staff_id="S06",
         magnitude_pct=0.12,
         detail="Muriel running heavy on Jack Daniel's during her first weeks.",
         logged_whatsapp=False, logged_shift_report=False, scenario=None, rag_scenario="C"),
    dict(incident_id="INC-005", type="keg_rma", date=date(2025, 12, 5),
         product_id="K002", remaining_fraction=0.30,
         detail="Tuborg keg returned flat.",
         logged_whatsapp=True, logged_shift_report=True, scenario=None, rag_scenario="C"),
    dict(incident_id="INC-006", type="breakage", date=date(2025, 12, 26),
         product_id="P041", magnitude_units=2.0, staff_id="S05",
         detail="Two Prosecco bottles broken on New Year prep.",
         logged_whatsapp=True, logged_shift_report=False, scenario=None, rag_scenario="B"),
    dict(incident_id="INC-007", type="delivery_short", date=date(2026, 1, 14),
         product_id="P048", ordered_units=144, received_units=120,
         detail="Red Bull delivery one flat short.",
         logged_whatsapp=True, logged_shift_report=False, scenario=None, rag_scenario="B"),
    dict(incident_id="INC-008", type="miscount", date=date(2026, 2, 11),
         product_id="P014", magnitude_units=+3.0,
         detail="Absolut overcounted, backups double counted.",
         logged_whatsapp=False, logged_shift_report=False, scenario=None, rag_scenario="D"),
    dict(incident_id="INC-009", type="walkout", date=date(2026, 3, 7),
         product_id="P022", magnitude_servings=4,
         detail="Unpaid ticket, four Chivas.",
         logged_whatsapp=True, logged_shift_report=True, scenario=None, rag_scenario="B"),
    dict(incident_id="INC-010", type="breakage", date=date(2026, 4, 18),
         product_id="P034", magnitude_units=1.0, staff_id="S03",
         detail="Campari knocked off the speed rack.",
         logged_whatsapp=True, logged_shift_report=True, scenario=None, rag_scenario="A"),
    dict(incident_id="INC-011", type="overpour", date=date(2026, 5, 1),
         end_date=date(2026, 5, 9), product_id="P025", staff_id="S05",
         magnitude_pct=0.14,
         detail="Bacardi running heavy through the Mojito season.",
         logged_whatsapp=False, logged_shift_report=False, scenario=None, rag_scenario="C"),
    dict(incident_id="INC-012", type="miscount", date=date(2026, 5, 20),
         product_id="P046", magnitude_units=-5.0,
         detail="Tonic undercounted, a case left in the corridor.",
         logged_whatsapp=False, logged_shift_report=False, scenario=None, rag_scenario="B"),
]


def by_date(d):
    return [i for i in INCIDENTS if i["date"] == d]


def active_overpours(d):
    return [i for i in INCIDENTS
            if i["type"] == "overpour" and i["date"] <= d <= i["end_date"]]


def validate(product_ids, staff_ids):
    """Fail loudly rather than silently simulating a product that does not exist."""
    problems = []
    seen = set()
    for i in INCIDENTS:
        if i["incident_id"] in seen:
            problems.append(f"duplicate id {i['incident_id']}")
        seen.add(i["incident_id"])
        if i["product_id"] not in product_ids:
            problems.append(f"{i['incident_id']}: unknown product {i['product_id']}")
        sid = i.get("staff_id")
        if sid and sid not in staff_ids:
            problems.append(f"{i['incident_id']}: unknown staff {sid}")
        if i["type"] == "overpour" and i["end_date"] < i["date"]:
            problems.append(f"{i['incident_id']}: overpour ends before it starts")
    return problems
