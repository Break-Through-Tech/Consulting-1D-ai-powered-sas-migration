# AI Migration Studio

AI Migration Studio is a Streamlit app built for our AI-powered SAS-to-Python migration project at Break Through Tech AI Studio.

Our project uses Gemini to help translate existing SAS programs that calculate hospital quality scores and star ratings. We then compare the Python results with the original SAS outputs to check whether the calculations were reproduced correctly.

The app brings the code, validation results, and hospital-level data together in one place so users can explore how the migration works.

## What you can explore

The app has four main pages.

### 1. Home

An overview of the migration and how the three programs connect:

- **Program 0:** Prepares and standardizes hospital measurements.
- **Program 1:** Uses 46 measures to calculate scores across five quality areas.
- **Program 2:** Combines those scores to produce hospital star ratings.

The overview also shows the validation status of the selected saved run and provides shortcuts to explore the code, results, or individual hospitals.

### 2. See the code

Explore the original SAS code alongside its saved Python implementation.

Select a program and calculation to view:

- The original SAS instructions and their source location.
- The corresponding Python code, generated with AI assistance and reviewed by the team.
- The calculation's declared inputs, outputs, and dependencies.
- Its recorded execution status, when available.

This page shows how individual calculations were translated and how they fit into the larger pipeline.

### 3. Check the results

Review how the Python outputs compare with the original SAS results.

The migration produces ten output tables:

- **Program 0:** Three tables from data preparation and standardization.
- **Program 1:** Five quality-score tables.
- **Program 2:** Two tables containing star ratings and national averages.

The validation report shows which tables passed, failed, or were not checked.

You can also select an individual table to inspect its validation details, including numerical differences and mismatched values when those details are available.

Comparing intermediate results helps us identify differences that might not be visible in the final star ratings.

### 4. Explore hospital ratings

Choose an individual hospital using its six-digit ID to explore the results behind its star rating.

This page includes:

- The hospital's star rating and combined quality score.
- A chart showing its five quality scores.
- Measurements used to calculate each quality score.
- A breakdown of how the three programs contribute to the final result.
- Comparisons with the original SAS results, when reference data is available.

The five quality areas are Mortality, Safety, Readmission, Patient Experience, and Process.

## Hospital auditing and discrepancy tracing

We also built tools to investigate results at the individual hospital level.

The hospital audit follows a hospital through seven saved checkpoints: standardized data from Program 0, five quality-score outputs from Program 1, and the final rating from Program 2.

Each checkpoint can be compared with its SAS counterpart.

If a difference appears, the diagnostic tool identifies the earliest observed mismatch and uses the program dependencies to suggest calculations worth investigating.

For example, we tested the system by deliberately changing a saved mortality score for one hospital. The audit detected the difference, and dependency tracing suggested the mortality calculation and its shared scoring function as places to inspect.

These suggestions help narrow an investigation. They do not automatically prove the root cause.

## Running locally

Run the app from the project repository:

    bash demo/run_migration_studio.sh

Then open:

http://localhost:8519

The launcher looks for a Python environment in the repository, followed by `~/sas-migration/.venv`. Otherwise, it uses `python3`.

You can specify an environment manually:

    PYTHON_BIN=.venv/bin/python bash demo/run_migration_studio.sh

The environment needs Streamlit, pandas, and Altair. Some features also depend on the packages used by the main repository.

## Project files and data

The app reads existing project files, including:

- `manifests/program0.json`
- `manifests/program1.json`
- `manifests/program2.json`
- `output/chain_runs/` for completed pipeline runs and validation reports

Select a saved run from the sidebar to view its results.

If no saved run is available, parts of the interface display illustrative preview data. Preview values are not actual validation results.

Hospital-level comparisons also require the project's audit modules and original SAS reference outputs.

## Notes

- The app is read-only and uses saved results.
- It does not call Gemini or generate new translations.
- It does not rerun the pipeline, trigger the feedback loop, or modify project data.
- The hospital data shown is from the July 2025 reference dataset.
- The app is a project demonstration, not an official CMS hospital-rating service.
- It is separate from the team's existing `demo/demo_app.py`.

## Project

Developed as part of Consulting 1D's AI-powered SAS migration project for Break Through Tech AI Studio.
