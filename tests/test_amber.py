"""Amber Electric profile on invented page text that copies the real layout and pypdf quirks."""

from typing import cast

import pytest

from markos_runtime.knowledge.amber import extract_amber, is_amber, unspace
from markos_runtime.knowledge.bills import answer, extract

SHA = "a" * 64
OPEN, CLOSE, DASH = "", "", ""  # Amber's font maps these to private use.
FOOTER = "Amber Electric Pty Ltd ABN 00 000 000 000" + CLOSE + " an authorised electricity retailer"


def spaced(amount: str) -> str:
    """Render `$40.00` the way pypdf extracts some Amber amounts: `$ 4 0 . 0 0 `."""
    return "$ " + " ".join(amount.removeprefix("$")) + " "


def page1(owed: str = "$47.99", due: str = "10 Dec 2026") -> str:
    return "\n".join(
        [
            "SAMPLE CUSTOMER",
            "1 EXAMPLE STREET SAMPLETOWN QLD 4000",
            "Account number: 00000000",
            "Invoice Number: 0000000",
            "Total Owed",
            owed,
            f"Due Date: {due}",
            FOOTER,
        ]
    )


def page2(
    subscription: str = "Amber Monthly Subscription",
    usage: str = "$40.00",
    extra: list[str] | None = None,
    period: str = f"{OPEN}01/11/2026 {DASH} 30/11/2026{CLOSE}",
) -> str:
    return "\n".join(
        [
            "Understand your bill",
            "Plan:",
            "Amber Core",
            "Billing Period:",
            "30 days",
            period,
            "CHARGES",
            "$105.49 −",
            "CREDITS",
            "$57.50 =",
            "BILL AMOUNT",
            spaced("$47.99"),
            "CHARGES SUMMARY",
            "Charge Description Amount Rate Cost",
            f"Usage 400.00 kWh 0.1000 $/kWh {spaced(usage)}",
            f"Network Daily Supply Charges 30 days 0.9300 $/Day {spaced('$27.90')}",
            f"Network Demand Charges {spaced('$5.00')}",
            f"{subscription} {spaced('$23.00')}",
            *(extra or []),
            f"GST {DASH} 10% $9.59",
            "CHARGES TOTAL $105.49",
            "CREDITS SUMMARY",
            "Charge Description Amount Rate Credit",
            f"Solar Exports 50.00 kWh 0.0500 $/kWh {spaced('$2.50')}",
            f"Vouchers & Concessions {spaced('$50.00')}",
            f"GST {DASH} 10% $5.00",
            "CREDITS TOTAL $57.50",
            "BILL TOTAL $47.99",
            "YOUR USAGE DATA",
            "Average Daily Usage (kWh): 13.330",
        ]
    )


def charges_page() -> str:
    # Pages 3 to 5 repeat the header figures; they must not be cited.
    return "\n".join(
        ["Understand Your Bill: Charges", "CHARGES", "$105.49 −", "BILL AMOUNT", "$47.99"]
    )


def pages(**kwargs: object) -> list[str]:
    owed = kwargs.pop("owed", "$47.99")
    return [page1(str(owed)), page2(**kwargs), charges_page(), FOOTER]  # type: ignore[arg-type]


def facts(report: object) -> dict[str, tuple[str | None, str]]:
    return {f.key: (f.value, f.state) for f in report.facts}  # type: ignore[attr-defined]


@pytest.mark.parametrize("subscription", ["Amber Monthly Subscription", "Amber Membership"])
def test_reads_every_summary_figure_with_page_citations(subscription: str) -> None:
    report = extract_amber(pages(subscription=subscription), SHA)
    assert report.extractor == "amber-pdf-v1" and report.source_sha256 == SHA
    assert facts(report) == {
        "period": ("2026-11-01 to 2026-11-30", "found"),
        "usage_kwh": ("400.00", "found"),
        "usage_charge": ("40.00", "found"),
        "supply_charge": ("27.90", "found"),
        "demand_charge": ("5.00", "found"),
        "subscription": ("23.00", "found"),
        "charges_gst": ("9.59", "found"),
        "charges_total": ("105.49", "found"),
        "export_credit": ("2.50", "found"),
        "concessions": ("50.00", "found"),
        "credits_gst": ("5.00", "found"),
        "credits_total": ("57.50", "found"),
        "total_due": ("47.99", "found"),
        "total_owed": ("47.99", "found"),
        "due_date": ("2026-12-10", "found"),
    }
    cited = {f.key: [(e.page, e.line) for e in f.evidence] for f in report.facts}
    assert cited["total_due"] == [(2, 27)] and cited["total_owed"] == [(1, 6)]
    assert cited["charges_gst"] == [(2, 19)] and cited["credits_gst"] == [(2, 25)]
    usage = next(f for f in report.facts if f.key == "usage_charge")
    assert usage.evidence[0].quote == page2(subscription).splitlines()[14]  # original, unjoined
    assert any("adds up to its total" in n for n in report.notices)
    assert not any("Needs review" in n for n in report.notices)


