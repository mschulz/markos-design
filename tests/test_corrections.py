"""Owner corrections: values are checked, the stored report never changes, totals are rechecked."""

from datetime import UTC, datetime

import pytest

from markos_runtime.knowledge.amber import extract_amber
from markos_runtime.knowledge.bills import SAMPLE, BillCorrection, answer, extract
from markos_runtime.knowledge.corrections import effective, normalise
from markos_runtime.knowledge.origin import extract_origin
from tests.test_amber import SHA, pages
from tests.test_origin import bill, charges, exports

AT = datetime(2026, 12, 1, tzinfo=UTC)


def fix(n: int, key: str, value: str | None, reason: str = "checked the bill") -> BillCorrection:
    return BillCorrection.model_validate(
        {"schema_version": 1, "correction_id": n, "key": key, "value": value, "reason": reason,
         "created_at": AT},
        strict=False,
    )  # fmt: skip


def view(report: object, *corrections: BillCorrection) -> dict[str, tuple[str, str | None]]:
    result = effective(report, list(corrections))  # type: ignore[arg-type]
    return {f.key: (f.state, f.value) for f in result.facts}


def test_values_are_normalised_or_rejected() -> None:
    assert normalise("usage_charge", "1,234.5") == "1234.50"
    assert normalise("usage_kwh", "400") == "400.000"
    assert normalise("due_date", "2026-12-10") == "2026-12-10"
    assert normalise("brought_forward", "-12.30") == "-12.30"
    assert normalise("period", "2026-11-01 to 2026-11-30") == "2026-11-01 to 2026-11-30"
    for key, value in [
        ("usage_charge", "-1.00"),
        ("usage_charge", "1.005"),
        ("usage_charge", "$5"),
        ("due_date", "10/12/2026"),
        ("period", "2026-11-30 to 2026-11-01"),
    ]:
        with pytest.raises(ValueError):
            normalise(key, value)  # type: ignore[arg-type]


def test_correction_makes_an_unreconciled_bill_add_up() -> None:
    report = extract_amber(pages(usage="$41.00"), SHA)
    assert view(report)["total_due"] == ("unreconciled", None)
    # Every unreconciled money figure must have a value before the totals can be checked.
    one = view(report, fix(1, "usage_charge", "40.00"))
    assert one["usage_charge"] == ("unreconciled", None) and one["total_due"][0] == "unreconciled"
    fixed = {
        "usage_charge": "40.00", "supply_charge": "27.90", "demand_charge": "5.00",
        "subscription": "23.00", "charges_gst": "9.59", "charges_total": "105.49",
        "export_credit": "2.50", "concessions": "50.00", "credits_gst": "5.00",
        "credits_total": "57.50", "total_due": "47.99",
    }  # fmt: skip
    all_fixed = [fix(n, key, value) for n, (key, value) in enumerate(fixed.items(), 1)]
    result = effective(report, all_fixed)
    assert {f.key: f.state for f in result.facts}["total_due"] == "corrected"
    assert "The totals add up using your corrections." in result.notices
    assert result.facts[0].evidence == report.facts[0].evidence  # citations are kept


def test_wrong_correction_makes_a_good_bill_unreconciled_and_withdrawal_restores_it() -> None:
    report = extract_amber(pages(), SHA)
    wrong = fix(1, "usage_charge", "99.00")
    bad = effective(report, [wrong])
    assert {f.state for f in bad.facts if f.key in {"usage_charge", "total_due"}} == {
        "unreconciled"
    }
    assert any(n.startswith("Needs review after your corrections") for n in bad.notices)
    assert effective(report, [wrong, fix(2, "usage_charge", None)]) == report


def test_date_correction_skips_arithmetic_and_answers_say_corrected() -> None:
    report = extract_amber(pages(), SHA)
    result = effective(report, [fix(1, "due_date", "2026-12-11")])
    assert {f.key: f.value for f in result.facts}["due_date"] == "2026-12-11"
    assert not any("totals" in n for n in result.notices[len(report.notices) :])
    text = str(answer(result, "When is this bill due?")["answer"])
    assert "2026-12-11" in text and "You corrected this figure" in text


def test_origin_bill_without_meter_charge_rechecks_and_text_bills_have_no_check() -> None:
    tables = [*charges("01 Jan 2026 to 31 Mar 2026 (90 days)", meter=False), *exports()]
    report = extract_origin(bill(tables=tables, bill_total="$313.60", owed="$313.60"), SHA)
    found = view(report, fix(1, "export_credit", "20.00"))
    assert found["export_credit"] == ("corrected", "20.00")
    assert found["meter_charge"] == ("missing", None)
    text = extract(SAMPLE)
    assert view(text, fix(1, "total_due", "1.00"))["total_due"] == ("corrected", "1.00")
