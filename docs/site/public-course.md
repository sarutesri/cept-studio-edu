---
title: Education course map
description: Seven public notebook lessons for learning CEPT through small, solver-backed OpenDSS studies.
---

<div class="cept-page-hero" markdown>

<span class="cept-kicker">Learn · Seven small studies</span>

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

Every lesson is small enough to run, inspect, and question. The learner-facing
path is terminal-first: Colab shows the same `cept <noun> <verb>` commands used
in a normal terminal, while direct OpenDSS/Python checks remain optional
implementation detail.

<div class="cept-course-path" markdown>

<div class="cept-course-stage" markdown>
<span class="cept-course-stage__index">01 · Foundation</span>

### Make the runtime and network visible

**00 · Environment** — confirm CEPT/OpenDSS identity and learn what a PASS can
prove.

**01 · First circuit** — run a tiny load flow and inspect the load-bus voltage.

**02 · IEEE13 unbalanced** — move to a real three-phase feeder and inspect
phase-specific voltage.

<span class="cept-course-stage__outcome">Outcome · You can run and read a bounded solver-backed study.</span>
</div>

<div class="cept-course-stage" markdown>
<span class="cept-course-stage__index">02 · Apply</span>

### Change the engineering question

**03 · Hosting capacity** — sweep PV against a declared voltage criterion and
inspect the capacity bracket.

**04 · Fault study** — run a ground fault and inspect solver-returned fault
current.

<span class="cept-course-stage__outcome">Outcome · You can see how the study definition changes what evidence is produced.</span>
</div>

<div class="cept-course-stage" markdown>
<span class="cept-course-stage__index">03 · Evidence</span>

### Keep the result connected to its trail

**05 · Reproducibility** — inspect Case/result identity, fingerprints, and
checks.

**06 · Guided Case review** — review known, missing, default, and AI-assumption
status before a separate bounded demo receipt.

<span class="cept-course-stage__outcome">Outcome · You can distinguish a completed workflow from a validated physical project.</span>
</div>

</div>

## What changes in Lesson 06

The final lesson starts closer to real engineering work: the learner may have
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
It does not establish project validation, field validation, PowerFactory parity,
or correctness of a user's own network.

When adapting a lesson, keep source, units, assumptions, defaults, AI-selected
placeholders, and missing information visible rather than turning an example
into a stronger claim than its evidence supports.

## Keep your learning trail

When you adapt a lesson, keep the source, units, assumptions, defaults,
AI-selected placeholders, and missing information visible. Run the comparison
cells when you want to see how the result was produced, and use the verification
receipt to confirm which exact artifacts were checked.

For engineering claim semantics, continue to [Validation and evidence](validation-and-evidence.md).
