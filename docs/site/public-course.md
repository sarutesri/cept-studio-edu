---
title: Education course map
description: Seven private notebook lessons for learning CEPT through small OpenDSS studies.
---

<div class="cept-page-hero" markdown>

<span class="cept-kicker">Learn · Seven small studies</span>

# CEPT education course map

Start with a small calculation, inspect what the solver returns, and then see
how CEPT keeps the setup and checks with the result. The course is private
while the public release path is still under review.
The bounded claim for the lessons is `WORKFLOW_VALIDATED`; the course is not being published anonymously in this cycle.


<div class="cept-actions" markdown>
[Open the private Colab course](colab.md){ .md-button .md-button--primary }
[Run the desktop example](getting-started.md){ .md-button }
</div>

</div>

## Lessons

| Lesson | Focus | What you inspect |
| --- | --- | --- |
| `00_environment` | runtime and supported workflow | solver/runtime identity |
| `01_first_circuit_load_flow` | tiny load-flow Case | load-bus voltage |
| `02_ieee13_unbalanced` | IEEE13 feeder | three-phase bus voltage |
| `03_hosting_capacity` | PV sweep | voltage criterion and capacity bracket |
| `04_fault_study` | ground fault | solver-returned fault current |
| `05_validation_reproducibility` | saved evidence | Case/result identity and checks |
| `06_colab_tui` | Colab TUI feel | full CLI tour plus key-gated agent chat |

Every lesson is designed to be small enough to run, inspect, and question.

## What the course demonstrates

The course demonstrates solver-backed CEPT workflows with bounded example data.
It does not establish project validation, field validation, PowerFactory parity,
or correctness of a user's own network.

When adapting a lesson, keep source, units, assumptions, and missing information
visible rather than turning an example into a stronger claim than its evidence
supports.

## Access status

The canonical notebooks remain in the private repository during this review
cycle. Authorized collaborators can use them in Colab; anonymous publication
will be a separate release step after the public package and cold-start path
are ready.

For engineering claim semantics, continue to [Validation and evidence](validation-and-evidence.md).
