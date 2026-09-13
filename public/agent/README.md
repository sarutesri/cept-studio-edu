# OpenCode teaching demonstration

This optional lesson shows an agent invoking the same installed CEPT Public CLI that the notebooks use. The solver and CEPT verification artifacts remain the source of engineering claims; the model transcript is only a narrated observation.

## Local or Jupyter use

1. Install the pinned CEPT Public wheel and verify its published SHA-256 before starting OpenCode.
2. Install OpenCode separately and authenticate with a provider using its documented login flow. Do not paste tokens into a notebook, prompt, output cell, repository file, or replay.
3. Copy `.opencode/commands/cept.md` into the teaching workspace (`cept-edu.md` is the same lesson under its explicit name).
4. Run:

```text
opencode run --command cept --format json --model opencode/muse-spark-1.3-contributor-free --variant xhigh "Run the bounded IEEE 13-node load-flow demonstration and explain only verified artifacts."
```

In the OpenCode TUI this is the short `/cept` command, so learners always see which command the agent is driving.

The model identifier above is a convenience for the free teaching route, not part of CEPT's engineering evidence. Provider availability can change. If it is unavailable, select an authenticated model explicitly; do not silently substitute one in a recorded comparison.

## Credential safety

Use OpenCode's credential store or short-lived environment injection supported by the provider. Keep secret names out of captured environment dumps and clear notebook outputs before sharing. The committed replay contains no credential values and is labelled `SANITIZED_OBSERVE_REPLAY`; it is not a live solver receipt.

## Hosted runtimes

Google Colab can run the CLI notebooks without an agent. Running OpenCode inside Colab additionally requires an authenticated model provider and therefore is opt-in. Binder is not a supported agent runtime: sessions are ephemeral, resource limits vary, and safely provisioning learner credentials is not deterministic.
