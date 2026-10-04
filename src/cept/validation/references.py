"""Published reference solutions for test feeders.

IEEE 13-Node Test Feeder
------------------------
Reference: IEEE PES Distribution System Analysis Subcommittee, "IEEE 13 Node
Test Feeder" (W. H. Kersting). Per-unit line-to-neutral voltage magnitudes,
base = 4.16 kV (LL) / 2.4 kV (LN); the 634 bus is on the 0.48 kV secondary.

The *as-distributed* EPRI OpenDSS model uses corrected underground-cable
(606) constants and lets the regulators choose taps automatically, which
deviates from the original published solution by up to ~1.3 %. To reproduce
the published numbers, the validation harness applies ``IEEE13_KERSTING_MATCH``
(original 606 constants + the published regulator taps + control off) before
solving. With that configuration OpenDSS reproduces the table below to within
~0.0013 pu.

Phase mapping: 1 = A, 2 = B, 3 = C.
"""

from __future__ import annotations

# {bus: {phase: v_pu}}
IEEE13_PUBLISHED: dict[str, dict[int, float]] = {
    "650": {1: 1.0000, 2: 1.0000, 3: 1.0000},
    "rg60": {1: 1.0625, 2: 1.0500, 3: 1.0687},
    "632": {1: 1.0210, 2: 1.0420, 3: 1.0174},
    "633": {1: 1.0180, 2: 1.0401, 3: 1.0148},
    "634": {1: 0.9940, 2: 1.0218, 3: 0.9960},
    "645": {2: 1.0329, 3: 1.0155},
    "646": {2: 1.0311, 3: 1.0134},
    "671": {1: 0.9900, 2: 1.0529, 3: 0.9778},
    "680": {1: 0.9900, 2: 1.0529, 3: 0.9778},
    "684": {1: 0.9881, 3: 0.9758},
    "611": {3: 0.9738},
    "652": {1: 0.9825},
    "692": {1: 0.9900, 2: 1.0529, 3: 0.9777},
    "675": {1: 0.9835, 2: 1.0553, 3: 0.9758},
}

IEEE34_KERSTING_MATCH: list[str] = [
    # From Run_IEEE34Mod1.dss second script: published regulator taps.
    # Tap = 1 + (tap_step × N), step = 0.00625
    "Transformer.reg1a.wdg=2 Tap=1.075",  # tap 12
    "Transformer.reg1b.wdg=2 Tap=1.03125",  # tap 5
    "Transformer.reg1c.wdg=2 Tap=1.03125",  # tap 5
    "Transformer.reg2a.wdg=2 Tap=1.08125",  # tap 13
    "Transformer.reg2b.wdg=2 Tap=1.06875",  # tap 11
    "Transformer.reg2c.wdg=2 Tap=1.075",  # tap 12
    "Set Controlmode=OFF",
]

# IEEE 34-node solution computed by OpenDSS at the Kersting-match configuration
# (published regulator taps + control off). Used as a regression baseline:
# any code change that shifts voltages will trigger a failure here.
# Representative diagnostic buses covering head, middle, tail, and weakest node.
# Source: OpenDSS DSS-Extensions 0.15.7, dss-python 0.15.7 (2024-03-29).
IEEE34_PUBLISHED: dict[str, dict[int, float]] = {
    "800": {1: 1.0500, 2: 1.0500, 3: 1.0500},
    "802": {1: 1.0475, 2: 1.0484, 3: 1.0484},
    "808": {1: 1.0139, 2: 1.0298, 3: 1.0289},
    "814": {1: 0.9474, 2: 0.9952, 3: 0.9895},  # phase-unbalanced lateral
    "850": {1: 1.0182, 2: 1.0261, 3: 1.0203},
    "852": {1: 0.9593, 2: 0.9692, 3: 0.9640},
    "832": {1: 1.0371, 2: 1.0357, 3: 1.0362},
    "888": {1: 1.0007, 2: 0.9996, 3: 1.0001},
    "890": {1: 0.9178, 2: 0.9248, 3: 0.9178},  # weakest bus (5-mile tap)
}

# IEEE 123-node: energy-conservation reference only (no per-bus published table
# in this repo; the FaultStudy/HC checks use the live circuit).
# Values confirmed by running OpenDSS (auto-control taps).
IEEE123_ENERGY_REF = {
    "source_kw_min": 3400.0,
    "source_kw_max": 3800.0,
    "loss_pct_max": 5.0,  # percent of source kW
    "v_min_acceptable": 0.95,  # lowest allowable pu outside known dead buses
    "v_max_acceptable": 1.06,
}

# Kundur single-machine-infinite-bus system, P. Kundur, "Power System
# Stability and Control" (1994), pg. 843, Example 13.1 — pre-fault steady
# state. Published operating point on a 2220 MVA / 345-24 kV base:
# SourceBus 0.90081 pu / 0 deg (given), generator terminal 1.0 pu / 28.34
# deg with P=0.9 pu, Q=0.436 pu (given). Used as a published-reference check
# for PowerFactory's balanced positive-sequence load flow, since the IEEE
# 13-node Kersting table (above) is unbalanced and the current PowerFactory
# adapter only supports balanced ComLdf (iopt_net=0). See
# examples/kundur_smib_load_flow/case.json and
# tests/test_inline_and_cross_engine.py.
KUNDUR_SMIB_PUBLISHED: dict[str, dict[str, float]] = {
    "SourceBus": {"v_pu": 0.90081, "angle_deg": 0.0},
    "LT": {"v_pu": 1.0, "angle_deg": 28.34},
}

# DSS commands that turn the as-distributed model into the published
# ("match Kersting") configuration. Applied after Redirect, before Solve.
IEEE13_KERSTING_MATCH: list[str] = [
    # Original (uncorrected) 606 underground-cable line constants.
    "Edit Linecode.mtx606 "
    "Rmatrix=[0.7982|0.3192 0.7891|0.2849 0.3192 0.7982] "
    "Xmatrix=[0.4463|0.0328 0.4041|-0.0143 0.0328 0.4463] "
    "Cmatrix=[257|0 257|0 0 257]",
    # Published regulator taps, with automatic control disabled.
    "Transformer.Reg1.Taps=[1.0 1.0625]",
    "Transformer.Reg2.Taps=[1.0 1.0500]",
    "Transformer.Reg3.Taps=[1.0 1.06875]",
    "Set Controlmode=OFF",
]
