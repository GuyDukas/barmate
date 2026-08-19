"""
The simulation engine.

One pass over the calendar. Every night, demand is generated, drinks deplete
stock through their recipes, planted incidents fire, stock runs out where it
runs out, orders are placed and deliveries land. Physical counts are taken on
count nights and recorded with human error.

The engine keeps two parallel truths:

  true_stock      what is physically on the shelf
  reported counts what a human wrote down

The agent only ever sees the second, plus the POS. The gap between them is the
entire problem BarMate exists to solve, so it is modelled explicitly rather
than approximated.
"""
import math
import random
from collections import defaultdict
from datetime import date, timedelta

from . import config as C
from . import incidents as INC
from . import master_data as M

WINE_GLASS_ML = 150
SPIRIT_CATS = {"gin", "vodka", "whiskey", "rum", "tequila",
               "aperitif", "liqueur", "vermouth"}

# Relative popularity for direct (non-cocktail) sales. Total servings per night
# at a demand index of 1.0. Cocktails are handled separately.
DIRECT_SERVINGS = {
    # bottled beer
    "P001": 11, "P002": 9, "P003": 8, "P004": 4, "P005": 9,
    "P006": 5, "P007": 6, "P008": 3,
    # gin
    "P009": 4, "P010": 3, "P011": 3, "P012": 2, "P013": 5,
    # vodka
    "P014": 7, "P015": 2, "P016": 4, "P017": 2, "P018": 2,
    # whiskey
    "P019": 8, "P020": 7, "P021": 4, "P022": 3, "P023": 2, "P024": 2,
    # rum
    "P025": 4, "P026": 3, "P027": 3, "P028": 1,
    # tequila
    "P029": 3, "P030": 2, "P031": 3, "P032": 2,
    # aperitif / liqueur / vermouth
    "P033": 3, "P034": 3, "P035": 1, "P036": 2, "P037": 1,
    # wine by the glass
    "P038": 9, "P039": 10, "P040": 5, "P041": 4, "P042": 3,
    # soft drinks
    "P043": 14, "P044": 9, "P045": 8, "P046": 6, "P047": 5,
    "P048": 7, "P049": 3,
    # draught
    "K001": 62, "K002": 48, "K003": 55, "K004": 44, "K005": 31,
}

COCKTAIL_SERVINGS = {
    "C001": 22, "C002": 14, "C003": 9, "C004": 19,
    "C005": 12, "C006": 11, "C007": 6,
}

# RAG-005: during 1+1, physical depletion is double the rung-up volume. Applies
# to draught and house pouring brands.
HAPPY_HOUR_DOUBLED = {"K001", "K002", "K003", "K004", "K005",
                      "P013", "P038", "P039", "P025", "P029"}


def daterange(a, b):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def season_factor(d):
    phase = (d.month - C.SEASON_PEAK_MONTH) / 12.0 * 2 * math.pi
    return 1.0 + C.SEASON_AMPLITUDE * math.cos(phase)


def serving_ml(product):
    cat, pid = product["category"], product["product_id"]
    if cat == "draught_beer":
        return C.ML_BEER_SERVING
    if cat == "wine":
        return WINE_GLASS_ML
    if cat in SPIRIT_CATS:
        return C.ML_SHOT
    return float(product["volume_ml"] or 330)  # sold as a whole unit


def serving_price(product):
    cat = product["category"]
    price = float(product["unit_price"])
    if cat == "draught_beer":
        return M.DRAUGHT_SERVING_PRICE
    if cat in SPIRIT_CATS:
        return max(25, round(price * 0.15 / 5) * 5)
    if cat == "wine":
        return max(25, round(price * 0.35 / 5) * 5)
    return price


