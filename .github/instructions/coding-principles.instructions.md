---
applyTo: '**'
---

# Coding Principles (Karpathy Skills)

Behavioural guidelines that govern how code is written in this repository. The
full canonical text is in [/CLAUDE.md](../../CLAUDE.md); this file is the
auto-applied summary. These principles cover **execution discipline** and
complement — they do not repeat — the spec-driven **Working Method** in
`.github/copilot-instructions.md`, which covers **planning discipline**.

## 1. Think Before Coding
Don't assume; don't hide confusion; surface tradeoffs. State assumptions
explicitly. If multiple interpretations exist, present them rather than picking
silently. If a simpler approach exists, say so. If something is unclear, stop and
ask.

## 2. Simplicity First
Minimum code that solves the problem, nothing speculative. No features beyond
what was asked, no abstractions for single-use code, no unrequested
configurability, no error handling for impossible scenarios. If 200 lines could
be 50, rewrite it.

## 3. Surgical Changes
Touch only what you must. Don't "improve" adjacent code, don't refactor what
isn't broken, and match existing style. Mention unrelated dead code rather than
deleting it; remove only the orphans your own changes create. Every changed line
should trace directly to the request.

## 4. Goal-Driven Execution
Define success criteria and loop until verified. Turn tasks into verifiable goals
(e.g. "fix the bug" -> "write a failing test that reproduces it, then make it
pass"). For multi-step work, state a brief plan with a verification check per
step.
