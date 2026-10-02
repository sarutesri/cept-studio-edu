---
title: Education course map
description: Eight public notebook lessons for learning CEPT through small, solver-backed OpenDSS studies.
---

<div class="cept-page-hero" markdown>

<span class="cept-kicker">Learn · Eight small studies</span>

# CEPT education course map

Start with a small calculation, inspect what the solver returns, then see how
CEPT keeps setup and evidence connected. Each lesson is designed to be run,
read, and questioned in Colab.

The lessons use the bounded claim `WORKFLOW_VALIDATED`. They show a reproducible
teaching workflow; they do not establish project validation, field validation,
or PowerFactory parity.

<div class="cept-actions" markdown>
[Open the Colab course](colab.md){ .md-button .md-button--primary }
[Run the desktop example](getting-started.md){ .md-button }
</div>

</div>

## Course path

The course is ordered by how a learner should think, not by which study is
easiest to implement. It opens on a wrong answer, then earns every later step.
The learner-facing path is terminal-first: Colab shows the same
`cept <noun> <verb>` commands used in a normal terminal, while direct
OpenDSS/Python checks remain optional implementation detail.

<div class="cept-course-path" markdown>

<div class="cept-course-stage" markdown>
<span class="cept-course-stage__index">Prologue</span>

### Set up the runtime and the claim boundary

**00 · Before you start** — confirm CEPT/OpenDSS identity and learn what a PASS
can and cannot prove.

<span class="cept-course-stage__outcome">Outcome · Every later number has a known environment and a known ceiling.</span>
</div>

<div class="cept-course-stage" markdown>
<span class="cept-course-stage__index">Phase 1 · The aha</span>

### See the failure before the theory

**01 · Why solvers lie** — an AI-written OpenDSS script converges on a wrong
model and reports 0.316 pu with no warning. The same feeder through CEPT returns
0.948 pu. Convergence is not correctness.

<span class="cept-course-stage__outcome">Outcome · You can explain why a validating bridge exists at all.</span>
</div>

<div class="cept-course-stage" markdown>
<span class="cept-course-stage__index">Phase 2 · Build it</span>

### Make your first system visible

**02 · Build your first circuit** — declare the IEEE 4-node feeder as a typed
Case and get an interactive single-line diagram without drawing a single
coordinate.

<span class="cept-course-stage__outcome">Outcome · You can turn a Case into a run and a diagram.</span>
</div>

<div class="cept-course-stage" markdown>
<span class="cept-course-stage__index">Phase 3 · Real network</span>

### Meet the messy distribution grid

**03 · A feeder is never balanced** — inspect phase-specific voltage on the
IEEE 13-node feeder instead of hiding imbalance inside one average.

**04 · When site data is incomplete** — review known, missing, default, and
AI-assumption status before a separate bounded demo receipt.

<span class="cept-course-stage__outcome">Outcome · You can read an unbalanced result and refuse to invent a missing one.</span>
</div>

<div class="cept-course-stage" markdown>
<span class="cept-course-stage__index">Phase 4 · Active grid</span>

### Ask the modern engineering questions

**05 · How much solar can it take?** — sweep PV against a declared voltage
criterion until reverse power flow pushes the feeder past the limit.

**06 · Short-circuit current** — apply a declared line-to-ground fault and read
the solver-returned current, without mistaking it for a protection decision.

<span class="cept-course-stage__outcome">Outcome · You can bound a renewable connection and read a fault result honestly.</span>
</div>

<div class="cept-course-stage" markdown>
<span class="cept-course-stage__index">Phase 5 · Trust</span>

### Prove the number afterwards

**07 · The digital receipt** — run the identical Case twice and follow the
fingerprint, artifact hashes, and verification receipt behind one exact result.

<span class="cept-course-stage__outcome">Outcome · You can distinguish a completed workflow from a validated physical project.</span>
</div>

</div>

## Real-world data in Lesson 04

Lesson 04 starts closer to real engineering work: the learner may have
incomplete information rather than a ready-made Case. It introduces the Case
information resolution ledger and three explicit policies:

- **strict** — source/derived values only;
- **assisted** — a documented default is allowed only after explicit approval;
- **exploratory** — an AI-selected assumption may also be used after explicit approval, for a demonstrator only.

Missing required data remains unresolved and blocks the user-case solve. The
lesson then runs a separate bundled IEEE13 demo so the solver output remains
truthful and traceable.

## What the course demonstrates

The course demonstrates solver-backed CEPT workflows with bounded example data.
For this public course, `sarutesri/cept-studio-edu` is the only development source;
the canonical private CEPT Studio repository remains the product source of truth.
It does not establish project validation, field validation, PowerFactory parity,
or correctness of a user's own network. A real-Colab cold-start review is still
required before the hosted learning path is called accepted.

When adapting a lesson, keep source, units, assumptions, defaults, AI-selected
placeholders, and missing information visible rather than turning an example
into a stronger claim than its evidence supports.

## Keep your learning trail

When you adapt a lesson, keep the source, units, assumptions, defaults,
AI-selected placeholders, and missing information visible. Run the comparison
cells when you want to see how the result was produced, and use the verification
receipt to confirm which exact artifacts were checked.

For engineering claim semantics, continue to [Validation and evidence](validation-and-evidence.md).
