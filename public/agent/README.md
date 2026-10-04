# OpenCode teaching demonstration

This optional lesson shows an agent invoking the same installed CEPT Public CLI that the notebooks use. The solver and CEPT verification artifacts remain the source of engineering claims; the model transcript is only a narrated observation.

## Local or Jupyter use

1. Install the pinned CEPT Public wheel and verify its published SHA-256 before starting OpenCode.
2. Install OpenCode separately and authenticate with a provider using its documented login flow. Do not paste tokens into a notebook, prompt, output cell, repository file, or replay.
3. Copy `.opencode/commands/cept.md` into the teaching workspace (`cept-edu.md` is the same lesson under its explicit name).
4. Run:

```text
opencode run --command cept --format json --model <provider/model> "Run the bounded IEEE 13-node load-flow demonstration and explain only verified artifacts."
```

In the OpenCode TUI this is the short `/cept` command, so learners always see which command the agent is driving.

The lesson drives the verb-only public CLI: put a public `case.json` in the
teaching workspace and the agent runs `cept check`, `cept run`, and
`cept verify` on it. `observe-replay.json` is a recorded transcript of an
observed session, not a command reference.

The committed replay was recorded with `opencode/muse-spark-1.3-contributor-free`. Do not copy that choice without reading its terms: OpenCode's privacy page says that model is discounted "in exchange for permission to use your prompts and completions to train future Meta models" (https://opencode.ai/docs/zen/#privacy). Pick a model whose data policy you accept. The same page documents `space-bunny-free` and `longcat-2.5-preview-free` as zero-retention and not used for training, but both are free for a limited time, so check the page before relying on them. Whatever you choose, the agent can send your prompts and the files in the workspace to that provider: do not use confidential data. Provider availability changes; if a model is unavailable, select another explicitly and do not silently substitute one in a recorded comparison.

## Credential safety

Use OpenCode's credential store or short-lived environment injection supported by the provider. Keep secret names out of captured environment dumps and clear notebook outputs before sharing. The committed replay contains no credential values and is labelled `SANITIZED_OBSERVE_REPLAY`; it is not a live solver receipt.

## Hosted runtimes

Google Colab can run the CLI notebooks without an agent. Running OpenCode inside Colab additionally requires an authenticated model provider and therefore is opt-in. Binder is not a supported agent runtime: sessions are ephemeral, resource limits vary, and safely provisioning learner credentials is not deterministic.
