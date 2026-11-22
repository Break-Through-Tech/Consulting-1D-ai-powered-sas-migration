# AI Migration Studio

A Streamlit app for exploring our SAS-to-Python migration.

You can compare the original SAS code with its Python translation, review validation results, and explore hospital quality scores and star ratings.

## Running the app

From the repository, run:

    bash demo/run_migration_studio.sh

Then open http://localhost:8519.

## Project data

The app uses the manifests in `manifests/` and completed runs in `output/chain_runs/`.

Some screens use preview data when a saved run isn't available. Hospital comparisons require the audit modules and SAS reference outputs.

The app is read-only. It doesn't call Gemini, rerun calculations, or change project data.
