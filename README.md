# AI-Powered SAS Migration

Uses Gemini to turn SAS programs into Python, then checks that the Python gives the same results as SAS.

- **Partner:** Oregon Health Authority
- **Program:** Break Through Tech AI Studio, Fall 2026

## Status

- **Program 1:** works. 5 real Gemini runs matched SAS on all 5 tables. The largest difference was `1.24e-14`.
- **Programs 0 and 2:** the AI versions can't run yet. See issue #49.

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
- **Get code from Gemini** (uses your key):
  ```
  python -m src.program1_integration
  ```
- **Read that code** in `output/program1_reviewed_code/`. The next step runs it, and it can access your computer.
- **Run the demo:**
  ```
  python -m streamlit run demo/demo_app.py
  ```

## Good to know

- `data/Project_1/SAS Output CSV/` holds exact copies of the SAS results.
- The original rounded CSVs are in `data/Project_1/archive/`.
- If a check fails, look at the failing rows. Don't raise the tolerance.
- More details: `docs/program1_integration.md`

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
