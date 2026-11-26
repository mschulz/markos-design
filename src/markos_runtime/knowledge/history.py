"""Combined bill history across suppliers: effective reports in, ordered rows and answers out.

Each profile's facts map to one shared set of figures. Totals include GST for every profile:
Amber's charge lines include its GST line, and Origin prints amounts with GST included.
A figure that is not found or corrected shows as "not established", never as a number.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from markos_runtime.knowledge.bills import BillFacts, BillKey

SUPPLIERS = {"amber-pdf-v1": "Amber Electric", "origin-pdf-v1": "Origin Energy"}
# Shared figure -> the fact that holds it in every profile.
FIGURES: dict[str, BillKey] = {
    "usage_kwh": "usage_kwh",
    "export_credit": "export_credit",
    "bill_total": "total_due",
    "amount_owed": "total_owed",
    "brought_forward": "brought_forward",
}
QUESTIONS = {
    "how much electricity did i use each period": "usage",
    "how has my bill changed": "change",
}


def established(report: BillFacts, key: BillKey) -> str | None:
    fact = next((f for f in report.facts if f.key == key), None)
    if fact is None or fact.state not in {"found", "corrected"}:
        return None
    return fact.value


def row(bill_id: UUID, name: str, report: BillFacts | None) -> dict[str, Any]:
    """One history row. `report` is None when the original bill failed its integrity check."""
    out: dict[str, Any] = {
        "bill_id": str(bill_id),
        "name": name,
        "supplier": SUPPLIERS.get(report.extractor, "Pasted text") if report else None,
        "available": report is not None,
        "start": None,
        "end": None,
        "days": None,
        "figures": {},
        "cost_per_day": None,
        "kwh_per_day": None,
        "corrected": False,
    }
    if report is None:
        return out
    states = {f.key: f.state for f in report.facts}
    out["corrected"] = "corrected" in states.values()
    period = established(report, "period")
    if period:
        start, end = (date.fromisoformat(d) for d in period.split(" to "))
        out.update(start=str(start), end=str(end), days=(end - start).days + 1)
    for name_, key in FIGURES.items():
        value = established(report, key)
        state = states.get(key, "missing")
        out["figures"][name_] = {"value": value, "state": "found" if value else state}
    days = out["days"]
    total, kwh = out["figures"]["bill_total"]["value"], out["figures"]["usage_kwh"]["value"]
    if days and total is not None:
        out["cost_per_day"] = str((Decimal(total) / days).quantize(Decimal("0.01")))
    if days and kwh is not None:
        out["kwh_per_day"] = str((Decimal(kwh) / days).quantize(Decimal("0.001")))
    return out


def history(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Rows in period order, with notices for gaps, overlaps and bills without a period."""
    dated = sorted((r for r in rows if r["start"]), key=lambda r: (r["start"], r["end"]))
    undated = [r for r in rows if not r["start"]]
    notices: list[str] = []
    for before, after in zip(dated, dated[1:], strict=False):
        expected = date.fromisoformat(before["end"]) + timedelta(days=1)
        start = date.fromisoformat(after["start"])
        if start > expected:
            notices.append(
                f"Gap: no bill covers {expected} to {start - timedelta(days=1)}, "
                f"between {before['name']} and {after['name']}."
            )
        elif start < expected:
            notices.append(
                f"Overlap: {before['name']} and {after['name']} both cover {start} "
                f"to {min(date.fromisoformat(before['end']), date.fromisoformat(after['end']))}."
            )
    for r in undated:
        reason = "its original failed a check" if not r["available"] else "its period is unclear"
        notices.append(f"{r['name']} is listed last because {reason}.")
    return {"items": dated + undated, "notices": notices}


def cite(r: dict[str, Any]) -> dict[str, Any]:
    return {"bill_id": r["bill_id"], "name": r["name"], "period": f"{r['start']} to {r['end']}"}


def answer(view: dict[str, Any], question: str) -> dict[str, Any]:
    q = " ".join(question.lower().strip().rstrip("?.!").split())
    kind = QUESTIONS.get(q)
    dated = [r for r in view["items"] if r["start"]]
    if kind is None:
        text = "I cannot answer that from your bill history. Try one of the suggested questions."
        return {"answer": text, "citations": []}
    if not dated:
        return {"answer": "No bill has a clear billing period yet.", "citations": []}
    if kind == "usage":
        parts = []
        for r in dated:
            label = f"{r['start']} to {r['end']} ({r['supplier']})"
            if r["kwh_per_day"] is None:
                state = r["figures"]["usage_kwh"]["state"]
                parts.append(f"{label}: not established ({state}).")
            else:
                kwh = r["figures"]["usage_kwh"]["value"]
                parts.append(f"{label}: {kwh} kWh, {r['kwh_per_day']} kWh a day.")
        return {"answer": " ".join(parts), "citations": [cite(r) for r in dated]}
    costed = [r for r in dated if r["cost_per_day"] is not None]
    if len(costed) < 2:
        return {
            "answer": "Fewer than two bills have an established total, so I cannot compare them.",
            "citations": [cite(r) for r in costed],
        }
    earlier, latest = costed[-2], costed[-1]
    a, b = Decimal(earlier["cost_per_day"]), Decimal(latest["cost_per_day"])
    change = b - a
    direction = "more" if change > 0 else "less" if change < 0 else "the same"
    text = (
        f"Your latest bill, {latest['start']} to {latest['end']} ({latest['supplier']}), "
        f"cost ${b} a day. The bill before, {earlier['start']} to {earlier['end']} "
        f"({earlier['supplier']}), cost ${a} a day."
    )
    if change:
        percent = (abs(change) / a * 100).quantize(Decimal("1")) if a else None
        text += f" That is ${abs(change)} a day {direction}" + (
            f", or {percent}%." if percent is not None else "."
        )
    else:
        text += " The cost per day is the same."
    text += " Days are counted including both the first and last day of each period."
    return {"answer": text, "citations": [cite(earlier), cite(latest)]}
