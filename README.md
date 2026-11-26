# MarkOS

MarkOS is a personal knowledge base and agent system. It keeps source documents,
extracts facts from them, and answers questions with evidence from the source.

## Current focus: electricity bills

The first useful workflow is: supply an electricity bill, keep the original,
extract the key figures, inspect them, and ask questions with cited answers.

Working now (K1-A, synthetic data only): paste bill text or load a sample bill,
see the extracted figures with the source line for each, and ask one of four
questions. Figures that are missing or conflict are marked **Needs review**.
Next (K1-B): text-based PDF bills with page citations.

See [the K1 bill workflow](MarkOS%20v0.3%20Lean%20Bill%20Knowledge%20MVP.md) and
[the roadmap](MarkOS%20v0.3%20Roadmap.md).

## How it is built

- **PostgreSQL** holds all durable state, extracted facts and their provenance.
- **Object storage** holds the original documents, unchanged.
- **RabbitMQ** is the internal event bus.
- **Markdown** is only an export, never the source of truth.
- Hosting target: DigitalOcean with Docker Compose, and Supabase.

Real accounts, Gmail, AI models and production hosting are not enabled yet.

## Repository layout

| Path | Contents |
|---|---|
| `services/runtime/` | The application (`markos_runtime`), its tests and its dependency lock |
| `infrastructure/` | Docker Compose files, database migrations, RabbitMQ definitions |
| `config/runtime/` | Example configuration and generated JSON schemas |
| `docs/decisions.md` | The design decisions and the reasons for them |
| `scripts/` | The documentation link check |

## Development

Run the checks from `services/runtime/`; its [README](services/runtime/README.md)
has the details. Database and broker tests need disposable local services and never
touch a real database.

Documentation check: `python3 -B scripts/check_docs.py` (fails on broken local links).
