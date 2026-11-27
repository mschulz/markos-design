---
title: MarkOS v0.3 Lean Bill Knowledge MVP
type: implementation-record
status: implemented-local-text-slice-pending-review
version: 1.0
owner: Mark Schulz
created: 2026-11-26
updated: 2026-11-26
reviewed: 2026-11-26
tags:
  - architecture
  - knowledge
---

# MarkOS v0.3 Lean Bill Knowledge MVP

## Decision

Bring one useful electricity-bill knowledge workflow ahead of Gmail ingestion and general classification: supply bill text, preserve the original, inspect extracted figures and ask a question with source evidence. The owner authorized this change on 2026-11-26 after the M1 control-plane preview. This first working slice is K1-A, for local sample-data review; it is not the full Knowledge Base or production acceptance.

The first slice accepts pasted UTF-8 text, including selectable text copied from a bill, and supplies a clearly invented sample. It does not accept a PDF or scan yet. This lets product feedback focus on the bill summary and grounded answers before adding document parsers, OCR, Gmail or model costs.

## What the user can do

Open **Electricity bills**, choose **Try sample bill** or paste bill text, then **Save bill**. Inspect the period, electricity used, usage/supply charges, export credit and amount due. Every recognized figure shows its exact original line. Ask **How much do I owe?**, **How much electricity did I use?**, **What dates does this bill cover?** or **What are the charges?**. The answer repeats recorded figures and cites their source lines. **Show original text** reveals numbered lines and its fingerprint. Saved bills can be reopened after refreshing or signing in again.

Missing, malformed and conflicting values are marked **Needs review** and never become confident answers. Unsupported questions receive an explicit limitation rather than a generated guess. This slice uses deterministic extraction and answers, not an LLM or general agent. There is no payment, savings claim, external disclosure or automatic action.

The supported text labels are intentionally narrow and documented in `knowledge/bills.py`. Dates use `YYYY-MM-DD to YYYY-MM-DD`. Usage is in kWh; currency is only labelled AUD when the text explicitly says `Currency: AUD`. Dollar signs alone do not establish AUD. The arithmetic notice compares listed usage plus supply minus export credit against amount due; it explicitly does not verify unlisted charges, tax or prior balances. An unrecognized layout needs review, not silent inference.

## Architecture and authority

This follows [decisions](decisions.md), [Vision](vision.md) and [MarkOS v0.3 Specification](specification.md). It adds no dependency on the archived application, PARA or Obsidian. GitHub remains canonical for project artifacts; bill originals, private data and sign-in material stay outside Git.

- The existing authenticated shared HTTPS gateway serves the bill page and API. Import requires the operator role; viewers may inspect and ask. Each SQL entry point independently checks the current session and owner/domain. Existing browser CSRF, request leases, rate and byte/time bounds remain in force.
- Additive migration 0014 stores the source fingerprint, bill identity/name and extracted facts/provenance in PostgreSQL. Preserve migrations 0001–0013. Raw originals are separate objects, not canonical Markdown or database message bodies.
- `BillObjects` is an explicit **local object-storage adapter**, with a private owned directory, server-generated owner/domain/hash paths, no symlink reads, atomic immutable writes, digest verification and at most 100 originals per scope. Identical text in the same scope returns the same bill; identical text in another scope grants no shared access.
- Import and answers are bounded synchronous human-initiated operations. RabbitMQ remains the internal event bus for the existing runtime; this slice does not add bill events or claim an asynchronous ingestion agent. Native Gmail can later feed the same source/fact boundary after its own review.
- Only an explicitly constructed `SyntheticBillApiConfig` plus a private `BillObjects` adapter enables the page and routes. Existing API profiles stay bill-disabled. The process configuration opts in with a gateway-only `bill_objects_path`. No endpoint changes this setting.

