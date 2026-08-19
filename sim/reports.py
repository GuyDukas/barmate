"""
The human layer.

Bartenders do not file structured data. They type a paragraph at 03:00 on a
phone, in a mix of Hebrew and English, and they leave things out. This module
turns what actually happened on a given night into the kind of text a real
closing report contains, and records ground truth for every one.

Three quality tiers, following the pattern already present in the original
course data:

  good       every product named with both a consumed figure and a remaining
             figure. Parseable without guessing.
  messy      lowercase, no punctuation, hedging ("think", "בערך"). All the
             numbers are there but the shape is loose.
  ambiguous  at least one product carries a bare number with no verb attached
             -- "Carlsberg 6" -- so it is impossible to tell whether six were
             used or six remain. THIS IS THE POINT. An agent that guesses is
             wrong even when it guesses correctly, because the information to
             decide is genuinely absent.

WhatsApp traffic is generated the same way: from real events, using phrasing
patterns taken from the original Hebrew logs supplied with the course.
"""
import datetime
import random

from . import config as C
from . import incidents as INC
from . import master_data as M

# ------------------------------------------------------------------ templates
HE_OPENERS = [
    "סיימתי משמרת, כמה עדכוני מלאי:",
    "עדכון סוף משמרת. מה שראיתי לפני הסגירה:",
    "ערב עמוס. הנה מה שיצא הלילה:",
    "רשימת מלאי לפני שאני הולך:",
]
EN_OPENERS = [
    "Shift finished. A few stock updates the next team should know:",
    "End-of-shift update. These are the main alcohol stock points from tonight:",
    "Busy service tonight. Here's what I noticed before closing:",
    "Stock notes before I leave:",
]
HE_CLOSERS = [
    "כדאי לבדוק שוב לפני המשמרת הבאה.",
    "שאר הדברים נראו רגילים.",
    "זהו מה שהיה בולט הלילה.",
]
EN_CLOSERS = [
    "Everything else looked generally normal at close.",
    "No other major alcohol issues stood out during closing.",
    "Those were the main stock changes I noticed.",
]
MESSY_CLOSERS_HE = ["תבדקו את זה מחר", "מישהו שיוודא, אני לא בטוח ב100%"]
MESSY_CLOSERS_EN = ["someone double check those before service",
                    "might have missed something so check"]


def _fmt(v):
    return int(v) if abs(v - round(v)) < 0.05 else round(v * 2) / 2


def _he_good(name, used, left):
    return random.choice([
        f"{name} - ירדו בערך {_fmt(used)}, נשארו בערך {_fmt(left)}.",
        f"מ{name} סיימנו בערך {_fmt(used)} יחידות, ספרתי בערך {_fmt(left)} במלאי.",
        f"{name} זז הרבה. בערך {_fmt(used)} במשמרת, נשארו בערך {_fmt(left)}.",
    ])


def _en_good(name, used, left):
    return random.choice([
        f"For {name}, we finished around {_fmt(used)} unit(s) and I counted about {_fmt(left)} still in stock.",
        f"We went through about {_fmt(used)} of {name} tonight and I counted around {_fmt(left)} left.",
        f"{name} moved quite a lot. About {_fmt(used)} were used during the shift, with roughly {_fmt(left)} remaining.",
    ])


def _he_messy(name, used, left):
    return random.choice([
        f"{name} ירד מהר, {_fmt(used)} ונשארו {_fmt(left)} נראה לי",
        f"{name} השתמשנו בערך {_fmt(used)}, חושב שנשארו {_fmt(left)}",
    ])


def _en_messy(name, used, left):
    return random.choice([
        f"{name} went fast, {_fmt(used)} used and {_fmt(left)} left i think",
        f"{name} we went through like {_fmt(used)}, think theres {_fmt(left)} left",
    ])


def _ambiguous(name, value, hebrew):
    """A bare number. Neither 'used' nor 'left' is stated anywhere."""
    return f"{name} {_fmt(value)}" if not hebrew else f"{name} {_fmt(value)}"