def test_answers_cite_pages_and_report_both_totals() -> None:
    result = answer(extract_amber(pages(), SHA), "How much do I owe?")
    assert result["answer"] == "Bill total: 47.99 AUD. Total owed: 47.99 AUD."
    citations = cast(list[dict[str, object]], result["citations"])
    assert [(c["page"], c["line"]) for c in citations] == [(2, 27), (1, 6)]
    due = answer(extract_amber(pages(), SHA), "When is this bill due?")
    assert due["answer"] == "Due date: 2026-12-10."


def test_totals_that_do_not_add_up_are_unreconciled() -> None:
    report = extract_amber(pages(usage="$41.00"), SHA)
    states = facts(report)
    assert states["usage_charge"] == (None, "unreconciled")
    assert states["total_due"] == (None, "unreconciled")
    assert states["usage_kwh"] == ("400.00", "found")  # not money, still reported
    assert "Needs review: the charge lines do not add up to the charges total." in report.notices
    assert "not established (unreconciled)" in str(answer(report, "How much do I owe?")["answer"])


def test_unknown_summary_line_blocks_confident_totals() -> None:
    report = extract_amber(pages(extra=[f"Late Payment Fee {spaced('$5.00')}"]), SHA)
    assert facts(report)["charges_total"] == (None, "unreconciled")
    assert any("unrecognised summary lines: page 2 line 19" in n for n in report.notices)


def test_earlier_balance_is_flagged_not_merged() -> None:
    report = extract_amber(pages(owed="$80.00"), SHA)
    assert facts(report)["total_owed"] == ("80.00", "found")
    assert facts(report)["total_due"] == ("47.99", "found")
    assert any("Total owed differs" in n for n in report.notices)


def test_invalid_period_and_missing_summary_need_review() -> None:
    report = extract_amber(pages(period="31/11/2026 30/11/2026"), SHA)
    assert facts(report)["period"] == (None, "invalid")
    reversed_dates = extract_amber(pages(period="30/11/2026 01/11/2026"), SHA)
    assert facts(reversed_dates)["period"] == (None, "invalid")
    no_summary = extract_amber([page1(), charges_page()], SHA)
    assert "Needs review: the bill summary page was not found exactly once." in no_summary.notices
    assert facts(no_summary)["total_due"] == (None, "missing")
    # Page 1 states the amount owed directly, so it does not depend on the summary check.
    assert facts(no_summary)["total_owed"] == ("47.99", "found")


def test_duplicate_summary_line_conflicts() -> None:
    report = extract_amber(pages(extra=[f"Network Demand Charges {spaced('$6.00')}"]), SHA)
    assert facts(report)["demand_charge"] == (None, "conflicting")
    assert facts(report)["total_due"] == (None, "unreconciled")


def test_profile_detection_and_normalising() -> None:
    assert is_amber(pages()) and not is_amber(["Example Energy\nAmount due: $1.00"])
    assert unspace("Usage 1 kWh 0.1 $/kWh $ 5 7 . 1 5 ") == "Usage 1 kWh 0.1 $/kWh $57.15"
    assert unspace(f"GST {DASH} 10% $9.59") == "GST 10% $9.59"


def test_text_bills_keep_their_extractor_and_citations() -> None:
    from markos_runtime.knowledge.bills import SAMPLE

    report = extract(SAMPLE)
    assert report.extractor == "bill-text-v1" and len(report.facts) == 6
    citation = answer(report, "How much do I owe?")["citations"][0]  # type: ignore[index]
    assert "page" not in citation
