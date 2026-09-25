# CEPT three-step quickstart (OpenDSS)

The two supported paths use different command sets: **Public wheel / Colab** is the small OpenDSS package, while the **Full Windows preview** is the full desktop application installed on Windows.

## Public wheel / Colab

Use Python 3.10 or newer and the public wheel with a verified SHA-256. In Colab, open the notebook directly from its Colab link.

### Step 1 — Prepare the Case

```powershell
mkdir thq; cd thq
copy ..\first_circuit_case.json case.json
```

### Step 2 — Run and verify the evidence with the Public CLI

```powershell
cept study run case.json --out run --format text
cept study verify run --format text
```

These are Public OpenDSS commands. The Public package does not include `physics audit` or `report open`; the evidence to check is `passed: true` from `study verify` and the files saved under `run`.

## Full Windows preview

After installing the Windows preview from [Download](https://sarutesri.github.io/cept-studio/download/):

```powershell
cept study run case.json --engine opendss --out run --force
cept study verify run
cept report open run
```

The Full preview includes these commands. Do not copy the full-preview command set into the Public wheel or Colab because the Public grammar does not support it.

## If something does not pass

- `BLOCKED` with missing field names → add information from its source; never guess a value.
- An engine other than `opendss` → this Public release supports OpenDSS only.
- An old result after changing an input → choose a new `--out` directory or use `--force` in the Full preview, then run again.

The Public path teaches Case → run → verify. It does not claim that the Public package includes a physics audit, SLD viewer, or report server; the Full preview may open a report with `cept report open run`.
