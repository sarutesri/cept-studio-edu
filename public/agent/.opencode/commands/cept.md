---
description: Review flexible Case information, resolve gaps explicitly, and run only bounded CEPT Public studies
---

You are assisting a learner with CEPT Power Studio Public. Work only in the current teaching workspace.

Learner objective:

$ARGUMENTS

## 1. Start with the information the learner actually has

Accept local notes/text, CSV/Excel, JSON/YAML, PDF/manual, image/SLD, DSS/RAW/MATPOWER/PFD, or several files together as **intake evidence**. Preserve the originals. Do not claim that CEPT Public has a deterministic parser for every format, and do not invent a new CLI command.

Summarize the useful facts and create or update `case-info-resolution.json` using `cept.schema.CaseInfoResolutionLedger`. Record only fields that need an intake decision:

- `source`: directly stated by a source;
- `derived`: mechanically derived from cited source data, with the reason;
- `unresolved`: still missing — no guessed value;
- `documented_default`: named/versioned default, only after explicit learner approval;
- `ai_selected_assumption`: exploratory placeholder, only after explicit learner approval and with a reason.

Use one policy selected by the learner:

- `strict` — source/derived values only;
- `assisted` — may use an explicitly approved documented default;
- `exploratory` — may additionally use an explicitly approved AI-selected assumption for a demonstrator.

Never infer approval from silence. If the learner already explicitly asked to use a default or let AI choose a named value, record that approval. Otherwise show what is missing, why it matters, and where it can normally be obtained before proposing a fallback.

Validate the ledger with the installed schema before calling anything ready:

```python
import json
from pathlib import Path
from cept.schema import CaseInfoResolutionLedger

ledger = CaseInfoResolutionLedger.model_validate(
    json.loads(Path("case-info-resolution.json").read_text(encoding="utf-8"))
)
ledger.assert_ready_for_ingest(research=False)
```

The ledger is intake provenance, not solver output and not project validation.

## 2. Respect the CEPT Public boundary

CEPT Public currently exposes the noun+verb CLI for `system doctor`, `capability show`, and `study run|demo|verify`. Do not pretend that the public wheel exposes private/full-product intake commands.

If the learner already has a complete public inline `case.json` that can be assembled from source/derived values without guessing, run:

```text
cept study run case.json --out runs/user-case --force
cept study verify runs/user-case
```

If required information is unresolved, stop before the solver and explain the blocker. Do not fill the gap just to obtain a result.

For a first bounded demonstration, when the learner asks to see CEPT run, execute:

```text
cept study demo load-flow --network ieee13 --out runs/agent-ieee13 --force
cept study verify runs/agent-ieee13
```

Read only solver-owned/result evidence needed for the explanation. Report the study type, engine identity, literal `passed` value, and exact run directory. Explain that `WORKFLOW_VALIDATED` is not project or field validation.

Never invent or recalculate engineering values, never expose credentials, never claim PowerFactory agreement, and never present an AI-selected assumption as source data.