Input is bounded to 16 KiB UTF-8, 200 lines, 512 characters per line and a 120-character name. Questions are at most 500 characters. Saved bill lists are bounded by the 100-original scope limit. All model/schema limits, original hashes and current-access checks are server-side. Originals are not logged or sent to a provider. Missing/corrupt originals and stored extraction that no longer matches the versioned source evidence block report/answer reads.

An object may be committed before a database failure or access revocation. It remains private and counts toward the file quota; retrying identical text can recover the same object without overwriting it. There is no automatic deletion/cleanup or personal-data recovery promise in this slice. A source record is not returned before metadata succeeds. Future lifecycle work must coordinate database and object backups, deletion and recovery before personal-data enablement.

## Review and run

No existing preview, account, vault or deployment is changed by this PR. Use a **fresh** owned synthetic project/check-out and the existing M1-G instructions, adding `--bills` to the prepare step:

```sh
# From the repo root, in an isolated control/tools/browser environment.
python -m tests.control_process.support prepare --confirm-synthetic --bills
# Continue the normal M1-G dependency/bootstrap/build/start steps for that fresh project.
```

The explicit prepare command refuses an existing preview folder. Bootstrap records `bill_objects_path=/var/lib/markos/bills` only for the opted-in gateway. The optional image owns a private directory at that path; the Compose gateway alone mounts the `bill-objects` named volume. Other agents/fixtures receive neither the setting nor the volume. No startup migration is introduced. Apply 0014 explicitly through the migration role before updated gateway readiness can pass.

For focused automated review, use an explicitly disposable PostgreSQL DSN with the existing integration fixtures:

```sh
uv run --no-sync pytest -q tests/test_bills.py
uv run --no-sync pytest -q tests/control_api/test_bills.py tests/browser/test_bills.py
```

These tests create/drop their own databases; never target the running local preview or a shared/production database. Required hosted CI retains the existing contract, broker, browser, process, wheel and restore gates. The packaged-preview job explicitly opts in with `--bills`, asks the sample amount question in the browser, and reopens the original after gateway restart and a fresh SQL restore against the retained source-object volume. This tests source accessibility, not an independent backup/restore of lost object storage. No production/container deployment is authorized.

## Acceptance for K1-A

1. A sample bill saves and reopens with exact original UTF-8 bytes and a matching fingerprint. Reimporting identical text does not create another bill.
2. The summary identifies the sample's period, 420 kWh and AUD 148.50 with exact source lines; the amount question cites line 9.
3. Missing/conflicting/malformed figures and unsupported questions produce explicit uncertainty and no unsupported citations.
4. Another owner/domain cannot read source, report or answer; a viewer cannot import; direct gateway table reads and missing browser CSRF are denied.
5. Corrupt/missing originals block answers; the local object adapter rejects public/Git directories and symlink files. Concurrent duplicates create one immutable original.
6. A narrow-screen browser can load the sample, save, read a cited answer, show original text and reopen after reload.
7. Existing profiles remain bill-disabled; archived code and migration hashes remain unchanged.

## K1-B plan: PDF bills from one supplier

Status: implemented. The Amber extractor is in `knowledge/amber.py`, and the PDF path, upload route, storage and web upload control are in place. The owner chose these on 2026-11-26.

### Decisions

