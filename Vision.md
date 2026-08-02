---
title: Vision
type: vision
status: active
version: 1.0
owner: Mark Schulz
created: 2026-08-02
updated: 2026-08-02
reviewed:
tags:
  - vision
  - architecture
---

# Vision

## Purpose

MarkOS exists to augment human thinking while preserving complete ownership of knowledge.

Its purpose is to provide a long-lived Personal Knowledge Engine that organises, connects and makes knowledge accessible independently of any editor, database, AI model or software platform.

## Vision Statement

Knowledge should outlive the tools used to create, edit and consume it.

MarkOS separates knowledge from applications so that the same body of knowledge can be explored through multiple interfaces while remaining stored in open, human-readable formats.

## Core Objectives

MarkOS will:

- preserve knowledge in plain Markdown;
- model knowledge independently of storage technologies;
- maintain rebuildable indexes for fast discovery;
- provide intelligent assistance without removing human control;
- support multiple editors and interfaces;
- remain extensible through adapters rather than modifications to the core.

## Design Philosophy

MarkOS is built around five principles.

1. Knowledge is primary.
2. Markdown is the canonical representation of knowledge.
3. Applications are interchangeable.
4. AI augments human judgement rather than replacing it.
5. Small, well-defined components are preferred over large integrated systems.

## Success Criteria

MarkOS is successful when:

- knowledge remains accessible regardless of the editor being used;
- new capabilities can be added without redesigning the core;
- indexes can always be rebuilt from Markdown;
- users trust the system with their long-term knowledge.

## Non-Goals

MarkOS is not intended to:

- become a proprietary knowledge store;
- replace Markdown with a custom format;
- depend on a specific editor;
- allow AI to modify knowledge without explicit human approval.

## Design Rationale

The vision defines the long-term purpose of MarkOS.

It should change very rarely. Architectural decisions, engineering practices and implementation details may evolve over time, but they should remain consistent with this vision.

## Related

- [[ADR-0000 Why MarkOS Exists]]
- [[ADR-0001 Markdown is the Source of Truth]]
- [[Engineering Charter]]
- [[Milestones]]