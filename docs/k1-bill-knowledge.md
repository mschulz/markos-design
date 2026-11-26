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

Status: planned, not implemented. The owner chose these on 2026-11-26.

### Decisions

- **One supplier first: Amber Electric.** Other suppliers come later, each with its own profile.
- **PDF library: pypdf**, pinned in the optional `control` extra only. It is pure Python with a BSD licence. Text extraction cannot read scanned images ([pypdf documentation](https://pypdf.readthedocs.io/en/stable/user/extract-text.html)), so scans and OCR stay out of scope.
- **Real bills never enter Git or any external service.** The owner keeps them in a local folder that Git ignores. Tests use invented fixtures that copy the layout.

### What a real Amber bill looks like

One owner-supplied bill was read locally. It is a 7-page PDF made by Chromium, not a scan, and pypdf extracts its text. Page 2, "Understand your bill", holds every summary figure:

- Billing period as two day-first dates, for example `01/10/2026  31/10/2026`.
- Usage in kWh with its rate and cost, network daily supply, network demand, the Amber monthly subscription and GST, then a charges total.
- Solar exports in kWh with their credit, vouchers and concessions and GST, then a credits total.
- A bill total. Charges total minus credits total equals the bill total exactly.

Page 1 shows "Total Owed" and a due date. Total Owed can include unpaid earlier bills, so it is a separate fact from the bill total. Pages 3 to 5 give line-item detail. Pages 6 and 7 are help text.

Extraction quirks to handle:

- Some amounts come out letter-spaced, for example `$ 5 7 . 1 5`.
- pypdf drops some characters, such as `(` before `excl GST` and the dash between the two period dates.
- The same charges/credits/bill amount header repeats on pages 2 to 5. Citations must point to the page 2 summary.

The bill also holds personal data: name, address, account number, NMI, meter numbers, invoice number and payment reference. K1-B stores none of these as facts, and fixtures use invented values.

### Design

1. **Read the PDF in isolation.** A child process runs pypdf with time, memory and page limits and no network access. It returns the text of each page or a bounded error. A parser crash cannot stop the gateway.
2. **Supplier profiles.** The Amber profile matches only when the text names Amber Electric. It reads the page 2 summary and cites each figure by page and line. An unknown supplier gets **Needs review**, as K1-A does for an unknown layout.
3. **More facts.** Add demand charges, subscription, GST, charges total, credits total, total owed and due date to the six K1-A facts. Replace the partial arithmetic notice with a full check: charges total minus credits total must equal the bill total, and each total must equal the sum of its lines.
4. **Upload route.** `POST /api/v1/bills/pdf` takes the raw PDF bytes, with a limit of 2 MiB and 20 pages. Only this route gets a larger body limit. All other routes keep 64 KiB. The same operator role, CSRF and scope checks apply.
5. **Storage.** `BillObjects` stores the original PDF as an immutable object, as it does for K1-A text. PostgreSQL stores the extracted page text, facts and citations in an additive migration. Migrations 0001 to 0014 stay unchanged.
6. **Web page.** Add a PDF upload control next to the paste box. The original view shows page text by page and line.

### Tests

- Invented Amber page text that reproduces the quirks above, including letter-spaced amounts and missing characters.
- One small generated PDF that runs the full path: upload, isolated parse, store, extract, answer and reopen.
- Limits: oversized, too many pages, encrypted, malformed and non-PDF input are each rejected with a clear error.
- An Amber bill whose totals do not add up shows **Needs review** and no confident answer.

### Open before implementation

- One bill shows one layout. Two more Amber bills from other months would show what varies, such as estimated reads, an unpaid earlier balance or a credit bill.
- The upload size and page limits are first guesses. Check them against real bills.

## Next slices and unresolved decisions

- **K1-B document input:** text-based PDF upload for one supplier. See [K1-B plan](#k1-b-plan-pdf-bills-from-one-supplier).
- **K1-C review and history:** user-corrected facts kept distinct from extraction, sample bill comparisons, and rules for tax, previous balances, export credits and tariff structures. Approve acceptance examples before claiming bill arithmetic validation.
- **Then M2 Gmail:** opt-in native ingestion using the existing permission/recovery contract; broad M3 classification and the remaining M4/M5 capabilities follow demonstrated value.
- Keep real bill/account enablement, hosted object-store provider, privacy/retention/deletion policy, backup/restore of source objects and extraction accuracy on representative documents explicitly unresolved. No real provider, personal bill, model or spend is needed for K1-A review.

## Related

- [MarkOS v0.3 Roadmap](roadmap.md)
- [local preview guide](local-preview.md)
- [decisions](decisions.md)
