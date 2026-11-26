"""Origin Energy electricity bill profile: page text in, cited facts out. No PDF parsing or I/O.

pypdf puts each table cell of an Origin bill on its own line, so rows are read cell by cell.
A quarter with a price change has one charges table and one exports table per rate period.
Every table is checked against its total, and every total against the bill total. Any
mismatch or unrecognised row marks the money facts unreconciled.
"""

import re
from datetime import datetime
from decimal import Decimal

from markos_runtime.knowledge.bills import BillFacts, BillKey, Evidence, Fact, FactState

FOOTER = "Origin Energy Electricity Ltd"
LABELS: dict[BillKey, str] = {
    "period": "Billing period",
    "usage_kwh": "Electricity used",
    "usage_charge": "Usage charge",
    "supply_charge": "Daily supply charge",
    "meter_charge": "Solar meter charge",
    "charges_total": "Charges total",
    "export_credit": "Solar feed-in credit",
    "concessions": "Rebates and credits",
    "total_due": "Total for this bill",
    "gst_included": "GST included in total",
    "brought_forward": "Balance brought forward",
    "total_owed": "Amount due",
    "due_date": "Due or direct debit date",
}
MONEY: set[BillKey] = {
    "usage_charge",
    "supply_charge",
    "meter_charge",
    "charges_total",
    "export_credit",
    "concessions",
    "total_due",
    "gst_included",
    "brought_forward",
    "total_owed",
}
ROWS: list[tuple[str, BillKey]] = [
    (r"General Usage|Controlled Load [0-9] Usage", "usage_charge"),
    (r"Daily Supply(?: - Controlled)?", "supply_charge"),
    (r"Solar Meter Charge", "meter_charge"),
    (r"Solar feed-in credit", "export_credit"),
]
HEADERS = {
    *("Item", "Description", "Units", "Price", "Amount", "Date", "(incl GST, if any)"),
    "*All amounts include GST, unless otherwise specified",
    "This bill is based on an actual reading",
}
SECTIONS = {"Since your last bill", "Usage and supply charges", "Solar exports", "Credits"}
TOTALS: dict[str, BillKey] = {
    "Total charges": "charges_total",
    "Total exports": "export_credit",
    "Total credits": "concessions",
}
DATE = r"[0-9]{2} [A-Z][a-z]{2} [0-9]{4}"
UNITS = re.compile(r"(?P<n>[0-9]+(?:\.[0-9]+)?) (?P<unit>kWh|days)")
PRICE = re.compile(r"\$-?[0-9]+\.[0-9]+")


class Line:
    def __init__(self, page: int, number: int, quote: str) -> None:
        self.page, self.number, self.quote, self.text = page, number, quote, quote.strip()

    def evidence(self) -> Evidence:
        return Evidence(schema_version=1, page=self.page, line=self.number, quote=self.quote)


def money(text: str) -> Decimal | None:
    """`$1,234.56`, `-$5.47` or `$-5.47` to a Decimal, or None."""
    match = re.fullmatch(r"(-?)\$(-?)([0-9]{1,3}(?:,[0-9]{3})*\.[0-9]{2}|[0-9]+\.[0-9]{2})", text)
    if not match:
        return None
    value = Decimal(match[3].replace(",", ""))
    return -value if match[1] or match[2] else value


def day(text: str) -> str | None:
    try:
        return str(datetime.strptime(text, "%d %b %Y").date())
    except ValueError:
        return None


def is_origin(pages: list[str]) -> bool:
    return any(FOOTER in page for page in pages)


