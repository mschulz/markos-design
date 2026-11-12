---
title: Vision
type: vision
status: active
version: 2.0
owner: Mark Schulz
created: 2026-11-12
updated: 2026-11-12
reviewed: 2026-11-12
tags:
  - architecture
---

# Vision

## Purpose

MarkOS is an always-on personal agent operating system. It ingests information, preserves source evidence, coordinates specialist agents and helps the user understand findings and approve actions.

## Principles

1. Human judgement and scoped approval control consequential actions.
2. Knowledge is source-grounded, owner/domain-scoped and durable independently of a client device.
3. Agents communicate through versioned events and bounded interfaces; RabbitMQ distributes internal events while PostgreSQL records durable state.
4. Immutable originals and structured knowledge are distinct from generated summaries. Markdown is a portable view/export, not canonical system state.
5. Components and providers remain replaceable through explicit contracts, tests and recovery procedures.

## Product direction

The first workflow is Gmail ingestion, classification, electricity-bill processing, source-linked knowledge and notification. A shared authenticated gateway supports browser/PWA, CLI and optional messaging. Cloud scheduling and processing continue with the MacBook off.

No PARA note hierarchy, Obsidian installation, local Markdown index or native desktop application is a foundation requirement. Source originals remain accessible with provenance; structured records and exports need documented recovery and portability.

## Supersession

This revision replaces the original Markdown-canonical Personal Knowledge Engine vision following the owner's explicit architecture correction. Previous text remains in Git history. [[ADR-004 Event-driven Architecture Supersedes Legacy MarkOS]] defines precedence and boundaries; [[MarkOS v0.3 Specification]] defines the current design. Preserving legacy code does not make it the new implementation baseline.

## Success criteria

The initial workflow survives duplicate events and service restarts, preserves source traceability and domain access, surfaces uncertainty, and operates without a personal computer running. Adding an agent does not require rewriting producers. Consequential actions remain reviewable and auditable.
