# AI Migration Studio v11.2 — Final card alignment

This update is based on v11.1. It changes only the hospital metric card presentation:
- Stars appear on a second line, underneath **4 of 5**.
- All three hospital summary cards have a consistent minimum height.
- The current `width="stretch"` Streamlit API replaces deprecated `use_container_width=True` calls.
- The launcher checks for the three program manifests and stops with a clear error if the demo was installed in the wrong directory.

No saved output, Python calculations, hospital audit logic, source code fixtures, or other repository files are modified.

## Install into the **actual Git repository**, not the ~/sas-migration virtualenv directory

```bash
cd "$HOME/Consulting-1D-ai-powered-sas-migration"
unzip -n "$HOME/Downloads/migration_studio_final_v11_2.zip" -d .
bash demo/run_migration_studio_final_v11_2.sh
```

Open http://localhost:8519.

The demo requires `manifests/program0.json`, `manifests/program1.json`, `manifests/program2.json` and a completed saved run under `output/chain_runs/` to show real project data. These files are not included in the ZIP.

This is a local, read-only preview separate from the hospital audit PR.
