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

This follows [[ADR-004 Event-driven Architecture Supersedes Legacy MarkOS]], [[Vision]] and [[MarkOS v0.3 Specification]]. It adds no dependency on the archived application, PARA or Obsidian. GitHub remains canonical for project artifacts; bill originals, private data and sign-in material stay outside Git.

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
# From services/runtime, in an isolated control/tools/browser environment.
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

## Next slices and unresolved decisions

- **K1-B document input:** text-based PDF upload, page citations, parser limits/isolation and a small supported supplier set. Scans/OCR need a separate measured decision. PDF parsing is not represented as implemented here; text extraction also cannot reliably read scanned images ([pypdf documentation](https://pypdf.readthedocs.io/en/stable/user/extract-text.html)).
- **K1-C review and history:** user-corrected facts kept distinct from extraction, sample bill comparisons, and rules for tax, previous balances, export credits and tariff structures. Approve acceptance examples before claiming bill arithmetic validation.
- **Then M2 Gmail:** opt-in native ingestion using the existing permission/recovery contract; broad M3 classification and the remaining M4/M5 capabilities follow demonstrated value.
- Keep real bill/account enablement, hosted object-store provider, privacy/retention/deletion policy, backup/restore of source objects and extraction accuracy on representative documents explicitly unresolved. No real provider, personal bill, model or spend is needed for K1-A review.

## Related

- [[MarkOS v0.3 Roadmap]]
- [[MarkOS v0.3 M1-G CI and Lean Preview]]
- [[ADR-006 Control Plane Identity and Trust Boundary]]
