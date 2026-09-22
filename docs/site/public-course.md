---
title: Education course map
description: Seven public notebook lessons for learning CEPT through small, solver-backed OpenDSS studies.
---

<div class="cept-page-hero" markdown>

<span class="cept-kicker">Learn · Seven small studies</span>

# CEPT education course map

Start with a small calculation, inspect what the solver returns, then see how
CEPT keeps setup and evidence connected. The canonical lessons are developed in
`cept-studio` and released only through the reviewed public export to
`cept-studio-edu`.

The bounded claim for these lessons is `WORKFLOW_VALIDATED`. The course does not
establish project validation, field validation, or PowerFactory parity.

<div class="cept-actions" markdown>
[Open the Colab course](colab.md){ .md-button .md-button--primary }
[Run the desktop example](getting-started.md){ .md-button }
</div>

</div>

## Course path

| Lesson | Focus | What you inspect |
| --- | --- | --- |
| `00_environment` | runtime and supported workflow | solver/runtime identity |
| `01_first_circuit_load_flow` | tiny load-flow Case | load-bus voltage |
| `02_ieee13_unbalanced` | IEEE13 feeder | three-phase bus voltage |
| `03_hosting_capacity` | PV sweep | voltage criterion and capacity bracket |
| `04_fault_study` | ground fault | solver-returned fault current |
| `05_validation_reproducibility` | saved evidence | Case/result identity and checks |
| `06_colab_tui` | guided Case-information review | known/missing/default/AI-assumption status + one bounded demo receipt |

Every lesson is designed to be small enough to run, inspect, and question. The learner-facing path is terminal-first: Colab shows the same `cept <noun> <verb>` commands used in a normal terminal, while direct OpenDSS/Python checks remain optional implementation detail.

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

## Source and release model

`cept-studio` is the only development source. The public repository is produced
from the canonical revision by the positive-allowlist exporter; public notebooks
must not be developed independently in `cept-studio-edu`.

A repository-side notebook PASS is necessary evidence, but the authenticated
real-Colab cold-start review remains its own release gate.

For engineering claim semantics, continue to [Validation and evidence](validation-and-evidence.md).
