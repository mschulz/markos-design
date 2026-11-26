"""Origin Energy profile on invented page text that copies the layout: one table cell per line."""

from markos_runtime.knowledge.bills import answer
from markos_runtime.knowledge.origin import extract_origin, is_origin

SHA = "b" * 64
HEADER = ["Item", "Description", "Units", "Price", " Amount"]
FOOTER = "Origin Energy Electricity Ltd ABN 00 000 000 000"


def row(label: str, units: str, price: str, amount: str, gst_note: bool = False) -> list[str]:
    return [label, *(["(incl GST, if any)"] if gst_note else []), units, price, " " + amount]


def charges(period: str, meter: bool = True, scale: int = 1) -> list[str]:
    rows = [
        *row("General Usage", "1000.000 kWh", "$0.300000", f"${300 * scale}.00"),
        *row("Controlled Load 2 Usage", "200.000 kWh", "$0.200000", f"${40 * scale}.00"),
        *row("Daily Supply", "90 days", "$1.000000", f"${90 * scale}.00"),
        *row("Daily Supply - Controlled", "90 days", "$0.040000", f"${3.6 * scale:.2f}"),
    ]
    if meter:
        rows += row("Solar Meter Charge", "90 days", "$0.100000", f"${9 * scale}.00")
    total = (300 + 40 + 90 + 3.6 + (9 if meter else 0)) * scale
    return ["Usage and supply charges", f"Billing period: {period}", *HEADER, *rows,
            "Total charges", f" ${total:.2f}"]  # fmt: skip


def exports(amount: str = "-$20.00") -> list[str]:
    return ["Solar exports", *HEADER,
            *row("Solar feed-in credit", "500.000 kWh", "$-0.040000", amount, gst_note=True),
            "Total exports", " " + amount]  # fmt: skip


def bill(
    *,
    tables: list[str] | None = None,
    bill_total: str = "$322.60",
    opening: str = "$250.00",
    forward: str = "$0.00",
    owed: str = "$322.60",
    wrap: bool = False,
    extra: list[str] | None = None,
) -> list[str]:
    period = "Billing period: 01 Jan 2026 to 31 Mar 2026 (90"
    page1 = "\n".join(["Invented Household", "Amount due", owed, "Debited on 21 Apr 2026", FOOTER])
    since = ["Since your last bill", "Date", "Item", " Amount",
             "Opening Balance", " " + opening,
             "Payments Received & Account Transfers", " -$250.00",
             "10 Jan 2026", "Payment received", " -$250.00",
             "Balance brought forward", " " + forward]  # fmt: skip
    page2 = [
        "Understand your bill",
        *([period, "days)"] if wrap else [period + " days)"]),
        "This bill is based on an actual reading",
        *since,
        "New charges and credits",
        *(tables or [*charges("01 Jan 2026 to 31 Mar 2026 (90 days)"), *exports()]),
        *(extra or []),
    ]
    page3 = ["Understand your bill (continued)", "Credits", "Date", "Item", " Amount",
             "01 Feb 2026", "Invented state rebate (incl GST of $9.09)", " -$100.00",
             "Total credits", " -$100.00",
             "Your total for this bill", " " + bill_total,
             "GST included in total", " $29.33",
             "*All amounts include GST, unless otherwise specified",
             "Account Balance", " " + owed, "Meter details", "Meter 0000"]  # fmt: skip
    return [page1, "\n".join(page2), "\n".join([*page3, FOOTER])]


def facts(pages: list[str]) -> dict[str, tuple[str, str | None]]:
    return {f.key: (f.state, f.value) for f in extract_origin(pages, SHA).facts}


def test_one_rate_quarter_reconciles_with_cited_cells() -> None:
    report = extract_origin(bill(), SHA)
    assert report.extractor == "origin-pdf-v1" and len(report.facts) == 13
    found = facts(bill())
    assert {state for state, _ in found.values()} == {"found"}
    assert found["period"][1] == "2026-01-01 to 2026-03-31"
    assert found["usage_kwh"][1] == "1200.000" and found["usage_charge"][1] == "340.00"
    assert found["export_credit"][1] == "20.00" and found["concessions"][1] == "100.00"
    assert found["total_owed"][1] == "322.60" and found["due_date"][1] == "2026-04-21"
    total = next(f for f in report.facts if f.key == "total_due")
    assert (total.evidence[0].page, total.evidence[0].quote) == (3, " $322.60")


def test_wrapped_period_line_is_joined() -> None:
    assert facts(bill(wrap=True))["period"] == ("found", "2026-01-01 to 2026-03-31")


def test_split_rate_quarter_adds_both_tables() -> None:
    tables = [*charges("01 Jan 2026 to 15 Feb 2026 (46 days)"), *exports("-$10.00"),
              *charges("16 Feb 2026 to 31 Mar 2026 (44 days)", meter=False, scale=2),
              *exports("-$10.00")]  # fmt: skip
    # Charges 442.60 + 867.20, minus exports 20.00 and credits 100.00.
    found = facts(bill(tables=tables, bill_total="$1189.80", owed="$1,189.80"))
    assert {state for state, _ in found.values()} == {"found"}
    assert found["charges_total"][1] == "1309.80" and found["export_credit"][1] == "20.00"
    assert found["usage_kwh"][1] == "2400.000" and found["total_owed"][1] == "1189.80"


def test_bill_without_solar_meter_charge_still_reconciles() -> None:
    tables = [*charges("01 Jan 2026 to 31 Mar 2026 (90 days)", meter=False), *exports()]
    found = facts(bill(tables=tables, bill_total="$313.60", owed="$313.60"))
    assert found["meter_charge"] == ("missing", None)
    assert found["total_due"] == ("found", "313.60")


def test_balance_forward_is_added_and_flagged() -> None:
    report = extract_origin(bill(opening="$300.00", forward="$50.00", owed="$372.60"), SHA)
    assert {f.state for f in report.facts} == {"found"}
    assert any("balance brought forward" in notice for notice in report.notices)


def test_mismatch_or_unknown_row_marks_money_unreconciled() -> None:
    late = row("Late Payment Fee", "1 days", "$5.000000", "$5.00")
    for pages in (bill(bill_total="$330.00"), bill(extra=late), bill(forward="$9.00")):
        found = facts(pages)
        assert found["total_due"] == ("unreconciled", None)
        assert found["period"][0] == "found" and found["due_date"][0] == "found"


def test_answers_cite_the_amount_due() -> None:
    result = answer(extract_origin(bill(), SHA), "How much do I owe?")
    assert "322.60" in str(result)


def test_origin_is_recognised_by_its_footer() -> None:
    assert is_origin(bill()) and not is_origin(["Example Energy\nAmount due: $1.00"])
