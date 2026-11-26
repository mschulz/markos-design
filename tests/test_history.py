"""Combined bill history on invented reports: order, per-day figures, gaps, overlaps, answers."""

from typing import Any
from uuid import UUID

from markos_runtime.knowledge.bills import BillFacts, Fact
from markos_runtime.knowledge.history import answer, history, row

SHA = "a" * 64


def report(
    extractor: str, period: str | None, total: str | None, kwh: str = "300.000"
) -> BillFacts:
    def fact(key: str, value: str | None, state: str = "found") -> Fact:
        return Fact.model_validate(
            {
                "schema_version": 1,
                "key": key,
                "label": key,
                "value": value,
                "unit": "",
                "state": state if value is not None else "unreconciled",
                "evidence": [{"schema_version": 1, "line": 1, "quote": "invented"}]
                if value
                else [],
            }
        )

    facts = [fact("period", period), fact("usage_kwh", kwh), fact("total_due", total)]
    if extractor == "origin-pdf-v1":
        facts.append(fact("export_credit", "12.50"))
    return BillFacts.model_validate(
        {
            "schema_version": 1,
            "extractor": extractor,
            "source_sha256": SHA,
            "facts": facts,
            "notices": [],
        }
    )


def bill(n: int, extractor: str, period: str | None, total: str | None) -> dict[str, Any]:
    return row(UUID(int=n), f"Bill {n}", report(extractor, period, total))


def test_mixed_suppliers_in_period_order_with_per_day_figures() -> None:
    view = history(
        [
            bill(1, "amber-pdf-v1", "2026-04-01 to 2026-04-30", "90.00"),
            bill(2, "origin-pdf-v1", "2026-01-01 to 2026-03-31", "450.00"),
        ]
    )
    first, second = view["items"]
    assert (first["supplier"], first["days"], first["cost_per_day"]) == (
        "Origin Energy",
        90,
        "5.00",
    )
    assert (second["supplier"], second["days"], second["cost_per_day"]) == (
        "Amber Electric",
        30,
        "3.00",
    )
    assert second["kwh_per_day"] == "10.000"
    assert second["figures"]["export_credit"] == {"value": None, "state": "missing"}
    assert view["notices"] == []


def test_gaps_and_overlaps_are_notices() -> None:
    view = history(
        [
            bill(1, "amber-pdf-v1", "2026-01-01 to 2026-01-31", "90.00"),
            bill(2, "amber-pdf-v1", "2026-02-10 to 2026-02-28", "90.00"),
            bill(3, "origin-pdf-v1", "2026-02-20 to 2026-03-31", "90.00"),
        ]
    )
    assert view["notices"] == [
        "Gap: no bill covers 2026-02-01 to 2026-02-09, between Bill 1 and Bill 2.",
        "Overlap: Bill 2 and Bill 3 both cover 2026-02-20 to 2026-02-28.",
    ]


def test_unreconciled_total_is_never_a_number() -> None:
    item = bill(1, "origin-pdf-v1", "2026-01-01 to 2026-03-31", None)
    assert item["figures"]["bill_total"] == {"value": None, "state": "unreconciled"}
    assert item["cost_per_day"] is None
    reply = answer(history([item]), "How has my bill changed?")
    assert "Fewer than two bills" in reply["answer"]


def test_unavailable_and_undated_bills_are_listed_last() -> None:
    view = history(
        [
            row(UUID(int=1), "Broken", None),
            bill(2, "amber-pdf-v1", None, "90.00"),
            bill(3, "amber-pdf-v1", "2026-01-01 to 2026-01-31", "93.00"),
        ]
    )
    assert [r["name"] for r in view["items"]] == ["Bill 3", "Broken", "Bill 2"]
    assert view["notices"] == [
        "Broken is listed last because its original failed a check.",
        "Bill 2 is listed last because its period is unclear.",
    ]
    assert view["items"][1]["figures"] == {}


def test_answers_cite_the_bills_they_use() -> None:
    view = history(
        [
            bill(1, "origin-pdf-v1", "2026-01-01 to 2026-03-31", "450.00"),
            bill(2, "amber-pdf-v1", "2026-04-01 to 2026-04-30", "90.00"),
        ]
    )
    change = answer(view, "how has my bill changed")
    assert "cost $3.00 a day" in change["answer"] and "cost $5.00 a day" in change["answer"]
    assert "$2.00 a day less, or 40%." in change["answer"]
    assert [c["name"] for c in change["citations"]] == ["Bill 1", "Bill 2"]
    usage = answer(view, "How much electricity did I use each period?")
    assert (
        "2026-04-01 to 2026-04-30 (Amber Electric): 300.000 kWh, 10.000 kWh a day."
        in usage["answer"]
    )
    assert len(usage["citations"]) == 2
    assert answer(view, "What is the weather?")["citations"] == []
