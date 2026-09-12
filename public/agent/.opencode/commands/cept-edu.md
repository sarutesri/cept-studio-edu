---
description: Run one bounded CEPT Public teaching study and explain its evidence
---

You are assisting a learner with CEPT Power Studio Public. Work only in the current teaching workspace.

Learner objective:

$ARGUMENTS

Use the installed public noun+verb CLI. For a first demonstration, execute exactly:

```text
python -m cept.public_cli study demo load-flow --network ieee13 --out runs/agent-ieee13 --force
python -m cept.public_cli study verify runs/agent-ieee13
```

Read only the resulting `manifest.json` and `public-verification.json`. Report the study type, engine identity, literal `passed` value, and exact run directory. Explain that `WORKFLOW_VALIDATED` is not project or field validation. Never invent or recalculate engineering values, never expose credentials, and never claim PowerFactory agreement.