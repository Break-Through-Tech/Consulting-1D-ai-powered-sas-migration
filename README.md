# AI-Powered SAS Migration

Uses Gemini to turn SAS programs into Python, then checks that the Python gives the same results as SAS.

- **Partner:** Oregon Health Authority
- **Program:** Break Through Tech AI Studio, Fall 2026

## Status

- **Program 1:** works. 5 real Gemini runs matched SAS on all 5 tables. The largest difference was `1.24e-14`.
- **Program 0:** works on one real Gemini run. All 33 steps ran and all 3 outputs matched SAS (largest difference `1.57e-13`). Gemini's output changes between runs, so it is not stable yet. That run is saved as a test (`tests/fixtures/program0_generated/`). See `docs/step_runner.md`.
- **Program 2:** the AI version can't run yet. It needs Program 1's results handed over (#72) and the translation (#33).

## Setup

You need Python 3.10 or newer.

```
git clone https://github.com/Break-Through-Tech/Consulting-1D-ai-powered-sas-migration.git
cd Consulting-1D-ai-powered-sas-migration
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

- Open `.env` and add your Gemini API key.
- Never commit `.env`.
- The data is already in this repo.

## Run

Run these from the main project folder.

- **Run the tests** (no key needed):
  ```
  python -m unittest discover tests
  ```
- **Check all 10 outputs against SAS** (no key needed):
  ```
  python -m src.validation_report --baseline
  ```
  This checks the hand-written Python. To check a pipeline run, give it the folder of CSVs instead, for example `python -m src.validation_report output/data`. The pipeline makes only Program 1's five tables today, so the best result for that folder is `INCOMPLETE` (5 of 10 not checked). More details: `docs/validation_report.md`
- **Get code from Gemini** (uses your key):
  ```
  python -m src.program1_integration
  ```
- **Read that code** in `output/program1_reviewed_code/`. The next step runs it, and it can access your computer.
- **Program 0 with Gemini** (uses your key; about 3 minutes on the free tier):
  ```
  python -m src.generate_steps 0 --min-interval 5
  ```
  Read every file in `output/program0_generated_code/`, then run and check them:
  ```
  python -m src.run_program 0 --code-dir output/program0_generated_code --reviewed
  ```
  To try it without a key, run the saved example: `python -m src.run_program 0 --code-dir tests/fixtures/program0_generated --reviewed`. More details: `docs/step_runner.md`
- **Run the demo:**
  ```
  python -m streamlit run demo/demo_app.py
  ```

## Good to know

- `data/Project_1/SAS Output CSV/` holds exact copies of the SAS results.
- The original rounded CSVs are in `data/Project_1/archive/`.
- If a check fails, look at the failing rows. Don't raise the tolerance.
- More details: `docs/program1_integration.md`

## Docs

- `docs/DOCUMENTATION_sas_reader.md`: how the SAS is split into steps (the manifests).
- `docs/program1_integration.md`: Program 1 generate, review, run, and the feedback loop.
- `docs/step_runner.md`: Programs 0 and 2, the translator, and how to run them.
- `docs/validation_report.md`: checking the 10 outputs against SAS.
- `docs/translation_history.md`: the saved record of translation attempts (Program 1).

## Team

| Name | GitHub |
|---|---|
| George Pulickan | @George-Pulickan |
| Hao Li | @becomingWise |
| Lizzette Rivera | @lizzette20 |
| Pawanjit Birdi | @pb125-sy |
| Miguel Garcia | @miguelagarcia-dev |
| Shane Salvador | @devxshane |
| Amanda Tsai | @amytxc |

## License

Not chosen yet.
