---
title: <Document Title>
type: architecture
status: draft
version: 1.0
owner: Mark Schulz
created: <YYYY-MM-DD>
updated: <YYYY-MM-DD>
reviewed: <YYYY-MM-DD>
tags:
  - architecture
---

# <Document Title>

## Decision

State the decision itself, plainly, in the first paragraph. A reader who
stops here should already know what was decided, not just the topic.

## Motivation

Why this decision, and why now. What problem it solves, what it would cost
not to decide this.

## <Domain-Specific Sections>

Whatever detail the decision actually needs — options considered, mechanisms,
data, a walked-through example. Name these sections for their content, not
generically ("Component Model", "Migration Order", "Audit Findings" — not
"Details").

## Consequences

### Benefits

What gets better because of this decision.

### Costs

What gets harder, or what's given up. A decision with no costs listed
usually means the costs weren't looked for.

## Related

- [[MarkOS Specification]]
- Other documents this one depends on or is depended on by.

---

Notes on using this template:

- Match the frontmatter fields exactly — `type`, `status` (`draft` /
  `accepted` / `evaluated`, matching what's actually true), and `tags` are
  read by other documents and by anyone scanning the vault, not decoration.
- Prefer one focused document per decision over one document covering
  several unrelated decisions — this repo's practice has been to consolidate
  *related* decisions into `MarkOS Specification.md` rather than spawn many
  small files, but a genuinely new, separable decision still gets its own
  document.
- Delete this notes section before publishing the document you create from
  this template.