class ReportWriter:
    def __init__(self, sim, seed=C.RANDOM_SEED + 1):
        self.sim = sim
        self.rng = random.Random(seed)
        random.seed(seed)
        self.staff_by_id = {s["staff_id"]: s for s in M.STAFF}

    # -------------------------------------------------------------- shift reports
    def build(self):
        usage_by_date = {}
        for u in self.sim.night_usage:
            usage_by_date.setdefault(u["date"], []).append(u)
        sched_by_date = {}
        for s in self.sim.schedule:
            sched_by_date.setdefault(s["date"], []).append(s)

        reports, truth = [], []
        n = 0
        for d in sorted(usage_by_date):
            rows = usage_by_date[d]
            # Bartenders report on alcohol that actually moved, not soft drinks.
            notable = [r for r in rows
                       if r["category"] not in ("soft_drink", "juice",
                                                "cocktail_ingredient")
                       and r["units_used"] >= 0.8]
            if not notable:
                continue
            notable.sort(key=lambda r: -r["units_used"])

            # Taking the top-k by volume fills every report with bottled beer,
            # because beer moves in the largest unit counts. Real closing
            # reports range across the rack. Weighting by the square root of
            # volume keeps busy lines likely without letting them monopolise.
            weights = [max(r["units_used"], 0.1) ** 0.5 for r in notable]
            k = min(len(notable), self.rng.choice([3, 4, 4, 5]))
            picked = self._weighted_sample(notable, weights, k)

            # Anything a human said they would write down actually gets written
            # down, so logged and unlogged losses stay distinguishable.
            must = {i["product_id"] for i in INC.by_date(
                        datetime.date.fromisoformat(d))
                    if i.get("logged_shift_report")}
            for r in notable:
                if r["product_id"] in must and r not in picked:
                    picked.insert(0, r)
            self.rng.shuffle(picked)

            authors = [s for s in sched_by_date.get(d, [])
                       if s["role"] in ("bartender", "shift_manager")]
            if not authors:
                continue
            author = self.rng.choice(authors)
            hebrew = self.rng.random() < 0.55

            roll = self.rng.random()
            quality = "good" if roll < 0.55 else ("messy" if roll < 0.88 else "ambiguous")
            # The night before the anchor always carries an ambiguous line, so
            # the clarification scenario is reproducible rather than lucky.
            if d == "2026-06-13":
                quality = "ambiguous"

            n += 1
            rid = f"SR{n:06d}"
            text, gt_items = self._compose(picked, quality, hebrew)
            reports.append({
                "report_id": rid, "date": d,
                # Filed after close, which is the small hours of the NEXT day.
                "submitted_at": (datetime.date.fromisoformat(d)
                                 + datetime.timedelta(days=1)).isoformat()
                                + f"T03:{self.rng.randint(5, 55):02d}:00",
                "staff_id": author["staff_id"],
                "author_name": author["name_he"] if hebrew else author["name_en"],
                "language": "he" if hebrew else "en",
                "raw_report": text,
            })
            truth.append({
                "report_id": rid, "date": d,
                "quality_ground_truth": quality,
                "requires_clarification": any(i["ambiguous"] for i in gt_items),
                "language": "he" if hebrew else "en",
                "products_reported": " | ".join(i["product_id"] for i in gt_items),
                "product_names": " | ".join(i["name"] for i in gt_items),
                "ambiguous_products": " | ".join(
                    i["product_id"] for i in gt_items if i["ambiguous"]),
                "true_units_used": " | ".join(
                    f"{i['product_id']}={i['used']}" for i in gt_items),
                "true_closing_stock": " | ".join(
                    f"{i['product_id']}={i['left']}" for i in gt_items),
            })
        return reports, truth

    def _weighted_sample(self, items, weights, k):
        pool = list(zip(items, weights))
        out = []
        for _ in range(min(k, len(pool))):
            total = sum(w for _, w in pool)
            if total <= 0:
                break
            roll, acc = self.rng.random() * total, 0.0
            for idx, (item, w) in enumerate(pool):
                acc += w
                if roll <= acc:
                    out.append(item)
                    pool.pop(idx)
                    break
        return out

    def _compose(self, picked, quality, hebrew):
        opener = self.rng.choice(HE_OPENERS if hebrew else EN_OPENERS)
        parts, gt = [], []
        amb_idx = self.rng.randrange(len(picked)) if quality == "ambiguous" else -1

        for i, r in enumerate(picked):
            name = r["name_he"] if hebrew and r["name_he"] else r["product_name"]
            used = round(r["units_used"], 1)
            left = round(r["closing_stock"], 1)
            if i == amb_idx:
                # The bare number is deliberately the REMAINING figure. An agent
                # that assumes "used" gets it wrong, and vice versa. Neither can
                # be inferred from the sentence.
                parts.append(_ambiguous(name, left, hebrew))
                gt.append(dict(product_id=r["product_id"], name=r["product_name"],
                               used=used, left=left, ambiguous=True,
                               bare_value=left, bare_value_means="remaining"))
            else:
                if quality == "good":
                    parts.append(_he_good(name, used, left) if hebrew
                                 else _en_good(name, used, left))
                else:
                    parts.append(_he_messy(name, used, left) if hebrew
                                 else _en_messy(name, used, left))
                gt.append(dict(product_id=r["product_id"], name=r["product_name"],
                               used=used, left=left, ambiguous=False,
                               bare_value=None, bare_value_means=None))

        if quality == "good":
            closer = self.rng.choice(HE_CLOSERS if hebrew else EN_CLOSERS)
            body = " ".join(parts)
        else:
            closer = self.rng.choice(MESSY_CLOSERS_HE if hebrew else MESSY_CLOSERS_EN)
            body = " ".join(p.rstrip(".") for p in parts)
            if not hebrew:
                body = body.lower()
        return f"{opener} {body} {closer}", gt

    # -------------------------------------------------------------- whatsapp
    # Phrasing patterns lifted from the real Hebrew shift-group logs supplied
    # with the course. Slang maps to the definitions in RAG-002 and RAG-008.
    CHATTER_HE = [
        "צריך קרח דחוף מי מביא", "מביא שניה", "אני בחביות רגע תכף עולה עם ארגז",
        "יש לי ספייר בפנים תבואי לקחת", "הבר בחוץ פצצה עכשיו",
        "מישהו שיביא כוסות מהמדיח", "גארניש נקי בחוץ, צריך לימונים",
        "הקרלסברג דופק הערב", "מוריד חבית חדשה", "בלי אבנים במכונת קרח",
    ]

    def whatsapp(self):
        msgs = []
        by_date = {}
        for s in self.sim.schedule:
            by_date.setdefault(s["date"], []).append(s)

        # Only the fortnight leading into the anchor. A year of chat would be
        # noise the agent has no reason to read.
        anchor_day = C.ANCHOR.date().isoformat()
        window = sorted(d for d in by_date if "2026-06-01" <= d <= anchor_day)

        incident_msgs = self._incident_messages()
        for d in window:
            staff = by_date[d]
            # On the anchor day the clock stops at 18:00. Chat from later that
            # night is the future and must not leak into the agent's view.
            last_hour = C.ANCHOR.hour - 1 if d == anchor_day else 23
            if last_hour < 20:
                continue
            for _ in range(self.rng.randint(3, 7)):
                s = self.rng.choice(staff)
                hh = self.rng.randint(20, last_hour)
                msgs.append({
                    "timestamp": f"{d}T{hh:02d}:{self.rng.randint(0,59):02d}:00",
                    "sender_id": s["staff_id"],
                    "sender": f'{s["name_he"]} {self._role_he(s["role"])}',
                    "message": self.rng.choice(self.CHATTER_HE),
                    "incident_id": "",
                })
            msgs.extend(incident_msgs.get(d, []))

        msgs.sort(key=lambda m: m["timestamp"])
        return msgs

    @staticmethod
    def _role_he(role):
        return {"bartender": "ברמן", "shift_manager": 'אחמ"ש',
                "barback": "ברבק"}.get(role, "")

    def _incident_messages(self):
        """One message per incident that a human actually mentioned in the group."""
        out = {}
        for i in INC.INCIDENTS:
            if not i.get("logged_whatsapp"):
                continue
            d = i["date"].isoformat()
            if not ("2026-06-01" <= d <= "2026-06-14"):
                continue
            p = self.sim.by_id[i["product_id"]]
            nm = p["name_he"] or p["name"]
            sid = i.get("staff_id") or "S01"
            s = self.staff_by_id[sid]
            text = {
                "breakage": f"נפל לי בקבוק {nm} כוסעמק, תרשמו בפחת",
                "comp": f"הוצאתי צ'ייסרים של {nm} לשולחן, תרשמו",
                "keg_rma": f"חבית {nm} מקציפה, מחליף אותה, אל תמזגו",
                "delivery_short": f"קיבלנו פחות ארגזים של {nm} ממה שכתוב בתעודה",
                "walkout": f"שולחן ברח בלי לשלם, היו עליו צ'ייסרים של {nm}",
                "unrecorded_removal": f"לקחו {nm} מהמדף לאירוע, לא יודע מי רשם",
            }.get(i["type"])
            if not text:
                continue
            out.setdefault(d, []).append({
                "timestamp": f"{d}T{self.rng.randint(21,23):02d}:{self.rng.randint(0,59):02d}:00",
                "sender_id": sid,
                "sender": f'{s["name_he"]} {self._role_he(s["role"])}',
                "message": text,
                "incident_id": i["incident_id"],
            })
        return out
