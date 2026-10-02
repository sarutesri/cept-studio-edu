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

Follow five phases: model integrity, model construction, network conditions,
applied studies, and evidence/reproducibility. Notebook cells use the same
`cept <noun> <verb>` commands as a terminal.

<div class="cept-course-path" markdown>

<div class="cept-course-stage" markdown>
<span class="cept-course-stage__index">Environment &amp; scope</span>

### Prepare the runtime

**00 · Environment and study scope** — check CEPT/OpenDSS and claim limits.

<span class="cept-course-stage__outcome">Outcome · Identify the runtime and evidence scope.</span>
</div>

<div class="cept-course-stage" markdown>
<span class="cept-course-stage__index">Phase 1 · Model integrity</span>

### Inspect model assumptions

**01 · Convergence and model integrity** — compare recorded feeder outputs
with omitted and declared downstream voltage bases: 0.316 pu versus 0.948 pu.

<span class="cept-course-stage__outcome">Outcome · Distinguish convergence from correctness.</span>
</div>

<div class="cept-course-stage" markdown>
<span class="cept-course-stage__index">Phase 2 · Model construction</span>

### Build a typed network

**02 · Typed network modelling** — define the IEEE 4-node feeder and inspect
its single-line diagram.

<span class="cept-course-stage__outcome">Outcome · Connect a Case, run, and diagram.</span>
</div>

<div class="cept-course-stage" markdown>
<span class="cept-course-stage__index">Phase 3 · Network conditions</span>

### Evaluate phases and input quality

**03 · Unbalanced feeder analysis** — inspect each phase of IEEE13.

**04 · Incomplete engineering data** — separate source data, missing inputs,
and approved resolutions from the bundled demonstrator.

<span class="cept-course-stage__outcome">Outcome · Inspect unbalance without concealing missing inputs.</span>
</div>

<div class="cept-course-stage" markdown>
<span class="cept-course-stage__index">Phase 4 · Applied studies</span>

### Study solar integration and faults

**05 · Solar hosting capacity** — sweep PV against a declared voltage limit.

**06 · Short-circuit analysis** — read solver-returned fault current, not a
protection acceptance decision.

<span class="cept-course-stage__outcome">Outcome · Interpret results against declared study criteria.</span>
</div>

<div class="cept-course-stage" markdown>
<span class="cept-course-stage__index">Phase 5 · Evidence &amp; reproducibility</span>

### Review the run evidence

**07 · Run evidence and reproducibility** — inspect Case fingerprints,
artifact hashes, and verification checks.

<span class="cept-course-stage__outcome">Outcome · Distinguish workflow evidence from project validation.</span>
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
