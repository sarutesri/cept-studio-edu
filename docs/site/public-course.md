---
title: Education course map
description: Eight public notebook lessons for learning CEPT through small, solver-backed OpenDSS studies.
---

<div class="cept-page-hero" markdown>

<span class="cept-kicker">Learn · Eight small studies</span>

# CEPT education course map

Eight Colab notebooks: define a network, run OpenDSS, and inspect the evidence.
Concise instructions and labelled step icons guide each study.

The lessons use the bounded claim `WORKFLOW_VALIDATED`. They show a reproducible
teaching workflow; they do not establish project validation, field validation,
or PowerFactory parity.

<div class="cept-actions" markdown>
[Open the Colab course](colab.md){ .md-button .md-button--primary }
[Run the desktop example](getting-started.md){ .md-button }
</div>

</div>

## Course path

Follow four stages: start, build, apply, and trust. Notebook cells use the same
`cept <noun> <verb>` commands as a terminal.

<div class="cept-course-path" markdown>

<div class="cept-course-stage" markdown>
<span class="cept-course-stage__index">Start</span>

### Check your setup, then see why the voltage base matters

**00 · Check your setup** — is the OpenDSS runtime ready, and what may a passing
check claim?

**01 · Same feeder, two voltage bases** — compare recorded outputs from an
omitted and a declared downstream voltage base: 0.316 pu versus 0.948 pu.

<span class="cept-course-stage__outcome">Outcome · See that a converged solve can still read a different voltage base.</span>
</div>

<div class="cept-course-stage" markdown>
<span class="cept-course-stage__index">Build</span>

### Describe a network, then meet unbalance and missing data

**02 · Build your first network** — define the IEEE 4-node feeder and inspect
its single-line diagram.

**03 · Unbalanced phases** — inspect each phase of IEEE13.

**04 · When data is missing** — separate source data, missing inputs, and
approved resolutions from the bundled demonstrator.

<span class="cept-course-stage__outcome">Outcome · Describe a network as data and keep missing inputs visible.</span>
</div>

<div class="cept-course-stage" markdown>
<span class="cept-course-stage__index">Apply</span>

### Ask two planning questions

**05 · Solar hosting capacity** — sweep PV against a declared voltage limit.

**06 · Short-circuit current** — read solver-returned fault current, not a
protection acceptance decision.

<span class="cept-course-stage__outcome">Outcome · Read results against declared study criteria.</span>
</div>

<div class="cept-course-stage" markdown>
<span class="cept-course-stage__index">Trust</span>

### Trace a result back to its inputs

**07 · Trace a result** — inspect Case fingerprints, artifact hashes, and
verification checks.

<span class="cept-course-stage__outcome">Outcome · Tell workflow evidence apart from project validation.</span>
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