class Reader:
    """Walk the "Understand your bill" section and collect cited values."""

    def __init__(self, pages: list[str]) -> None:
        self.lines = [
            Line(p, n, quote)
            for p, page in enumerate(pages, 1)
            for n, quote in enumerate(page.splitlines(), 1)
        ]
        self.values: dict[BillKey, list[tuple[Decimal | str | None, Evidence]]] = {
            key: [] for key in LABELS
        }
        self.kwh: list[tuple[Decimal, Evidence]] = []
        self.problems: list[str] = []
        self.balance_parts: list[tuple[str, Decimal]] = []
        self.payments: list[Decimal] = []

    def at(self, i: int) -> Line | None:
        return self.lines[i] if i < len(self.lines) else None

    def amount_after(self, i: int) -> tuple[Decimal | None, Line | None]:
        line = self.at(i + 1)
        return (money(line.text) if line else None), line

    def add(self, key: BillKey, value: Decimal | str | None, line: Line | None) -> None:
        if line is not None:
            self.values[key].append((value, line.evidence()))

    def unknown(self, line: Line) -> None:
        self.problems.append(f"unrecognised line on page {line.page} line {line.number}")

    def read(self) -> None:
        texts = [line.text for line in self.lines]
        if "Understand your bill" not in texts:
            self.problems.append("the bill summary was not found")
            return
        self.header(texts.index("Understand your bill"))
        i = texts.index("Understand your bill")
        section = ""
        table: list[Decimal] = []
        while i < len(self.lines):
            line = self.lines[i]
            text = line.text
            if text == "Meter details":
                break
            if text in {"Understand your bill (continued)", "New charges and credits", ""}:
                pass
            elif text == "Understand your bill":
                i = self.period(i + 1)
                continue
            elif text in SECTIONS:
                section, table = text, []
            elif text.startswith("Billing period:") and section == "Usage and supply charges":
                i = self.skip_period(i)
                continue
            elif text in HEADERS:
                pass
            elif text in TOTALS:
                total, cell = self.amount_after(i)
                if total is None or sum(table) != total:
                    self.problems.append(f"the {section.lower()} rows do not add up to {text}")
                self.add(TOTALS[text], total, cell)
                section, table = "", []
                i += 2
                continue
            elif text in {"Your total for this bill", "GST included in total"}:
                value, cell = self.amount_after(i)
                self.add("total_due" if text.startswith("Your") else "gst_included", value, cell)
                i += 2
                continue
            elif text == "Account Balance":
                value, cell = self.amount_after(i)
                self.add("total_owed", value, cell)
                i += 2
                continue
            elif section == "Since your last bill":
                i = self.balance(i)
                continue
            elif section in {"Usage and supply charges", "Solar exports"}:
                i = self.row(i, table)
                continue
            elif section == "Credits" and re.fullmatch(DATE, text):
                value, cell = self.amount_after(i + 1)
                if value is None or cell is None:
                    self.unknown(line)
                else:
                    table.append(value)
                i += 3
                continue
            else:
                self.unknown(line)
            i += 1

    def header(self, end: int) -> None:
        """Amount due and its due or debit date on page 1."""
        for i, line in enumerate(self.lines[:end]):
            if line.text == "Amount due":
                value, cell = self.amount_after(i)
                self.add("total_owed", value, cell)
            match = re.fullmatch(rf"(?:Debited on|Due date:?|Pay by) ({DATE})", line.text)
            if match:
                self.add("due_date", day(match[1]), line)

    def period(self, i: int) -> int:
        """The bill period may wrap onto a second line: `... (92` then `days)`."""
        line = self.at(i)
        if line is None or not line.text.startswith("Billing period:"):
            self.problems.append("the billing period was not found")
            return i
        text, j = line.text, i + 1
        nxt = self.at(j)
        if not text.endswith(")") and nxt is not None and nxt.text == "days)":
            text, j = text + " days)", j + 1
        match = re.fullmatch(rf"Billing period: ({DATE}) to ({DATE}) \([0-9]+ days\)", text)
        start, end = (day(match[1]), day(match[2])) if match else (None, None)
        valid = start is not None and end is not None and start <= end
        self.add("period", f"{start} to {end}" if valid else None, line)
        return j

    def skip_period(self, i: int) -> int:
        nxt = self.at(i + 1)
        return i + 2 if nxt is not None and nxt.text == "days)" else i + 1

    def balance(self, i: int) -> int:
        """Opening balance, payments and the balance brought forward must add up."""
        line = self.lines[i]
        if line.text in {"Opening Balance", "Payments Received & Account Transfers"}:
            value, _ = self.amount_after(i)
            if value is None:
                self.unknown(line)
            else:
                self.balance_parts.append((line.text, value))
            return i + 2
        if line.text == "Balance brought forward":
            value, cell = self.amount_after(i)
            parts = dict(self.balance_parts)
            payments = sum(self.payments)
            if (
                value is None
                or len(parts) != 2
                or parts["Opening Balance"] + parts["Payments Received & Account Transfers"]
                != value
                or payments != parts["Payments Received & Account Transfers"]
            ):
                self.problems.append("the opening balance and payments do not add up")
            self.add("brought_forward", value, cell)
            return i + 2
        if re.fullmatch(DATE, line.text):
            value, cell = self.amount_after(i + 1)
            if value is None or cell is None:
                self.unknown(line)
            else:
                self.payments.append(value)
            return i + 3
        self.unknown(line)
        return i + 1

    def row(self, i: int, table: list[Decimal]) -> int:
        """Label, optional `(incl GST, if any)`, units, price, amount."""
        line = self.lines[i]
        key = next((k for pattern, k in ROWS if re.fullmatch(pattern, line.text)), None)
        j = i + 1
        nxt = self.at(j)
        if nxt is not None and nxt.text == "(incl GST, if any)":
            j += 1
        cells = [self.at(j), self.at(j + 1), self.at(j + 2)]
        units = UNITS.fullmatch(cells[0].text) if cells[0] else None
        price = PRICE.fullmatch(cells[1].text) if cells[1] else None
        amount = money(cells[2].text) if cells[2] else None
        if key is None or units is None or price is None or amount is None:
            self.unknown(line)
            return i + 1
        expected = "kWh" if key in {"usage_charge", "export_credit"} else "days"
        if units["unit"] != expected:
            self.unknown(line)
            return i + 1
        table.append(amount)
        if key == "usage_charge" and cells[0] is not None:
            self.kwh.append((Decimal(units["n"]), cells[0].evidence()))
        if key != "export_credit":  # The exports fact comes from each table's total.
            self.add(key, amount, cells[2])
        return j + 3


