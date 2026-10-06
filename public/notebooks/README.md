# CEPT Public candidate notebooks

The course has four tracks: start, answer feeder questions, trust a result, and
automate. Each notebook keeps solver output separate from teaching text and makes
the evidence boundary visible. The lessons are concise, icon-led, and designed
for Colab. File names keep their original numbers so published Colab links stay
valid; the lesson number is the one in each notebook's title.

| Lesson | Track | Notebook | Topic |
| --- | --- | --- | --- |
| 00 | Start | [`00_environment.ipynb`](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/00_environment.ipynb) | Check your setup |
| 01 | Answer feeder questions | [`02_first_circuit_sld.ipynb`](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/02_first_circuit_sld.ipynb) | Build your first network |
| 02 | Answer feeder questions | [`03_unbalanced_feeder.ipynb`](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/03_unbalanced_feeder.ipynb) | Unbalanced phases |
| 03 | Answer feeder questions | [`05_solar_hosting_capacity.ipynb`](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/05_solar_hosting_capacity.ipynb) | Solar hosting capacity, and the node that sets the limit |
| 04 | Answer feeder questions | [`10_missing_line_rating.ipynb`](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/10_missing_line_rating.ipynb) | One missing rating, twice the solar |
| 05 | Answer feeder questions | [`04_incomplete_data.ipynb`](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/04_incomplete_data.ipynb) | When data is missing |
| 06 | Answer feeder questions | [`06_fault_study.ipynb`](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/06_fault_study.ipynb) | Short-circuit current for one declared fault |
| 07 | Answer feeder questions | [`11_first_dynamics.ipynb`](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/11_first_dynamics.ipynb) | A first dynamics run (illustrative machine data) |
| 08 | Trust a result | [`01_why_solvers_lie.ipynb`](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/01_why_solvers_lie.ipynb) | Same feeder, two voltage bases |
| 09 | Trust a result | [`07_digital_evidence.ipynb`](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/07_digital_evidence.ipynb) | Trace a result |
| 10 | Automate | [`09_workflow_recipe.ipynb`](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/09_workflow_recipe.ipynb) | Write a workflow recipe (and run one that ships) |
| 11 | Automate | [`08_ask_in_plain_words.ipynb`](https://colab.research.google.com/github/sarutesri/cept-studio-edu/blob/main/public/notebooks/08_ask_in_plain_words.ipynb) | Ask in plain words (a recorded AI session) |

Every lesson except 11 uses the same `cept` commands as a terminal; lesson 11 replays a recorded session and runs none.
Step icons support scanning: **⚙ Setup · 🧩 Inputs · ▶ Run · 📊 Results · ✓ Verify · ◇ Interpret**.
Direct OpenDSS/Python comparison cells remain optional; the main path stays
focused on the declared Case and saved evidence.

Lesson 08 compares an omitted downstream voltage base with the declared Case.
The recorded outputs illustrate why convergence alone does not establish model
correctness; the CEPT value is not real-project ground truth.

Lesson 05 separates source data, missing inputs, documented defaults, and
AI-selected assumptions. Required unresolved inputs remain blocked, and the
bundled demonstrator is separate from learner intake.

The notebooks are intended for Google Colab and must be reviewed on a real
cold start before hosted acceptance is claimed. Local execution does not
replace that gate.

No notebook embeds a private project path, PowerFactory input, or invented
engineering value. Demonstrator assumptions remain visible in the Case or the
Case-information resolution ledger.
