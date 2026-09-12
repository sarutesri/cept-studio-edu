---
title: Notebook course
description: Learn CEPT by running six small OpenDSS notebook examples in Google Colab.
---

<div class="cept-hero cept-hero--colab" markdown>

<span class="cept-status cept-status-beta">Learn · Preview</span>

## Learn by running small examples.

The notebook course is the browser-first way to explore CEPT. Each lesson shows
what you set up, what OpenDSS calculates, and what CEPT saves for inspection.
The bounded claim is `WORKFLOW_VALIDATED`; it does not validate a physical
project. Open the notebooks in Google Colab; anonymous access follows the public mirror.

<div class="cept-actions" markdown>
<a class="cept-colab-link cept-colab-link--labelled" href="https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/00_environment.ipynb" target="_blank" rel="noopener" aria-label="Open Lesson 00 in Google Colab" title="Open in Google Colab"><span class="cept-colab-mark" aria-hidden="true"></span><span>Start with Lesson 00</span></a>
[See the course map](public-course.md){ .md-button }
</div>

</div>

!!! info "Preview status"
    The teaching package runs under Apache-2.0 with a `WORKFLOW_VALIDATED` bound; it does not validate a physical project. A passed real-Colab cold-start review is still pending.


```mermaid
flowchart LR
    A[Open notebook] --> B[Set up study]
    B --> C[Run OpenDSS]
    C --> D[Inspect result]
    D --> E[Check saved evidence]
```

## Choose a lesson

| Lesson | Try this |
| --- | --- |
| **00 · Environment** | Check the runtime and what a successful run means. |
| **01 · First circuit** | Build a tiny two-bus load flow and inspect voltage. |
| **02 · IEEE13** | Explore an unbalanced three-phase feeder. |
| **03 · Hosting capacity** | Increase PV and keep the voltage criterion visible. |
| **04 · Fault study** | Run a declared ground fault and inspect solver-returned current. |
| **05 · Reproducibility** | Follow fingerprints, saved artifacts, and checks. |
<div class="cept-card-grid cept-card-grid--three" markdown>

<div class="cept-card" markdown>
<span class="cept-status">Lesson 00</span>

### Environment
Check the runtime, package boundary, and claim scope.

<div class="cept-card-actions" markdown>
<a class="cept-colab-link" href="https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/00_environment.ipynb" target="_blank" rel="noopener" aria-label="Open Lesson 00 in Google Colab" title="Open in Google Colab"><span class="cept-colab-mark" aria-hidden="true"></span></a>
[Private source](https://github.com/sarutesri/cept-studio-edu/blob/main/public/notebooks/00_environment.ipynb){ .cept-source-link target="_blank" rel="noopener" }
</div>
</div>

<div class="cept-card" markdown>
<span class="cept-status">Lesson 01</span>

### First circuit
Build a tiny two-bus load flow and inspect the returned voltage.

<div class="cept-card-actions" markdown>
<a class="cept-colab-link" href="https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/01_first_circuit_load_flow.ipynb" target="_blank" rel="noopener" aria-label="Open Lesson 01 in Google Colab" title="Open in Google Colab"><span class="cept-colab-mark" aria-hidden="true"></span></a>
[Private source](https://github.com/sarutesri/cept-studio-edu/blob/main/public/notebooks/01_first_circuit_load_flow.ipynb){ .cept-source-link target="_blank" rel="noopener" }
</div>
</div>

<div class="cept-card" markdown>
<span class="cept-status">Lesson 02</span>

### IEEE13
Explore an unbalanced three-phase feeder.

<div class="cept-card-actions" markdown>
<a class="cept-colab-link" href="https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/02_ieee13_unbalanced.ipynb" target="_blank" rel="noopener" aria-label="Open Lesson 02 in Google Colab" title="Open in Google Colab"><span class="cept-colab-mark" aria-hidden="true"></span></a>
[Private source](https://github.com/sarutesri/cept-studio-edu/blob/main/public/notebooks/02_ieee13_unbalanced.ipynb){ .cept-source-link target="_blank" rel="noopener" }
</div>
</div>

<div class="cept-card" markdown>
<span class="cept-status">Lesson 03</span>

### Hosting capacity
Increase PV while keeping the voltage criterion visible.

<div class="cept-card-actions" markdown>
<a class="cept-colab-link" href="https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/03_hosting_capacity.ipynb" target="_blank" rel="noopener" aria-label="Open Lesson 03 in Google Colab" title="Open in Google Colab"><span class="cept-colab-mark" aria-hidden="true"></span></a>
[Private source](https://github.com/sarutesri/cept-studio-edu/blob/main/public/notebooks/03_hosting_capacity.ipynb){ .cept-source-link target="_blank" rel="noopener" }
</div>
</div>

<div class="cept-card" markdown>
<span class="cept-status">Lesson 04</span>

### Fault study
Run a declared ground fault and inspect solver-returned current.

<div class="cept-card-actions" markdown>
<a class="cept-colab-link" href="https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/04_fault_study.ipynb" target="_blank" rel="noopener" aria-label="Open Lesson 04 in Google Colab" title="Open in Google Colab"><span class="cept-colab-mark" aria-hidden="true"></span></a>
[Private source](https://github.com/sarutesri/cept-studio-edu/blob/main/public/notebooks/04_fault_study.ipynb){ .cept-source-link target="_blank" rel="noopener" }
</div>
</div>

<div class="cept-card" markdown>
<span class="cept-status">Lesson 05</span>

### Reproducibility
Follow fingerprints, saved artifacts, and checks.

<div class="cept-card-actions" markdown>
<a class="cept-colab-link" href="https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/05_validation_reproducibility.ipynb" target="_blank" rel="noopener" aria-label="Open Lesson 05 in Google Colab" title="Open in Google Colab"><span class="cept-colab-mark" aria-hidden="true"></span></a>
[Private source](https://github.com/sarutesri/cept-studio-edu/blob/main/public/notebooks/05_validation_reproducibility.ipynb){ .cept-source-link target="_blank" rel="noopener" }
</div>
</div>

</div>


<div class="cept-actions" markdown>
<a class="cept-colab-link cept-colab-link--labelled" href="https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/02_ieee13_unbalanced.ipynb" target="_blank" rel="noopener"><span class="cept-colab-mark" aria-hidden="true"></span><span>Try IEEE13 in Colab</span></a>
[Open the full lesson map](public-course.md){ .md-button }
</div>

## What these lessons are for

The notebooks demonstrate bounded solver-backed workflows. They are useful for
learning and reproducibility checks; they do **not** validate a physical project
or field installation.

For the full Windows workflow with interactive SLD and report output, continue
to [Getting started](getting-started.md).

Found a confusing lesson or unexpected result?
[Report it](https://github.com/sarutesri/cept-studio/issues/new).
