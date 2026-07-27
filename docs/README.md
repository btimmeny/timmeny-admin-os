# Timmeny Admin OS Documentation

This directory is the version-controlled source of truth for the product vision, architecture, implementation guidance, and operating model of Timmeny Admin OS.

## Start Here

Read these documents in order:

1. [North Star](./NorthStar.md) — why Timmeny Admin OS exists
2. [Product](./Product.md) — what Timmeny Admin OS is building and why
3. [Principles](./Principles.md) — the non-negotiable design rules
4. [Architecture](./Architecture.md) — the high-level operating model
5. [Domain Model](./Domain-Model.md) — the canonical business language
6. [Roadmap](./Roadmap.md) — the current sequence of design and implementation
7. [Architecture Decisions](./adr/README.md) — accepted architectural decisions

Some of these files will be added as the design is completed. Existing implementation documentation remains in this directory alongside the product architecture library.

## Purpose

Timmeny Admin OS is an AI-native personal administrative operating system that brings order to the complexity of life.

It connects goals, commitments, decisions, communications, calendar activity, actions, evidence, and unexpected life events into one coherent administrative model.

The platform is designed to answer:

> What requires attention, why does it matter, and are current actions moving life toward the intended outcomes?

## Documentation Rules

- Git is the architectural source of truth.
- The Architecture chat is used for design and tradeoff discussion.
- Accepted decisions are reflected in the relevant document and, when significant, an ADR.
- Documents should reference one another rather than duplicate content.
- Only documents affected by a decision should be updated.
- Documentation should use progressive disclosure: this page first, deeper documents only when needed.

## Current Product Areas

The initial Life Health view is intentionally limited to:

- Career
- Marriage
- Family — Children
- Family — Extended Family
- Health
- Finance
- Travel & Vacations

Goals, objectives, actions, decisions, evidence, and progress signals exist beneath these areas.

## Current Status

The product vision and canonical architecture are being established while the existing FastAPI and Monday.com capabilities remain operational.

The immediate objective is to align the working implementation with the broader Timmeny Admin OS product model without disrupting the current service.