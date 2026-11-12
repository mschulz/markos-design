---
title: ADR-002 Cloud-first Deployment
type: architecture-decision
status: accepted
version: 1.0
owner: Mark Schulz
created: 2026-11-11
updated: 2026-11-11
reviewed: 2026-11-11
tags:
  - architecture
  - deployment
---

# ADR-002 Cloud-first Deployment

> Transport amendment: RabbitMQ replaces the provisional MQTT references in this historical hosting decision. [[ADR-003 RabbitMQ Internal Event Bus]] selects the bus; [[ADR-004 Event-driven Architecture Supersedes Legacy MarkOS]] selects the independent event-driven product baseline. DigitalOcean / Compose / Supabase placement remains accepted. Exact operational profiles still require review.

## Decision

Run MarkOS production on a DigitalOcean VM using Docker Compose, with Supabase PostgreSQL for durable state. The VM hosts the MQTT broker, Gmail ingestion bridge, agent workers, persistent scheduler, notification gateway and web API/control plane. The MacBook is a development and administration client only. Shutting it down, losing its network connection or closing its terminal must not interrupt production processing or scheduled jobs.

The owner approved this baseline in the [Agent PubSub Architecture conversation](chatgpt-conversation://6aefc392-d95c-83ec-b848-4a884bfa3e7c), reaffirmed in the 11 November 2026 continuation. This records an accepted hosting decision; it does not approve implementation milestones, spending, provisioning, deployment or merging this PR.

Identifier note: the earlier handoff already contains [[ADR-0002 GitHub as Canonical Project Record]]. Preserve that separate governance record and its links. This document retains the owner's requested **ADR-002** cloud decision name; use full titles when referring to either record. No historical ADR is renumbered.

## Motivation

Gmail ingestion, agent execution, watch renewal, periodic maintenance and notifications need to run while personal devices are off. A single VM and managed database provide a simple initial operational boundary, with services separable later.

## Runtime and durability

[[MarkOS Cloud Deployment Architecture]] defines placement, recovery procedures and proposed operational targets. PostgreSQL holds events, outbox/inbox, task state, schedules, run history, leases, Gmail cursors, approvals, conversations, notification attempts and knowledge records as their schemas are approved. MQTT distributes events; persistent broker data improves restart recovery but is not the authoritative event archive.

Schedules and missed-job recovery must be database-backed. Container-local cron, an in-memory timer or MacBook jobs cannot be the sole production scheduler. Every long-running Compose service requires a restart policy, health checks, dependency retry and graceful shutdown. Backups, independent monitoring and tested recovery are production readiness requirements.

This hosting decision does not settle the outstanding Markdown knowledge-authority conflict, retrieval implementation or source-object provider. Production operational state is durable in Supabase; broader knowledge contracts still require the reconciliation in [[MarkOS v0.3 Roadmap]].

## Alternatives considered

- MacBook-hosted production: rejected because shutdown and sleep interrupt jobs.
- Hybrid production that needs MacBook services, tunnels or local files: rejected for the same dependency.
- Multiple VMs or an orchestrated cluster: deferred until availability or scale justifies the operational cost.

## Consequences

### Benefits

Production is independent of personal devices. Docker Compose keeps an initial deployment understandable; managed PostgreSQL separates durable state from the compute VM.

### Costs

A single VM is always-on by intent, not highly available. VM failure interrupts all execution until recovery. Supabase, internet connectivity, Gmail and notification providers remain external dependencies. Backups, monitoring, patching, credentials recovery, capacity planning and a recurring operating budget need explicit ownership.

Region, VM sizing, Supabase plan and recovery capability, object storage, monitoring destination, costs and recovery targets require review before provisioning. No provider service is created by this decision record.

## Related

- [[ADR-0002 GitHub as Canonical Project Record]]
- [[MarkOS v0.3 Specification]]
- [[MarkOS v0.3 Roadmap]]
- [[MarkOS Cloud Deployment Architecture]]
