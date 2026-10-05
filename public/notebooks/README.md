# CEPT Public candidate notebooks

The course has five stages: start, build, apply, trust, and assist. Each notebook keeps
solver output separate from teaching text and makes the evidence boundary
visible. The lessons are concise, icon-led, and designed for Colab.

| Stage | Notebook | Topic |
| --- | --- | --- |
| Start | [`00_environment.ipynb` — open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/00_environment.ipynb) | Check your setup |
| Start | [`01_why_solvers_lie.ipynb` — open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/01_why_solvers_lie.ipynb) | Same feeder, two voltage bases |
| Build | [`02_first_circuit_sld.ipynb` — open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/02_first_circuit_sld.ipynb) | Build your first network |
| Build | [`03_unbalanced_feeder.ipynb` — open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/03_unbalanced_feeder.ipynb) | Unbalanced phases |
| Build | [`04_incomplete_data.ipynb` — open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/04_incomplete_data.ipynb) | When data is missing |
| Apply | [`05_solar_hosting_capacity.ipynb` — open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/05_solar_hosting_capacity.ipynb) | Solar hosting capacity under one criterion |
| Apply | [`06_fault_study.ipynb` — open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/06_fault_study.ipynb) | Short-circuit current for one declared fault |
| Trust | [`07_digital_evidence.ipynb` — open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/07_digital_evidence.ipynb) | Trace a result |
| Assist | [`08_ask_in_plain_words.ipynb` — open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/08_ask_in_plain_words.ipynb) | Ask in plain words (a recorded AI session) |
| Assist | [`09_workflow_recipe.ipynb` — open in Google Colab](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/09_workflow_recipe.ipynb) | Write a workflow recipe (and run one that ships) |


Notebooks 00-07 use the same `cept` commands as a terminal; notebook 08 replays a recorded session and runs none.
Step icons support scanning: **⚙ Setup · 🧩 Inputs · ▶ Run · 📊 Results · ✓ Verify · ◇ Interpret**.
Direct OpenDSS/Python comparison cells remain optional; the main path stays
focused on the declared Case and saved evidence.

Lesson 01 compares an omitted downstream voltage base with the declared Case.
The recorded outputs illustrate why convergence alone does not establish model
correctness; the CEPT value is not real-project ground truth.

Lesson 04 separates source data, missing inputs, documented defaults, and
AI-selected assumptions. Required unresolved inputs remain blocked, and the
bundled demonstrator is separate from learner intake.

The notebooks are intended for Google Colab and must be reviewed on a real
cold start before hosted acceptance is claimed. Local execution does not
replace that gate.

No notebook embeds a private project path, PowerFactory input, or invented
engineering value. Demonstrator assumptions remain visible in the Case or the
Case-information resolution ledger.
