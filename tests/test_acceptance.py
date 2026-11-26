"""K1-C acceptance examples: invented bills with the expected facts and answers the owner approves.

Each case names the arithmetic rule it checks (docs/k1-bill-knowledge.md, "Bill arithmetic rules").
Change an expected answer here only with the owner's approval.
"""

from markos_runtime.knowledge.amber import extract_amber
from markos_runtime.knowledge.bills import BillFacts, answer
from markos_runtime.knowledge.origin import extract_origin
from tests.test_amber import pages as amber_pages
from tests.test_origin import bill as origin_bill
from tests.test_origin import charges, exports

AMBER, ORIGIN = "a" * 64, "b" * 64


def found(report: BillFacts) -> dict[str, tuple[str, str | None]]:
    return {f.key: (f.state, f.value) for f in report.facts}


def owe(report: BillFacts) -> str:
    return str(answer(report, "How much do I owe?")["answer"])


def test_ordinary_amber_bill() -> None:
    """Rules: Amber GST, Amber totals."""
    report = extract_amber(amber_pages(), AMBER)
    assert {state for state, _ in found(report).values()} == {"found"}
    assert found(report)["charges_gst"] == ("found", "9.59")
    assert owe(report) == "Bill total: 47.99 AUD. Total owed: 47.99 AUD."


def test_ordinary_origin_bill() -> None:
    """Rules: Origin GST, Origin totals, export credits."""
    report = extract_origin(origin_bill(), ORIGIN)
    assert {state for state, _ in found(report).values()} == {"found"}
    assert found(report)["export_credit"] == ("found", "20.00")
    assert owe(report) == "Total for this bill: 322.60 AUD. Amount due: 322.60 AUD."


def test_unpaid_earlier_balance() -> None:
    """Rule: earlier balances. Origin adds it to the amount due. Amber flags the difference."""
    origin = extract_origin(
        origin_bill(opening="$300.00", forward="$50.00", owed="$372.60"), ORIGIN
    )
    assert found(origin)["brought_forward"] == ("found", "50.00")
    assert owe(origin) == "Total for this bill: 322.60 AUD. Amount due: 372.60 AUD."
    assert any("balance brought forward" in n for n in origin.notices)
    amber = extract_amber(amber_pages(owed="$80.00"), AMBER)
    assert owe(amber) == "Bill total: 47.99 AUD. Total owed: 80.00 AUD."
    assert any("Total owed differs" in n for n in amber.notices)


def test_credit_bill_with_negative_total() -> None:
    """Rule: export credits. Exports larger than charges give a negative bill total."""
    tables = [*charges("01 Jan 2026 to 31 Mar 2026 (90 days)"), *exports("-$500.00")]
    # Charges 442.60, minus exports 500.00 and credits 100.00.
    report = extract_origin(
        origin_bill(tables=tables, bill_total="-$157.40", owed="-$157.40"), ORIGIN
    )
    assert {state for state, _ in found(report).values()} == {"found"}
    assert found(report)["export_credit"] == ("found", "500.00")
    assert owe(report) == "Total for this bill: -157.40 AUD. Amount due: -157.40 AUD."


def test_estimated_read_needs_review() -> None:
    """Rule: estimated reads. Money figures are not established, dates and usage still are."""
    pages = [
        p.replace("based on an actual reading", "based on an estimated reading")
        for p in origin_bill()
    ]
    report = extract_origin(pages, ORIGIN)
    assert found(report)["total_due"] == ("unreconciled", None)
    assert found(report)["period"] == ("found", "2026-01-01 to 2026-03-31")
    assert owe(report) == (
        "Total for this bill: not established (unreconciled). Check the original text. "
        "Amount due: not established (unreconciled). Check the original text."
    )


def test_split_rate_quarter() -> None:
    """Rule: tariff structures. Both rate periods are added together."""
    tables = [*charges("01 Jan 2026 to 15 Feb 2026 (46 days)"), *exports("-$10.00"),
              *charges("16 Feb 2026 to 31 Mar 2026 (44 days)", meter=False, scale=2),
              *exports("-$10.00")]  # fmt: skip
    report = extract_origin(
        origin_bill(tables=tables, bill_total="$1189.80", owed="$1,189.80"), ORIGIN
    )
    assert found(report)["charges_total"] == ("found", "1309.80")
    assert found(report)["usage_kwh"] == ("found", "2400.000")
    assert owe(report) == "Total for this bill: 1189.80 AUD. Amount due: 1189.80 AUD."