class Simulation:
    def __init__(self, broadcasts=None, weather=None, holidays=None):
        self.rng = random.Random(C.RANDOM_SEED)
        self.products = M.build_products()
        self.by_id = {p["product_id"]: p for p in self.products}
        self.recipes = M.build_recipes(self.products)
        self.recipe_by_cocktail = defaultdict(list)
        for r in self.recipes:
            self.recipe_by_cocktail[r["cocktail_id"]].append(r)
        self.cocktail_price = {c["cocktail_id"]: c["price"] for c in M.COCKTAILS}

        self.broadcasts = broadcasts or {}   # date -> list of broadcast dicts
        self.weather = weather or {}         # date -> dict
        self.holidays = holidays or {}       # date -> label

        problems = INC.validate(set(self.by_id), {s["staff_id"] for s in M.STAFF})
        if problems:
            raise ValueError("incident definitions are inconsistent: " + "; ".join(problems))

        self.true_stock = {}
        self.pending_deliveries = defaultdict(list)  # arrival date -> [(pid, qty, order_id)]
        self.order_seq = 0

        # Outputs
        self.weekly_need = self._weekly_need()

        self.deliveries_received = []
        self.night_usage = []   # per product, per night: what actually left the shelf
        self.sales = []
        self.orders = []
        self.counts = []
        self.gt_stock = []
        self.gt_events = []
        self.reservations = []
        self.schedule = []
        self.res_seq = 0
        self.sale_seq = 0

    # -------------------------------------------------------------- setup
    def _weekly_need(self):
        """
        Units consumed per week per product at a demand index of 1.0, counting
        BOTH direct servings and the draw from every cocktail that uses the
        product. Sizing stock from direct sales alone starves the cocktail
        ingredients, which is exactly the mistake a real bar makes and exactly
        the mistake we do not want baked into the baseline.
        """
        ml = defaultdict(float)
        for pid, n in DIRECT_SERVINGS.items():
            ml[pid] += n * serving_ml(self.by_id[pid])
        for cid, n in COCKTAIL_SERVINGS.items():
            for r in self.recipe_by_cocktail[cid]:
                ml[r["ingredient_product_id"]] += n * r["quantity_ml"]
        need = {}
        for p in self.products:
            pid = p["product_id"]
            unit_ml = float(p["volume_ml"] or 1000)
            daily = ml.get(pid, 0.0) / unit_ml
            if pid in HAPPY_HOUR_DOUBLED:
                daily *= 1.0 + C.HAPPY_HOUR_SHARE
            need[pid] = daily * 7
        return need

    def seed_stock(self):
        for p in self.products:
            pid = p["product_id"]
            weeks = 1.6 if p["category"] == "draught_beer" else 2.2
            qty = max(float(p["safety_stock"]) + 2, self.weekly_need[pid] * weeks)
            self.true_stock[pid] = round(qty, 3)

    # -------------------------------------------------------------- demand
    def event_multiplier(self, d):
        """
        Applies RAG-004's rules to REAL broadcast data. No multiplier is
        invented for dates where we hold no broadcast listing, which is why
        most of the calendar runs at 1.0.
        """
        listings = self.broadcasts.get(d, [])
        if not listings:
            return 1.0, []
        reasons = []
        mult = 1.0
        live = [b for b in listings if b.get("is_live") == "yes"]
        football = [b for b in live if b.get("sport_type") == "Football"]
        if football:
            mult = max(mult, 1.5)
            reasons.append(f"{len(football)} live football broadcasts")
        local = [b for b in live if any(
            t in (b.get("event_name") or "") for t in
            ("Hapoel", "Maccabi", "Israel", "Beitar", "Bnei"))]
        if local:
            mult *= 1.12
            reasons.append(f"{len(local)} fixtures involving Israeli clubs")
        other_live = [b for b in live if b not in football]
        if other_live and not football:
            mult = max(mult, 1.18)
            reasons.append(f"{len(other_live)} live broadcasts")
        return mult, reasons

    def weather_multiplier(self, d):
        """Only fires when real weather has been loaded. Never guesses."""
        w = self.weather.get(d)
        if not w:
            return 1.0, []
        tmax = w.get("temperature_2m_max")
        rain = w.get("precipitation_sum") or 0
        if tmax is not None and tmax >= 32:
            return 1.15, [f"heat, {tmax:.0f}C"]
        if rain >= 8:
            return 0.82, [f"rain, {rain:.0f}mm"]
        return 1.0, []

    def holiday_multiplier(self, d):
        label = self.holidays.get(d)
        if not label:
            return 1.0, []
        return 1.3, [label]

    def cover_factor(self, d, covers):
        expected = (C.DOW_FACTOR[d.weekday()] * C.RESERVATIONS_PER_DOW_UNIT
                    * C.EXPECTED_PARTY_SIZE)
        if expected <= 0:
            return 1.0
        raw = 1.0 + C.COVER_SENSITIVITY * (covers / expected - 1.0)
        lo, hi = C.COVER_UPLIFT_BOUNDS
        return min(hi, max(lo, raw))

    def demand_index(self, d, covers):
        idx = C.DOW_FACTOR[d.weekday()] * season_factor(d)
        idx *= self.cover_factor(d, covers)
        idx *= self.rng.gauss(1.0, C.DAILY_NOISE_SD)
        ev, ev_r = self.event_multiplier(d)
        we, we_r = self.weather_multiplier(d)
        ho, ho_r = self.holiday_multiplier(d)
        idx *= ev * we * ho
        return max(0.15, idx), ev_r + we_r + ho_r

    # -------------------------------------------------------------- daily pieces
    def make_reservations(self, d):
        base = C.DOW_FACTOR[d.weekday()] * 8
        n = max(0, int(self.rng.gauss(base, 2.5)))
        covers = 0
        for _ in range(n):
            roll = self.rng.random()
            if roll < 0.90:
                rtype, size = "regular", self.rng.choice([2, 2, 3, 4, 4, 5, 6])
            elif roll < 0.955:
                rtype, size = "birthday", self.rng.randint(8, 16)
            elif roll < 0.985:
                rtype, size = "large_group", self.rng.randint(12, 26)
            else:
                rtype, size = "private_event", self.rng.randint(25, 55)
            status = "cancelled" if self.rng.random() < 0.06 else "confirmed"
            self.res_seq += 1
            self.reservations.append({
                "reservation_id": f"R{self.res_seq:06d}",
                "date": d.isoformat(),
                "time": self.rng.choice(["18:30", "19:00", "20:00", "20:30", "21:00", "22:00"]),
                "party_size": size,
                "reservation_type": rtype,
                "status": status,
            })
            if status == "confirmed":
                covers += size
        return covers

    def make_schedule(self, d):
        managers = [s for s in M.STAFF if s["role"] == "shift_manager"]
        bartenders = [s for s in M.STAFF if s["role"] == "bartender"]
        barbacks = [s for s in M.STAFF if s["role"] == "barback"]
        busy = C.DOW_FACTOR[d.weekday()] >= 1.0

        # A planted incident must not depend on a dice roll. Anyone an incident
        # names for tonight is rostered tonight.
        required = {i["staff_id"] for i in INC.by_date(d) if i.get("staff_id")}
        required |= {i["staff_id"] for i in INC.active_overpours(d)}

        on = [self.rng.choice(managers)]
        forced = [s for s in bartenders if s["staff_id"] in required]
        pool = [s for s in bartenders if s["staff_id"] not in required]
        slots = max(0, (3 if busy else 2) - len(forced))
        on += forced + self.rng.sample(pool, min(slots, len(pool)))
        on += [s for s in managers + barbacks
               if s["staff_id"] in required and s not in on]
        if busy:
            on += [b for b in barbacks if b not in on]
        for s in on:
            self.schedule.append({
                "schedule_id": f"SCH{len(self.schedule)+1:06d}",
                "date": d.isoformat(),
                "staff_id": s["staff_id"],
                "name_en": s["name_en"],
                "name_he": s["name_he"],
                "role": s["role"],
                "station": s["station"],
                "shift_start": "18:00",
                "shift_end": "03:00",
            })
        return [s["staff_id"] for s in on]

    # -------------------------------------------------------------- main day
    def run_day(self, d, on_shift):
        # Deliveries arrive during the day, before doors open. Anything that
        # happens during service therefore acts on the restocked shelf.
        for pid, qty, oid in self.pending_deliveries.pop(d, []):
            self.true_stock[pid] = round(self.true_stock.get(pid, 0) + qty, 4)
            self.deliveries_received.append({
                "date": d.isoformat(), "order_id": oid,
                "product_id": pid, "units_received": qty})

        covers = self.make_reservations(d)
        idx, reasons = self.demand_index(d, covers)

        ml_required = defaultdict(float)   # physical ml to remove
        ml_sold = defaultdict(float)       # ml the POS accounts for
        day_sales = []

        # cocktails
        for cid, base in COCKTAIL_SERVINGS.items():
            n = max(0, int(round(self.rng.gauss(base * idx, base * 0.18))))
            if not n:
                continue
            for r in self.recipe_by_cocktail[cid]:
                pid = r["ingredient_product_id"]
                ml = n * r["quantity_ml"]
                ml_required[pid] += ml
                ml_sold[pid] += ml
            day_sales.append(dict(item_type="cocktail", item_id=cid, units=n,
                                  revenue=n * self.cocktail_price[cid]))

        # direct servings
        for pid, base in DIRECT_SERVINGS.items():
            n = max(0, int(round(self.rng.gauss(base * idx, base * 0.20))))
            if not n:
                continue
            p = self.by_id[pid]
            ml = n * serving_ml(p)
            ml_required[pid] += ml
            ml_sold[pid] += ml
            day_sales.append(dict(item_type="product", item_id=pid, units=n,
                                  revenue=n * serving_price(p)))

        # happy hour: physical depletion doubles on the affected lines for the
        # share of volume that falls inside the window. POS revenue does not.
        for pid in list(ml_required):
            if pid in HAPPY_HOUR_DOUBLED:
                ml_required[pid] += ml_required[pid] * C.HAPPY_HOUR_SHARE

        # over-pouring: every pour of the affected product runs heavy
        for op in INC.active_overpours(d):
            if op["staff_id"] in on_shift and op["product_id"] in ml_required:
                extra = ml_required[op["product_id"]] * op["magnitude_pct"]
                ml_required[op["product_id"]] += extra
                self.gt_events.append(dict(
                    date=d.isoformat(), incident_id=op["incident_id"],
                    type="overpour", product_id=op["product_id"],
                    units=round(extra / float(self.by_id[op["product_id"]]["volume_ml"]), 4),
                    ml=round(extra, 1)))

        # convert to units, apply stock ceiling
        taken_units = {}
        for pid, ml in ml_required.items():
            p = self.by_id[pid]
            unit_ml = float(p["volume_ml"] or 1000)
            want = ml / unit_ml
            have = self.true_stock.get(pid, 0.0)
            taken = min(want, have)
            taken_units[pid] = taken
            self.true_stock[pid] = round(have - taken, 4)
            shortfall_units = want - taken
            if shortfall_units > 1e-6:
                lost = int(round(shortfall_units * unit_ml / serving_ml(p)))
                for s in day_sales:
                    if s["item_id"] == pid:
                        served = max(0, s["units"] - lost)
                        s["revenue"] = round(s["revenue"] * (served / s["units"]), 2) if s["units"] else 0
                        s["lost"] = s["units"] - served
                        s["units"] = served
                        break

        # incidents that remove stock outside the POS
        for i in INC.by_date(d):
            self.apply_incident(d, i)

        # What a bartender would have seen by close: how much they got through,
        # and what was left on the shelf. Written after incidents because a
        # dropped bottle is gone by the time anyone counts.
        for pid, taken in taken_units.items():
            p = self.by_id[pid]
            self.night_usage.append({
                "date": d.isoformat(), "product_id": pid,
                "product_name": p["name"], "name_he": p["name_he"],
                "category": p["category"], "station": p["station"],
                "units_used": round(taken, 3),
                "closing_stock": round(self.true_stock.get(pid, 0.0), 3)})

        # write POS lines
        for s in day_sales:
            self.sale_seq += 1
            if s["item_type"] == "cocktail":
                name = next(c["name"] for c in M.COCKTAILS if c["cocktail_id"] == s["item_id"])
                cat = "cocktail"
            else:
                name = self.by_id[s["item_id"]]["name"]
                cat = self.by_id[s["item_id"]]["category"]
            self.sales.append({
                "sale_id": f"SALE{self.sale_seq:07d}",
                "date": d.isoformat(),
                "item_type": s["item_type"],
                "item_id": s["item_id"],
                "item_name": name,
                "category": cat,
                "units_sold": s["units"],
                "lost_sales_due_to_stockout": s.get("lost", 0),
                "revenue": round(s["revenue"], 2),
            })

        # end of day truth
        for p in self.products:
            pid = p["product_id"]
            self.gt_stock.append({
                "date": d.isoformat(), "product_id": pid, "product_name": p["name"],
                "category": p["category"],
                "true_closing_stock": round(self.true_stock.get(pid, 0.0), 3),
                "stockout": self.true_stock.get(pid, 0.0) <= 0.02,
            })

        if d.weekday() in C.COUNT_WEEKDAYS:
            self.take_count(d)
        self.place_orders(d)
        return idx, reasons, covers

    # -------------------------------------------------------------- incidents
    def apply_incident(self, d, i):
        pid = i["product_id"]
        p = self.by_id[pid]
        unit_ml = float(p["volume_ml"] or 1000)
        units = 0.0

        if i["type"] == "unrecorded_removal":
            # Stock leaves the building without a POS line and without anyone
            # writing it down. Expressed as the level it leaves behind, so the
            # scenario lands in the same place regardless of where the ordering
            # sawtooth happened to be that week.
            have = self.true_stock.get(pid, 0.0)
            units = max(0.0, have - i["leave_units"])
        elif i["type"] == "breakage":
            units = i["magnitude_units"]
        elif i["type"] in ("comp", "walkout"):
            units = i["magnitude_servings"] * serving_ml(p) / unit_ml
        elif i["type"] == "keg_rma":
            units = i["remaining_fraction"]
        elif i["type"] == "delivery_short":
            units = 0.0  # handled as a smaller delivery, recorded below
        elif i["type"] == "miscount":
            units = 0.0  # affects the written count, not the shelf

        if units:
            self.true_stock[pid] = round(max(0.0, self.true_stock.get(pid, 0) - units), 4)

        self.gt_events.append(dict(
            date=d.isoformat(), incident_id=i["incident_id"], type=i["type"],
            product_id=pid, units=round(units, 4), ml=round(units * unit_ml, 1)))

    # -------------------------------------------------------------- counting
    def take_count(self, d):
        planted = {i["product_id"]: i for i in INC.by_date(d) if i["type"] == "miscount"}
        counter = self.rng.choice([s for s in M.STAFF if s["role"] != "barback"])
        for p in self.products:
            pid = p["product_id"]
            true_v = round(self.true_stock.get(pid, 0.0), 2)
            err = 0.0
            if pid in planted:
                err = planted[pid]["magnitude_units"]
            else:
                roll, acc = self.rng.random(), 0.0
                for prob, lo, hi in C.COUNT_ERROR_MODES:
                    acc += prob
                    if roll <= acc:
                        if hi > 0:
                            err = round(self.rng.uniform(lo, hi), 2)
                            err *= self.rng.choice([-1, 1])
                        break
            reported = max(0.0, round(true_v + err, 2))
            self.counts.append({
                "count_id": f"CNT{len(self.counts)+1:06d}",
                "date": d.isoformat(), "product_id": pid, "product_name": p["name"],
                "reported_stock": reported, "counted_by": counter["name_en"],
                "station": p["station"],
            })
            self.gt_stock_error = None
            self._count_gt = getattr(self, "_count_gt", [])
            self._count_gt.append({
                "count_id": f"CNT{len(self.counts):06d}", "date": d.isoformat(),
                "product_id": pid, "true_stock": true_v,
                "reported_stock": reported, "count_error": round(reported - true_v, 2),
                "planted": pid in planted,
                "incident_id": planted[pid]["incident_id"] if pid in planted else "",
            })

    # -------------------------------------------------------------- ordering
    def place_orders(self, d):
        day_name = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][d.weekday()]
        shorts = {i["product_id"]: i for i in INC.by_date(d)
                  if i["type"] == "delivery_short"}
        for sup in M.SUPPLIERS:
            if day_name not in sup["delivery_days"].split(","):
                continue
            for p in self.products:
                if p["supplier_id"] != sup["supplier_id"]:
                    continue
                pid = p["product_id"]
                # Cover the gap until the next delivery from this supplier plus
                # the safety floor. Two delivery days a week means roughly half
                # a week of cover, with headroom for a busy weekend.
                target = float(p["safety_stock"]) + self.weekly_need[pid] * 0.85
                if self.true_stock.get(pid, 0) >= target:
                    continue
                need = target - self.true_stock.get(pid, 0)
                case = int(p["case_size"])
                qty = max(case, int(math.ceil(need / case) * case))
                self.order_seq += 1
                oid = f"O{self.order_seq:06d}"
                delayed = self.rng.random() < C.DELAY_PROBABILITY
                exp = d + timedelta(days=C.ORDER_LEAD_DAYS_NOMINAL)
                act = exp + timedelta(days=self.rng.randint(*C.DELAY_EXTRA_DAYS)) if delayed else exp
                received = qty
                if pid in shorts:
                    received = shorts[pid]["received_units"]
                    qty = shorts[pid]["ordered_units"]
                self.orders.append({
                    "order_id": oid, "order_date": d.isoformat(), "product_id": pid,
                    "product_name": p["name"], "quantity": qty,
                    "expected_delivery_date": exp.isoformat(),
                    "actual_delivery_date": act.isoformat() if act <= C.SIM_END else "",
                    "status": "delayed" if delayed else "delivered",
                    "supplier_id": sup["supplier_id"], "supplier": sup["name"],
                })
                if act <= C.SIM_END:
                    self.pending_deliveries[act].append((pid, received, oid))

    # -------------------------------------------------------------- driver
    def run(self):
        self.seed_stock()
        daily = []
        for d in daterange(C.SIM_START, C.SIM_END):
            on_shift = self.make_schedule(d)
            idx, reasons, covers = self.run_day(d, on_shift)
            daily.append({"date": d.isoformat(), "demand_index": round(idx, 4),
                          "covers": covers, "context": "; ".join(reasons)})
        self.daily = daily
        return self