- **One supplier first: Amber Electric.** Other suppliers come later, each with its own profile.
- **PDF library: pypdf**, pinned in the optional `control` extra only. It is pure Python with a BSD licence. Text extraction cannot read scanned images ([pypdf documentation](https://pypdf.readthedocs.io/en/stable/user/extract-text.html)), so scans and OCR stay out of scope.
- **Real bills never enter Git or any external service.** The owner keeps them in a local folder that Git ignores. Tests use invented fixtures that copy the layout.

### What a real Amber bill looks like

Four owner-supplied bills (July to October 2026) were read locally. They share one layout. It is a 7-page PDF made by Chromium, not a scan, and pypdf extracts its text. Page 2, "Understand your bill", holds every summary figure:

- Billing period as two day-first dates, for example `01/10/2026  31/10/2026`.
- Usage in kWh with its rate and cost, network daily supply, network demand, the Amber monthly subscription and GST, then a charges total.
- Solar exports in kWh with their credit, vouchers and concessions and GST, then a credits total.
- A bill total. Charges total minus credits total equals the bill total exactly.

Page 1 shows "Total Owed" and a due date. Total Owed can include unpaid earlier bills, so it is a separate fact from the bill total. Pages 3 to 5 give line-item detail. Pages 6 and 7 are help text.

Extraction quirks to handle:

- Some amounts come out letter-spaced, for example `$ 5 7 . 1 5`.
- Amber's font maps `(`, `)` and the date dash to private-use code points (U+E081, U+E082, U+E089). They look missing in a terminal. The extractor turns them into spaces before matching, and citations keep the original line.
- The subscription line is `Amber Membership` on older bills and `Amber Monthly Subscription` on newer ones.
- The same charges/credits/bill amount header repeats on pages 2 to 5. Citations must point to the page 2 summary.

The bill also holds personal data: name, address, account number, NMI, meter numbers, invoice number and payment reference. K1-B stores none of these as facts, and fixtures use invented values.

### Design

1. **Read the PDF in isolation.** `knowledge/pdf.py` runs pypdf in a child process with a 15-second deadline, a 10-second CPU limit and a 512 MiB memory limit (Linux only), and at most two run at once. It returns the text of each page or a bounded error code. A parser crash cannot stop the gateway. A socket guard stops accidental network use, but it is not an OS sandbox.
2. **Supplier profiles.** The Amber profile matches only when the text names Amber Electric. It reads the page 2 summary and cites each figure by page and line. A PDF from any other supplier is rejected with `bill-pdf-unsupported-supplier` and is not stored.
3. **More facts.** Add demand charges, subscription, GST, charges total, credits total, total owed and due date to the six K1-A facts. Replace the partial arithmetic notice with a full check: charges total minus credits total must equal the bill total, and each total must equal the sum of its lines.
4. **Upload route.** `POST /api/v1/bills/pdf?owner_id=…&domain=…&name=…` takes the raw PDF bytes as `application/pdf`, with a limit of 2 MiB and 20 pages. Only this route gets a larger body limit, and only when bills are enabled. All other routes keep 64 KiB. The same operator role, CSRF and scope checks apply.
5. **Storage.** `BillObjects` stores the original PDF as an immutable `.pdf` object. Migration 0015 lets `bill_records` hold Amber reports. PostgreSQL stores the facts and citations, not the page text. Every read parses the original again and checks that the facts still match, as K1-A does for text. This costs about 0.4 seconds per read for a 7-page bill.
6. **Web page.** A PDF upload control sits below the paste box. Citations show page and line, and the original view shows page text by page and line.

### Tests

- Invented Amber page text that reproduces the quirks above, including letter-spaced amounts and missing characters.
- One small generated PDF that runs the full path: upload, isolated parse, store, extract, answer and reopen.
- Limits: oversized, too many pages, encrypted, malformed and non-PDF input are each rejected with a clear error.
- An Amber bill whose totals do not add up shows **Needs review** and no confident answer.

### Still open

- The four sample bills have no unpaid earlier balance, credit bill or estimated read. The extractor flags these for review, and tests use invented examples.
- The four sample bills are about 300 KiB and 7 pages each, well inside the 2 MiB and 20-page limits.

### Origin Energy profile

Origin was the previous supplier. Most old bills are in Gmail, so this profile lets them be read once Gmail ingestion (M2) exists.

- pypdf puts each table cell on its own line: label, units, price, then amount.
- A quarter with a price change has one charges table and one exports table per rate period. The state rebate is split the same way.
- The billing period line can wrap onto a second line.
- Page 1 shows "Debited on" a date instead of a due date. The profile stores it as the due or debit date.
- The four Origin samples (Feb–Nov 2025) reconcile. None has a balance brought forward or an estimated read. The newest has no solar meter charge, so that fact is "missing".

## K1-C plan: corrections, history and checked arithmetic

Status: approved. K1-C1 (corrections) is implemented in `knowledge/corrections.py` and migration 0017. K1-C2 (history) is implemented in `knowledge/history.py` and migration 0018. K1-C3 (rules, acceptance examples and coverage) is implemented: see "Bill arithmetic rules" below. The owner approved merging K1-C3 on 2026-11-26. Owner decisions are under "Decisions".

K1-C makes stored bills trustworthy over time. The owner can correct a figure without losing what the extractor read, compare bills across suppliers, and rely on written arithmetic rules backed by approved examples.

### Starting point

- `bill_records` holds one immutable report per original (migration 0014). Every read parses the original again and checks the report. Nothing can change a stored figure.
- Three extractors exist: `bill-text-v1` (6 facts), `amber-pdf-v1` (15) and `origin-pdf-v1` (13). They share some fact keys but not all. Amber has `charges_gst` and `credits_gst`. Origin has `gst_included` and `brought_forward`.
- Answers come from a fixed question list, one bill at a time.
- Money figures that fail an arithmetic check are `unreconciled`, and answers do not repeat them.

### Design

1. **Corrections stay separate from extraction.** A new append-only table `bill_corrections` holds the bill, fact key, new value, a short required reason, who made it and when. The stored report never changes, and the original-recheck still runs on every read.
   - The effective value of a fact is the latest correction, or the extracted value if there is none.
   - Answers and the web page show both: "Corrected by you on <date>: $X (bill shows $Y, page 2 line 27)".
   - A correction can be withdrawn by a later correction back to the extracted value. Nothing is deleted.
   - After a correction, the profile's arithmetic checks run again on the effective values. If they pass, the money facts show "reconciled with corrections". If they fail, they stay unreconciled.
   - Only the operator role (the `chat` permission, as for uploads) can correct. Viewers see corrections.
2. **One shared fact vocabulary for history.** A small mapping turns each extractor's facts into common figures: period start and end, days, usage kWh, export credit, bill total (GST included), amount owed and balance brought forward. Amber's GST lines are added into its totals, so all totals mean "including GST". Supplier-specific facts stay on the bill page only.
3. **History view and questions.** A bills history page and `GET /api/v1/bills/history` list bills in period order, across suppliers, with:
   - total, kWh, cost per day and kWh per day, because Amber bills monthly and Origin quarterly;
   - gaps or overlaps between billing periods, shown as notices;
   - unreconciled figures shown as "not established", never as numbers.
   New fixed questions: "How much electricity did I use each period?" and "How has my bill changed?". Each answer cites the bills it uses.
4. **Written arithmetic rules.** A "Bill arithmetic rules" section in this document states, for each profile:
   - GST: whether amounts include it and which lines carry it;
   - earlier balances: bill total versus amount owed, and how a balance brought forward is shown;
   - export credits: stored as a positive credit and subtracted from charges;
   - tariff structures: split-rate quarters, controlled load, demand charges and subscriptions.
   Each rule names the test that enforces it.
5. **Approved acceptance examples.** A set of invented bills, one per rule and edge case, each with its expected facts and answers. The owner approves the set before K1-C claims checked arithmetic. Cases include an unpaid earlier balance, a credit bill (negative total), an estimated read and a split-rate quarter.
6. **Measured extraction coverage.** A local script runs every profile over the owner's private sample folder and prints counts only: bills read, facts found, missing, unreconciled. It never prints values, and its output is not committed. This gives a real accuracy figure before Gmail brings in many more bills.

### Delivery

Three PRs, each reviewed and merged separately:

1. **K1-C1 corrections:** migration 0017 (corrections table and the 500-bill limit), correction API, effective values, re-checked arithmetic, and correction controls on the bill page.
2. **K1-C2 history:** shared vocabulary, history route and page, and the two new questions.
3. **K1-C3 rules and acceptance:** the rules section, the approved examples as tests, and the coverage script.

### Tests

- A correction never changes the stored report, and the original-recheck still passes.
- A withdrawn correction restores the extracted value. The full correction history stays readable.
- Correcting a wrong figure can make a bill reconcile, and a bad correction makes it unreconciled.
- Viewers cannot correct. CSRF and scope checks apply, as on other bill writes.
- History orders mixed Amber and Origin bills by period, finds gaps and overlaps, and never shows an unreconciled figure as a number.

### Decisions

The owner approved these on 2026-11-26.

1. **Who can correct:** only the operator role, with a required reason of up to 200 characters.
2. **What can be corrected:** any fact, including period and due date.
3. **History:** one combined history across Amber and Origin, with the supplier shown on each row.
4. **Acceptance examples:** the four edge cases above plus one ordinary bill per profile. The owner approves the expected answers before C3 merges.
5. **Bill limit:** raised from 100 to 500 per scope in migration 0017 (K1-C1). Amber bills monthly, so 10 years is 120 bills. 500 holds about 40 years of monthly bills.

### Out of scope

Deleting bills, retention and privacy policy, a hosted object store, Gmail ingestion (M2), and model-written answers. These stay unresolved, as listed below.

## K1-D: bill deletion

Deletion follows the decision *Deletion beats retention and leaves only a tombstone*.

1. `delete_bill` removes the bill record and its corrections in one transaction. It keeps a tombstone with the SHA-256, bill ID, file suffix and Gmail message pointer, and writes an audit row. It needs the same authority as a correction.
2. The gateway then erases the original from the object store and marks the tombstone erased. A missing object counts as erased.
3. If erasure fails, the response is `503 bill-erasure-pending`. Repeating the delete retries it, and also erases up to 20 other pending originals.
4. While erasure is pending, a manual import of the same original is refused, so an erasure never removes a new bill's original. After erasure, a manual re-import is allowed.
5. The mail worker never imports a deleted Gmail bill again. It checks the tombstone before it stores a PDF. If a deletion lands during an import, it erases the PDF it stored.

Out of scope: mail-record deletion and backup expiry. A backup taken before deletion still holds the bill until it expires.

## Bill arithmetic rules

These rules hold for every stored report and for the effective view after corrections. Each rule names the tests that enforce it. Acceptance examples are in `tests/test_acceptance.py`. The owner approves their expected answers, and changes need the owner's approval.

### GST

- **Amber:** charge lines exclude GST. One "GST – 10%" charge line carries it, and the charges total includes that line. Credits have their own GST line inside the credits total. Tests: `test_amber.py::test_reads_every_summary_figure_with_page_citations`, `test_acceptance.py::test_ordinary_amber_bill`.
- **Origin:** every amount includes GST. "GST included in total" is stored for reference and is not added again. Tests: `test_origin.py::test_one_rate_quarter_reconciles_with_cited_cells`, `test_acceptance.py::test_ordinary_origin_bill`.
- **History:** every bill total includes GST, for both profiles. Test: `test_history.py::test_mixed_suppliers_in_period_order_with_per_day_figures`.

### Totals

- **Amber:** charge lines add up to the charges total. Credit lines add up to the credits total. The charges total minus the credits total equals the bill total. Tests: `test_amber.py::test_totals_that_do_not_add_up_are_unreconciled`, `test_amber.py::test_unknown_summary_line_blocks_confident_totals`.
- **Origin:** each table's rows add up to that table's total. The charges total minus export credits and other credits equals the bill total. Tests: `test_origin.py::test_mismatch_or_unknown_row_marks_money_unreconciled`.
- If any total fails, every money figure on that bill is "unreconciled" and answers say "not established". Dates and usage are still reported.

### Earlier balances

- **Bill total** is the cost of this period. **Amount owed** (Amber "Total owed", Origin "Amount due") also holds any earlier balance.
- **Origin:** the bill total plus the balance brought forward equals the amount due. A positive balance adds a notice. A credit balance is negative and adds the notice "The amount due is reduced by a credit from an earlier bill." Tests: `test_origin.py::test_balance_forward_is_added_and_flagged`, `test_origin.py::test_credit_brought_forward_keeps_its_sign`, `test_acceptance.py::test_unpaid_earlier_balance`.
- **Amber:** the bill shows no balance line. If the total owed differs from the bill total, both are stored and a notice says so. They are never merged. Tests: `test_amber.py::test_earlier_balance_is_flagged_not_merged`, `test_acceptance.py::test_unpaid_earlier_balance`.

### Export credits and credit bills

- Export credits and rebates are stored as positive amounts and subtracted from charges. Origin prints them with a minus sign. Amber lists them under credits. Tests: `test_origin.py::test_credit_brought_forward_keeps_its_sign`, `test_acceptance.py::test_ordinary_origin_bill`.
- Bill totals, amounts owed and balances keep their sign. A credit bill has a negative total. Test: `test_acceptance.py::test_credit_bill_with_negative_total`.
- Corrections accept a negative value only for the bill total, amount owed and balance brought forward. Tests: `test_corrections.py::test_values_are_normalised_or_rejected`.

### Estimated reads and unknown lines

- **Origin:** only "This bill is based on an actual reading" is accepted. An estimated read, or any line the profile does not know, makes every money figure unreconciled. Tests: `test_origin.py::test_mismatch_or_unknown_row_marks_money_unreconciled`, `test_acceptance.py::test_estimated_read_needs_review`.
- **Amber:** any unknown summary line makes the totals unreconciled. Test: `test_amber.py::test_unknown_summary_line_blocks_confident_totals`.

### Tariff structures

- **Split-rate quarters (Origin):** a price change gives one charges table and one exports table per rate period. All tables are added together and each is checked against its own total. Tests: `test_origin.py::test_split_rate_quarter_adds_both_tables`, `test_acceptance.py::test_split_rate_quarter`.
- **Controlled load (Origin):** controlled-load usage and its daily supply are added to the usage and supply charges. Usage kWh includes controlled load. Test: `test_origin.py::test_one_rate_quarter_reconciles_with_cited_cells`.
- **Solar meter charge (Origin):** optional. A bill without it shows "missing", not $0.00, and still reconciles. Test: `test_origin.py::test_bill_without_solar_meter_charge_still_reconciles`.
- **Demand charges and subscriptions (Amber):** each is its own charge line. The subscription may be called "Monthly Subscription" or "Membership". Test: `test_amber.py::test_reads_every_summary_figure_with_page_citations`.

### Measured coverage

`scripts/bill_coverage.py FOLDER` runs every profile over a private folder of PDF bills. It prints counts only: bills per profile, fact states, and bills whose total is not established. It also checks that a second parse gives the same report. Its output is not committed.

On the owner's samples on 2026-11-26: 4 Amber bills with 60 facts found, and 4 Origin bills with 51 found and 1 missing (a bill without a solar meter charge). Every bill total was established, and every report was repeatable.

## Next slices and unresolved decisions

- **K1-C review and history:** planned above in [K1-C plan](#k1-c-plan-corrections-history-and-checked-arithmetic).
- **Then M2 Gmail:** opt-in native ingestion using the existing permission/recovery contract; broad M3 classification and the remaining M4/M5 capabilities follow demonstrated value.
- Keep real bill/account enablement, hosted object-store provider, privacy/retention/deletion policy, backup/restore of source objects and extraction accuracy on representative documents explicitly unresolved. No real provider, personal bill, model or spend is needed for K1-A review.

## Related

- [MarkOS v0.3 Roadmap](roadmap.md)
- [local preview guide](local-preview.md)
- [decisions](decisions.md)