def fact(key: BillKey, found: list[tuple[Decimal | str | None, Evidence]], summed: bool) -> Fact:
    """Summed facts add their rows. Other facts must agree wherever they appear."""
    if summed:
        numbers = [v for v, _ in found if isinstance(v, Decimal)]
        state: FactState = (
            "invalid" if len(numbers) != len(found) else "found" if found else "missing"
        )
        value: str | None = str(abs(sum(numbers))) if state == "found" else None
    else:
        distinct = {v for v, _ in found}
        state = (
            "invalid"
            if None in distinct
            else "conflicting"
            if len(distinct) > 1
            else "found"
            if found
            else "missing"
        )
        first = found[0][0] if state == "found" else None
        value = str(abs(first)) if isinstance(first, Decimal) else first
    unit = "kWh" if key == "usage_kwh" else "" if key in {"period", "due_date"} else "AUD"
    return Fact(
        schema_version=1,
        key=key,
        label=LABELS[key],
        value=value,
        unit=unit,
        state=state,
        evidence=[e for _, e in found],
    )


SUMMED: set[BillKey] = {
    "usage_charge",
    "supply_charge",
    "meter_charge",
    "charges_total",
    "export_credit",
    "concessions",
}


def totals_problems(values: dict[BillKey, Decimal]) -> list[str]:
    """The summary arithmetic. A bill without a solar meter charge has none."""
    problems = []
    meter = values.get("meter_charge", Decimal(0))
    lines = values["usage_charge"] + values["supply_charge"] + meter
    if lines != values["charges_total"]:
        problems.append("the charge rows do not add up to the charges total")
    net = values["charges_total"] - values["export_credit"] - values["concessions"]
    if net != values["total_due"]:
        problems.append("charges minus exports and credits is not the bill total")
    if values["total_due"] + values["brought_forward"] != values["total_owed"]:
        problems.append("the amount due is not this bill's total plus the balance forward")
    return problems


def extract_origin(pages: list[str], source_sha256: str) -> BillFacts:
    reader = Reader(pages)
    reader.read()
    reader.values["usage_kwh"] = [(v, e) for v, e in reader.kwh]
    facts = [fact(key, reader.values[key], key in SUMMED | {"usage_kwh"}) for key in LABELS]
    notices = [
        "Figures are extracted from the Origin Energy bill PDF. Check them against the original.",
        "Amounts are AUD and include GST: Origin Energy is an Australian retailer.",
    ]
    values = {
        f.key: Decimal(f.value)
        for f in facts
        if f.key in MONEY and f.state == "found" and f.value is not None
    }
    problems = reader.problems
    required = MONEY - {"meter_charge"}
    if not problems and not values.keys() >= required:
        problems.append("some figures are missing or unclear, so the totals were not checked")
    if not problems:
        problems = totals_problems(values)
    if problems:
        notices.extend("Needs review: " + problem + "." for problem in problems)
        facts = [
            f.model_copy(update={"state": "unreconciled", "value": None})
            if f.key in MONEY and f.state == "found"
            else f
            for f in facts
        ]
    else:
        notices.append(
            "Every table adds up to its total, charges minus exports and credits equals the "
            "bill total, and the amount due equals that total plus the balance brought forward."
        )
        if values["brought_forward"]:
            notices.append(
                "The amount due includes a balance brought forward from an earlier bill."
            )
    return BillFacts(
        schema_version=1,
        extractor="origin-pdf-v1",
        source_sha256=source_sha256,
        facts=facts,
        notices=notices,
    )
