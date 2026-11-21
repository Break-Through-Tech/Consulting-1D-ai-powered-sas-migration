"""Independent, read-only Streamlit walkthrough for Consulting 1D's SAS migration.

This file DOES NOT import or modify demo/demo_app.py or hospital_explorer.py.

Install as: repo/demo/migration_studio_final_v11_2.py
Run from repository root:
    python -m streamlit run demo/migration_studio_final_v11_2.py --server.port 8519

Reads existing manifests, a saved chained run, and optional hospital-audit modules.
No model calls, generated-code execution, changes to files, or live translation.
"""

from __future__ import annotations

import html
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import altair as alt
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
RUN_ROOT = ROOT / "output" / "chain_runs"

PROGRAM_LABELS = {
    0: ("Prepare hospital data", "Standardize the hospital measurements used for scoring."),
    1: ("Calculate quality scores", "Combine 46 measures into scores across five areas of care."),
    2: ("Calculate star ratings", "Combine those quality scores to produce hospital star ratings."),
}
PROGRAM_OUTPUTS = {
    0: ["LESS100_MEASURE", "MEASURE_AVERAGE_STDDEV_2025JUL", "STD_DATA_2025JUL_ANALYSIS"],
    1: ["OUTCOME_MORTALITY", "OUTCOME_SAFETY", "OUTCOME_READMISSION", "PTEXP", "PROCESS"],
    2: ["STAR_2025JUL", "NATIONAL_AVERAGE_2025JUL"],
}
OUTPUT_TO_PROGRAM = {name: p for p, names in PROGRAM_OUTPUTS.items() for name in names}
GROUP_PREVIEW = [
    {"Quality group": "Mortality", "Score": 0.2961},
    {"Quality group": "Safety", "Score": 0.3449},
    {"Quality group": "Readmission", "Score": 0.3614},
    {"Quality group": "Patient Experience", "Score": 0.0421},
    {"Quality group": "Process", "Score": -0.4847},
]

st.set_page_config(
    page_title="Migration Studio | Hospital ratings",
    page_icon="🔵",
    layout="wide",
    initial_sidebar_state="expanded",
)

# The prototype's visual layer is intentionally self-contained. No edits to
# the teammate's Streamlit styling, app navigation, or CSS are required.
st.markdown(
    """
<style>
/* Clean visual system, starting from the v4 layout. No stacked version overrides. */
:root {
  --studio-canvas: #F1F5FB;
  --studio-sidebar: #F5F8FC;
  --studio-surface: #FFFFFF;
  --studio-ink: #202A3A;
  --studio-body: #354255;
  --studio-muted: #667085;
  --studio-border: #E4EAF2;
  --studio-hover: #EAF2FF;
  --studio-accent: #245FCF;
  --studio-accent-hover: #194FAF;
  --studio-code: #FFFFFF;
  --studio-font: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
}
/* Typography. Use native system fonts: SF on Mac, Segoe on Windows. */
html, body, .stApp, [data-testid="stMarkdownContainer"],
[data-testid="stWidgetLabel"], [data-testid="stCaptionContainer"],
[data-testid="stSidebar"] button, div.stButton button, input, select, textarea {
  font-family: var(--studio-font) !important;
  color: var(--studio-body);
  -webkit-font-smoothing: antialiased;
}
[data-testid="stMarkdownContainer"] p, [data-testid="stMarkdownContainer"] li { line-height: 1.57; }
h1, h2, h3, h4, .studio-hero-title, .studio-section-title, .studio-brand,
.studio-card-head, .studio-step-title, .studio-value, .studio-kicker {
  font-family: var(--studio-font) !important;
  color: var(--studio-ink) !important;
  letter-spacing: -.025em;
}
h1 { font-size: 1.95rem !important; font-weight: 600 !important; line-height: 1.24 !important; }
h2 { font-size: 1.40rem !important; font-weight: 600 !important; }
h3 { font-size: 1.08rem !important; font-weight: 590 !important; }
[data-testid="stMarkdownContainer"] strong { font-weight: 600; }
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] * {
  color: var(--studio-muted) !important;
  opacity: 1 !important;
  line-height: 1.5;
}
/* Keep the existing Streamlit page dimensions and original sidebar layout. */
[data-testid="stAppViewContainer"], [data-testid="stHeader"] {
  background: var(--studio-canvas) !important;
}
.block-container { max-width: 1370px; padding: 1.6rem 2.5rem 3.6rem; }
[data-testid="stSidebar"], [data-testid="stSidebarContent"] {
  background: var(--studio-sidebar) !important;
  border-right: 1px solid var(--studio-border);
}
[data-testid="stSidebar"] hr { border-color: var(--studio-border); margin: 1.15rem 0; }
.studio-brand { font-size: 1.18rem; font-weight: 600; margin: .08rem 0; }
.studio-brand-sub { color: var(--studio-muted); font-size: .83rem; margin: .18rem 0 0; }
[data-testid="stSidebar"] [data-testid="stButton"] button {
  width: 100%; min-height: 2.65rem; font-size: .92rem;
  font-weight: 480 !important; padding-left: 1rem;
  text-align: left !important; justify-content: flex-start !important;
  border-radius: 10px; box-shadow: none !important;
  transition: background-color 130ms ease, border-color 130ms ease;
}
[data-testid="stSidebar"] [data-testid="stButton"] button p { text-align: left !important; }
[data-testid="stSidebar"] [data-testid="stButton"] button[kind="primary"] {
  background: var(--studio-accent) !important;
  border-color: var(--studio-accent) !important;
  color: #FFFFFF !important;
}
[data-testid="stSidebar"] [data-testid="stButton"] button[kind="primary"] * { color: #FFFFFF !important; }
[data-testid="stSidebar"] [data-testid="stButton"] button[kind="secondary"] {
  background: transparent !important; border-color: transparent !important;
  color: var(--studio-body) !important;
}
[data-testid="stSidebar"] [data-testid="stButton"] button[kind="secondary"]:hover {
  background: var(--studio-hover) !important;
  border-color: transparent !important;
}
[data-testid="stSidebar"] [data-testid="stButton"] button[kind="primary"]:hover {
  background: var(--studio-accent-hover) !important;
  border-color: var(--studio-accent-hover) !important;
}
/* Existing homepage hero: white left, tiny blue glow on right. */
.studio-hero {
  background: radial-gradient(ellipse 430px 290px at 100% 8%, rgba(192,215,252,.46), transparent 78%),
              linear-gradient(105deg,#FFFFFF 10%,#FFFFFF 70%,#F6F9FF 100%);
  border: 1px solid var(--studio-border); border-radius: 18px;
  padding: 2.05rem 2.15rem 1.9rem;
  margin: .22rem 0 1rem;
  box-shadow: 0 2px 10px rgba(33,45,65,.025);
}
.studio-eyebrow {font-size: .74rem; text-transform: uppercase; letter-spacing: .088em;
  font-weight: 560; color: #526174; margin: .1rem 0 .5rem; }
.studio-hero .studio-eyebrow { margin: 0 0 .55rem; }
.studio-hero-title {font-size: 2.16rem; font-weight: 610; line-height: 1.22;
  letter-spacing: -.04em; margin: 0 0 .72rem; max-width: 760px;}
.studio-hero-text {font-size: 1.015rem; color: var(--studio-body); line-height: 1.65;
  max-width: 790px; margin: 0 0 .88rem;}
.studio-hero-note {font-size: .88rem; font-weight: 580; color: #103E73; }
.studio-hero-note.neutral { color: var(--studio-muted); }
/* Section and KPI text is charcoal, NOT blue/navy. */
.studio-section-title {font-size: 1.35rem; font-weight: 600; margin: .85rem 0 .28rem; }
.studio-section-sub {color: var(--studio-muted); font-size: .97rem; line-height: 1.55; margin: 0 0 .86rem;}
.studio-subtitle {color: var(--studio-muted); font-size: 1.01rem; line-height: 1.56;
  margin: -.25rem 0 1.35rem; max-width: 790px; }
.studio-stat, .studio-step, [data-testid="stVerticalBlockBorderWrapper"] {
  background: var(--studio-surface) !important;
  border: 1px solid var(--studio-border) !important;
  border-radius: 14px !important;
  box-shadow: 0 1px 5px rgba(33,45,65,.035) !important;
}
.studio-stat { min-height: 111px; padding: 1.1rem 1.2rem; }
.studio-kicker {font-size: .84rem; font-weight: 560; margin: 0 0 .28rem; }
.studio-value {font-size: 1.76rem; font-weight: 620; line-height: 1.15; letter-spacing: -.035em; }
.studio-micro {color: var(--studio-muted); font-size: .86rem; line-height: 1.48; margin-top: .35rem; }
.studio-step {padding: 1.1rem 1.18rem; min-height: 135px;}
.studio-number {display: inline-flex; align-items: center; justify-content: center;
  background: #EAF2FF; color: #3977BF; font-size: .91rem; font-weight: 600;
  border-radius: 9px; width: 33px; height: 33px; margin-bottom: .55rem;}
.studio-step-title {font-weight: 580; font-size: 1.01rem; margin-bottom: .25rem; }
.studio-step-desc {color: var(--studio-muted); font-size: .9rem; line-height: 1.52; }
.studio-card-head {font-weight: 580; font-size: 1.02rem; margin: .05rem 0 .45rem; }
.studio-tag {display: inline-block; font-size: .76rem; font-weight: 540;
  padding: .3rem .65rem; color: #103E73; background: #EAF2FF;
  border: 1px solid #D8E5F6; border-radius: 999px; }
.studio-status-success { color: #103E73; font-weight: 580; }
.studio-status-fail { color: #A42F2F; font-weight: 570; }
.studio-status-muted { color: var(--studio-muted); font-weight: 550; }
.studio-feature {background: #FFFFFF; border: 1px solid var(--studio-border);
  border-radius: 13px; padding: 1.1rem 1.25rem; color: var(--studio-body); line-height: 1.58; }
.studio-helper {color: var(--studio-muted); font-size: .94rem; line-height: 1.55; max-width: 870px; }
.studio-flow-arrow {color: #768AA7; font-size: 1.4rem; font-weight: 500;
  text-align: center; padding-top: 3.7rem;}
/* Fluent-style interaction: only actionable controls react. */
div.stButton > button {
  min-height: 2.62rem; font-family: var(--studio-font) !important;
  border-radius: 10px !important; font-weight: 520 !important;
  transition: background-color 140ms ease, border-color 140ms ease !important;
  box-shadow: none !important;
}
div.stButton > button[kind="primary"] {
  background: var(--studio-accent) !important;
  border-color: var(--studio-accent) !important;
  color: #FFFFFF !important;
}
div.stButton > button[kind="primary"] * {color: #FFFFFF !important;}
div.stButton > button[kind="primary"]:hover {
  background: var(--studio-accent-hover) !important;
  border-color: var(--studio-accent-hover) !important;
}
div.stButton > button[kind="secondary"] {
  background: #FFFFFF !important; border-color: #D9E3F0 !important;
  color: #354255 !important;
}
div.stButton > button[kind="secondary"]:hover {
  background: var(--studio-hover) !important; border-color: #BFD1E8 !important;
}
[data-testid="stSelectbox"] [data-baseweb="select"] > div:first-child,
[data-testid="stSidebar"] [data-baseweb="select"] > div:first-child,
[data-testid="stTextInput"] input {
  background: #FFFFFF !important; border: 1px solid #D9E3EE !important;
  border-radius: 10px !important; color: var(--studio-ink) !important;
}
[data-testid="stSelectbox"] [data-baseweb="select"] > div:first-child:hover,
[data-testid="stTextInput"] input:hover { border-color: #AABDD6 !important; }
[data-baseweb="popover"], [data-baseweb="menu"],
[data-baseweb="popover"] [role="listbox"], [data-baseweb="popover"] [role="option"] {
  background: #FFFFFF !important; color: var(--studio-ink) !important;
}
[data-baseweb="popover"] [role="option"]:hover,
[data-baseweb="popover"] [role="option"][aria-selected="true"] {
  background: var(--studio-hover) !important;
}
[data-testid="stWidgetLabel"] *, [data-testid="stTextInput"] label * {color: var(--studio-body) !important;}
/* White code surfaces; a definite light-gray outline separates them from the card. */
[data-testid="stCodeBlock"], [data-testid="stCodeBlock"] pre,
[data-testid="stCode"] pre {
  background: #FFFFFF !important;
  border-radius: 10px !important;
}
[data-testid="stCodeBlock"] {
  border: 1px solid #D5DFEB !important;
  border-radius: 10px !important;
  overflow: hidden;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.7);
}
[data-testid="stCodeBlock"] code {background: transparent !important; overflow-wrap: anywhere !important;}
[data-testid="stCodeBlock"] pre {max-width: 100%; overflow-x: hidden !important; white-space: pre-wrap !important; tab-size: 2;}
[data-testid="stCodeBlock"] pre, [data-testid="stCodeBlock"] code,
[data-testid="stCodeBlock"] span {
  font-family: ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace !important;
  font-size: .85rem !important; font-weight: 400 !important;
}
/* Tables should not inherit mint colors from data formatters. */
[data-testid="stDataFrame"] {background: #FFFFFF !important; border: 1px solid var(--studio-border) !important; border-radius: 10px;}
[data-testid="stDataFrame"] [role="grid"] {background: #FFFFFF !important;}
[data-testid="stAlert"] {border-radius: 11px; }
[data-testid="stMetricValue"] {color: var(--studio-ink); font-weight: 600;}
[data-testid="stMetricLabel"] {color: var(--studio-muted); font-weight: 500;}
/* Keep focus accessible, not animated. */
div.stButton > button:focus-visible,
[data-testid="stSelectbox"] [data-baseweb="select"]:focus-within {
  outline: 2px solid var(--studio-accent) !important; outline-offset: 2px;
}
@media(max-width: 950px) {
  .block-container {padding: .88rem 1rem 2.7rem;}
  .studio-hero {padding: 1.3rem 1.35rem;}
  .studio-hero-title {font-size: 1.72rem;}
  .studio-stat {min-height: 0;}
}
@media(prefers-reduced-motion: reduce) {
  div.stButton > button, [data-testid="stSidebar"] [data-testid="stButton"] button {transition: none !important;}
}

/* Passing checks use muted blue rather than the default green alert color.
   Warnings and errors remain their native meaningful colors. */
.studio-pass-notice {
  display: flex; align-items: center; gap: .7rem;
  padding: .88rem 1rem; border-radius: 11px; margin-bottom: 1.1rem;
  border: 1px solid #D8E6FB; background: #EAF2FF;
  color: #1F2937; font-size: .91rem; line-height: 1.45;
}
.studio-pass-notice .studio-pass-icon {
  display: inline-flex; flex-shrink: 0; align-items: center; justify-content: center;
  height: 25px; width: 25px; border-radius: 50%;
  color: #103E73; background: #FFFFFF; font-size: .92rem; font-weight: 650;
}

/* V10: opaque white cards, preserving v9's layout, spacing, and typography.
   Streamlit attaches st-key-* to keyed containers. CSS is scoped to those keys. */
[class*="st-key-studio_surface_"] {
  background: #FFFFFF !important;
  background-color: #FFFFFF !important;
  border-radius: 16px !important;
  border-color: #DAE3EE !important;
  box-shadow: 0 2px 12px rgba(29, 49, 80, .045) !important;
}
[data-testid="stVerticalBlockBorderWrapper"]:has(> div[class*="st-key-studio_surface_"]),
[data-testid="stVerticalBlockBorderWrapper"]:has(> div > div[class*="st-key-studio_surface_"]) {
  background: #FFFFFF !important;
  background-color: #FFFFFF !important;
  border-radius: 16px !important;
  border-color: #DAE3EE !important;
  box-shadow: 0 2px 12px rgba(29, 49, 80, .045) !important;
}
/* A little extra separation for the cards, without changing their positions. */
.studio-stat, .studio-step { background: #FFFFFF !important; border-radius: 16px !important; }
/* Code and data stay white; blue is reserved for navigation, links, and charts. */
[data-testid="stDataFrame"], [data-testid="stDataFrame"] [role="grid"] {
  background: #FFFFFF !important;
}
/* Match the heights of the three hospital KPI cards. Streamlit's native
   metric captions make the other two taller; the stars must not create a short card.
   Cover keyed container and its border wrapper across Streamlit DOM variants. */
[class*="st-key-studio_surface_17"],
[class*="st-key-studio_surface_18"],
[class*="st-key-studio_surface_19"],
[data-testid="stVerticalBlockBorderWrapper"]:has([class*="st-key-studio_surface_17"]),
[data-testid="stVerticalBlockBorderWrapper"]:has([class*="st-key-studio_surface_18"]),
[data-testid="stVerticalBlockBorderWrapper"]:has([class*="st-key-studio_surface_19"]) {
  min-height: 168px !important;
}
/* The hospital's stars sit alongside its number instead of adding a second row. */
.studio-rating-label {font-size: .86rem; color: var(--studio-muted); margin: 0 0 .45rem;}
.studio-rating-row {display: block; min-height: 2.3rem;}
.studio-rating-number {font-size: 1.85rem; line-height: 1.25; font-weight: 600; letter-spacing: -.03em; color: var(--studio-ink);}
.studio-rating-stars {display:block; margin-top:.55rem; font-size: 1.35rem; line-height: 1.2; letter-spacing: .025em; color: #245FCF; white-space: nowrap;}
.studio-rating-stars .unfilled {color: #CAD5E5;}
</style>
""",
    unsafe_allow_html=True,
)



def blue_success(message: str) -> None:
    """Present validated PASS outcomes in the approved blue status style.

    This is a display-only alternative to Streamlit's green st.success alert;
    it does not change any audit or validation logic.
    """
    st.markdown(
        '<div class="studio-pass-notice" role="status">'
        '<span class="studio-pass-icon" aria-hidden="true">✓</span>'
        f'<span>{esc(message)}</span></div>',
        unsafe_allow_html=True,
    )


def clean_table(frame: pd.DataFrame):
    """Ensure readable, neutral cells independently of Streamlit's widget theme."""
    return frame.style.set_properties(**{
        "background-color": "#ffffff",
        "color": "#1F2937",
    })

def esc(text: Any) -> str:
    return html.escape(str(text))


def read_json(path: Path) -> dict | list | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None


def list_runs() -> list[Path]:
    if not RUN_ROOT.exists():
        return []
    return [
        p for p in sorted(RUN_ROOT.iterdir(), reverse=True)
        if p.is_dir() and (p / "validation" / "validation_report.json").is_file()
    ]


@st.cache_data(show_spinner=False)
def load_manifest(program: int) -> list[dict]:
    items = read_json(ROOT / "manifests" / f"program{program}.json")
    return items if isinstance(items, list) else []


@st.cache_data(show_spinner=False)
def load_run(run_path: str) -> dict:
    run = Path(run_path)
    report = read_json(run / "validation" / "validation_report.json")
    handoff = read_json(run / "handoff_summary.json")
    steps = {p: read_json(run / f"program{p}_steps.json") for p in (0, 1, 2)}
    return {
        "report": report if isinstance(report, dict) else {},
        "handoff": handoff if isinstance(handoff, dict) else {},
        "steps": steps,
        "run_dir": run_path,
    }


def report_outputs(run: dict | None) -> dict:
    if not run:
        return {}
    outputs = run.get("report", {}).get("outputs", {})
    return outputs if isinstance(outputs, dict) else {}


def safe_status(status: Any) -> str:
    return str(status or "NOT_CHECKED").upper()


def summary_counts(run: dict | None) -> dict[str, int]:
    rows = report_outputs(run)
    counts = {status: 0 for status in ("PASS", "FAIL", "ERROR", "NOT_CHECKED")}
    for value in rows.values():
        status = safe_status(value.get("status") if isinstance(value, dict) else None)
        counts[status if status in counts else "NOT_CHECKED"] += 1
    return counts


def actual_run_has_complete_pass(run: dict | None) -> bool:
    rows = report_outputs(run)
    expected = set(OUTPUT_TO_PROGRAM)
    return bool(run) and set(rows) == expected and all(
        isinstance(rows[name], dict) and safe_status(rows[name].get("status")) == "PASS"
        for name in expected
    ) and str(run.get("report", {}).get("status", "")).upper() == "PASS"


def render_title(eyebrow: str, title: str, subtitle: str) -> None:
    st.markdown(f'<div class="studio-eyebrow">{esc(eyebrow)}</div>', unsafe_allow_html=True)
    st.title(title)
    st.markdown(f'<div class="studio-subtitle">{esc(subtitle)}</div>', unsafe_allow_html=True)


def info_card(title: str, value: str, supporting: str = "") -> None:
    # A single calm card instead of a stack of nested Streamlit containers.
    st.markdown(
        '<div class="studio-stat">'
        f'<div class="studio-kicker">{esc(title)}</div>'
        f'<div class="studio-value">{esc(value)}</div>'
        f'<div class="studio-micro">{esc(supporting)}</div>'
        '</div>',
        unsafe_allow_html=True,
    )


def chip_status(status: str) -> str:
    status = safe_status(status)
    labels = {"PASS": "✓ Passed", "FAIL": "✕ Failed", "ERROR": "! Error", "NOT_CHECKED": "— Not checked"}
    return labels.get(status, status.title())


def program_state(program: int, run: dict | None) -> tuple[str, str]:
    if run is None:
        return "Preview", "Sample layout only"
    outputs = report_outputs(run)
    expected = PROGRAM_OUTPUTS[program]
    program_entries = [outputs.get(n) for n in expected]
    ok = sum(isinstance(entry, dict) and safe_status(entry.get("status")) == "PASS" for entry in program_entries)
    if ok == len(expected):
        return "PASS", f"{ok} of {len(expected)} results matched the originals"
    if any(isinstance(entry, dict) and safe_status(entry.get("status")) in ("FAIL", "ERROR") for entry in program_entries):
        return "FAIL", f"{ok} of {len(expected)} results matched the originals"
    return "NOT_CHECKED", f"{ok} of {len(expected)} results matched the originals"


def python_candidates(program: int, run: dict | None, step_id: str) -> list[Path]:
    candidates = []
    if run:
        dirs = run.get("handoff", {}).get("code_dirs", {})
        raw = dirs.get(str(program))
        if raw:
            code_dir = Path(str(raw))
            candidates.append(code_dir / f"{step_id}.py")
            if not code_dir.is_absolute():
                candidates.append(ROOT / code_dir / f"{step_id}.py")
    paths = {
        0: ["output/program0_generated_code", "tests/fixtures/program0_generated", "output/generated_code"],
        1: ["output/program1_reviewed_code", "output/program1_generated_code", "output/generated_code"],
        2: ["output/program2_generated_code", "tests/fixtures/program2_generated", "output/generated_code"],
    }
    for directory in paths[program]:
        candidates.append(ROOT / directory / f"{step_id}.py")
    return candidates


def python_source(program: int, run: dict | None, step_id: str) -> tuple[str | None, str | None]:
    for file in python_candidates(program, run, step_id):
        try:
            if file.is_file():
                return file.read_text(encoding="utf-8"), str(file)
        except (OSError, UnicodeDecodeError):
            continue
    return None, None


def quality_plot(records: list[dict]):
    df = pd.DataFrame(records)
    if df.empty:
        st.info("No saved quality-group scores are available.")
        return
    df["Score"] = pd.to_numeric(df["Score"], errors="coerce")
    df = df.dropna(subset=["Score"])
    if df.empty:
        st.info("This hospital has no usable standardized group scores.")
        return
    labels = list(df["Quality group"])
    domain = max(.5, float(df["Score"].abs().max()) * 1.22)
    base = alt.Chart(df).encode(
        y=alt.Y("Quality group:N", sort=labels, title=None,
                axis=alt.Axis(labelFontSize=12, labelColor="#526174", labelLimit=220, labelPadding=10)),
        x=alt.X("Score:Q", scale=alt.Scale(domain=[-domain, domain]),
                axis=alt.Axis(title="Standardized quality score (0 = reference average)", format=".2f")),
        tooltip=[alt.Tooltip("Quality group:N"), alt.Tooltip("Score:Q", format=".4f")],
    )
    bars = base.mark_bar(size=24, cornerRadius=3).encode(
        color=alt.condition(alt.datum.Score >= 0, alt.value("#3B82F6"), alt.value("#A7B7CD")),
    )
    zero = alt.Chart(pd.DataFrame({"zero": [0]})).mark_rule(
        color="#526174", strokeWidth=1.5
    ).encode(x="zero:Q")
    labels_chart = base.mark_text(fontSize=11, dx=8, align="left", color="#1F2937").encode(
        text=alt.Text("Score:Q", format=".4f")
    ).transform_filter("datum.Score >= 0")
    neg_labels = base.mark_text(fontSize=11, dx=-8, align="right", color="#1F2937").encode(
        text=alt.Text("Score:Q", format=".4f")
    ).transform_filter("datum.Score < 0")
    chart = (bars + zero + labels_chart + neg_labels).properties(
        height=260, background="#ffffff"
    ).configure_view(
        stroke=None, fill="#ffffff"
    ).configure_axis(labelColor="#526174", titleColor="#526174", gridColor="#E8EEF6", labelFontSize=12, titleFontSize=12)
    st.altair_chart(chart, width="stretch")


def validation_panel(run: dict | None, hospital_result: dict | None = None) -> None:
    with st.container(border=True, key="studio_surface_01"):
        st.markdown('### Validation Overview')
        st.caption("See what matched for this hospital and for the full dataset.")
        if hospital_result is None:
            if run is None:
                st.markdown("**Hospital checks unavailable**")
                st.caption("Load a saved run to check a hospital. The preview scores are illustrative only.")
            else:
                st.markdown("**— No hospital selected**")
                st.caption("Choose a hospital to see its comparison with SAS.")
        else:
            checks = hospital_result.get("checks", [])
            pass_count = sum(x.get("status") == "PASS" for x in checks)
            total = len(checks)
            if total and pass_count == total:
                blue_success(f"{pass_count} of {total} hospital checks passed")
            elif total:
                st.error(f"{pass_count} of {total} hospital checks passed")
            else:
                st.warning("No hospital-level checks returned.")
        st.divider()
        st.markdown("**First difference found**")
        if hospital_result is not None:
            failing = [x for x in hospital_result.get("checks", []) if x.get("status") == "FAIL"]
            st.write("No differences found in the checked results" if not failing else failing[0].get("output", "Difference detected"))
        elif run is None:
            st.caption("Preview only — no checks have run here")
        else:
            st.caption("Select a hospital to inspect this result.")
        st.divider()
        st.markdown("**Full dataset · all result sets**")
        if run is None:
            st.markdown("**No saved report loaded**")
            st.caption("No full-dataset validation has been performed in this preview.")
        else:
            counts = summary_counts(run)
            total = len(report_outputs(run))
            if actual_run_has_complete_pass(run):
                blue_success(f"{counts['PASS']} of {total} result sets passed")
            else:
                st.warning(f"{counts['PASS']}/{total} passed · {counts['FAIL']} failed · "
                           f"{counts['ERROR']} errors · {counts['NOT_CHECKED']} unchecked")
            report = run.get("report", {})
            st.caption(f"Reference comparison · rtol={report.get('rtol', 'N/A')} · "
                       f"atol={report.get('atol', 'N/A')}")


def navigate_to_view(view: str) -> None:
    """Navigate in a button callback, before any view widgets are drawn."""
    st.session_state["studio_view"] = view


def navigate_to_program(program: int) -> None:
    """Button callbacks set the next page before the view is drawn."""
    st.session_state["studio_program"] = program
    st.session_state["studio_view"] = "Code Explorer"


def sample_notice(run: dict | None) -> None:
    if run is None:
        st.info("**Preview mode** — no saved results were found. "
                "Example values are shown for layout only, not as real validation results. "
                "Put this file in your project’s `demo/` folder to load a saved run.")


def overview_page(run: dict | None) -> None:
    """Start with a clear result, then show how the three programs connect."""
    full_pass = actual_run_has_complete_pass(run)
    outputs = report_outputs(run)
    passed = summary_counts(run)["PASS"] if run else 0
    total = len(outputs)
    if run is None:
        note = "Preview only · Load a saved run to see validation results"
        note_class = "neutral"
    elif full_pass:
        note = "✓ All 10 output tables passed validation"
        note_class = ""
    else:
        note = f"{passed} of {total} output tables passed · Review the validation report"
        note_class = "neutral"
    st.markdown(
        '<section class="studio-hero">'
        '<div class="studio-eyebrow">AI Migration Studio · Overview</div>'
        '<div class="studio-hero-title">Inside the migration</div>'
        '<p class="studio-hero-text">Hospital star ratings rely on programs written in SAS. '
        'With help from AI, we’re translating those programs into Python and using '
        'the original results to verify the new calculations.</p>'
        f'<div class="studio-hero-note {note_class}">{esc(note)}</div>'
        '</section>', unsafe_allow_html=True,
    )

    one, two, three = st.columns(3, gap="small")
    with one:
        info_card("Programs", "3", "Connected from data preparation to star ratings")
    with two:
        info_card("Tables matched", f"{passed} of {total}" if run else "Not loaded",
                  "Passed against the original SAS results" if run else "Choose a saved run to view comparisons")
    with three:
        info_card("Data period", "July 2025", "Hospital data used for these calculations")

    st.write("")
    st.markdown('<div class="studio-section-title">How the calculations connect</div>'
                '<div class="studio-section-sub">Each program uses results from the one before it.</div>',
                unsafe_allow_html=True)
    flow = st.columns([5, .40, 5, .40, 5], gap="small")
    for program, column in zip((0, 1, 2), (flow[0], flow[2], flow[4])):
        title, description = PROGRAM_LABELS[program]
        state, detail = program_state(program, run)
        with column:
            with st.container(border=True, key=f"studio_surface_program_{program}"):
                st.caption(f"PROGRAM {program}")
                st.markdown(f'<div class="studio-card-head">{esc(title)}</div>', unsafe_allow_html=True)
                st.caption(description)
                if run is not None:
                    if state == "PASS":
                        st.markdown(f'<span class="studio-status-success">✓ {esc(detail)}</span>', unsafe_allow_html=True)
                    elif state == "FAIL":
                        st.markdown(f'<span class="studio-status-fail">{esc(detail)} · Needs review</span>', unsafe_allow_html=True)
                    else:
                        st.caption(detail)
                st.button(f"Open Program {program}", key=f"guided_program_{program}",
                          width="stretch", on_click=navigate_to_program, args=(program,))
    for arrow_column in (flow[1], flow[3]):
        with arrow_column:
            st.markdown('<div class="studio-flow-arrow" aria-hidden="true">→</div>', unsafe_allow_html=True)

    st.write("")
    next_steps, guide = st.columns([1.1, 1], gap="medium")
    with next_steps:
        with st.container(border=True, key="studio_surface_03"):
            st.markdown("### Where to go next")
            st.caption("Choose what you'd like to look at more closely.")
            st.button("See the code", key="guided_code", width="stretch",
                      on_click=navigate_to_view, args=("Code Explorer",))
            st.caption("Read what each calculation does, then compare the SAS and Python code.")
            st.button("Check the results", key="guided_results", width="stretch",
                      on_click=navigate_to_view, args=("Validation & Diagnostics",))
            st.caption("See which output tables matched SAS and how they were checked.")
            st.button("Explore hospital ratings", key="guided_hospital", width="stretch",
                      on_click=navigate_to_view, args=("Hospital Ratings",))
            st.caption("Choose a hospital to see the scores behind its rating.")
    with guide:
        with st.container(border=True, key="studio_surface_04"):
            st.markdown("### Quick guide")
            st.caption("A few terms you'll see throughout the demo.")
            st.markdown("**SAS** · The programming language used for the original hospital-rating calculations.")
            st.markdown("**Python** · The language we're translating those calculations into.")
            st.markdown("**Validation** · Checking new results against SAS, allowing for tiny numerical differences.")
            st.markdown("**Saved run** · Results recorded from a completed pipeline run. This demo doesn't rerun it.")
    with st.expander("Technical details about the checks"):
        st.write("The pipeline connects three dependent programs and compares ten Python output tables "
                 "with their SAS reference tables. The comparison uses numerical tolerances recorded "
                 "in the validation report. Hospital-level checks are separate from the full-dataset checks.")
        if run is not None:
            report = run.get("report", {})
            st.caption(f"Saved run: {Path(run['run_dir']).name} · Relative tolerance: "
                       f"{report.get('rtol', 'not recorded')} · Absolute tolerance: "
                       f"{report.get('atol', 'not recorded')}")


def code_page(run: dict | None) -> None:
    render_title("SEE THE CODE", "See how the code works",
                 "Choose a program and a calculation to view the original SAS code beside its Python version.")
    if not any(load_manifest(p) for p in (0, 1, 2)):
        st.warning("No program descriptions were found here. Place this file in the repository's demo/ folder.")
        st.code("manifests/program0.json\nmanifests/program1.json\nmanifests/program2.json", language=None)
        return
    controls = st.columns([1, 1.8], gap="small")
    with controls[0]:
        p = st.selectbox("Program", list(PROGRAM_LABELS),
                         format_func=lambda n: f"Program {n} · {PROGRAM_LABELS[n][0]}",
                         key="studio_program")
    manifest = load_manifest(p)
    if not manifest:
        st.warning(f"Program {p}'s description could not be loaded.")
        return
    with controls[1]:
        steps = [x for x in manifest if isinstance(x, dict) and x.get("id")]
        # The calculation list is scoped to the selected program's manifest.
        selected_id = st.selectbox(f"Calculation · Program {p}", [x["id"] for x in steps],
                                   format_func=lambda sid: next(
                                       (f"{s.get('title', sid)} · {sid}" for s in steps if s["id"] == sid), sid
                                   ), key=f"studio_step_{p}")
    step = next(x for x in steps if x["id"] == selected_id)
    code, code_location = python_source(p, run, selected_id)

    overview_col, explainer_col = st.columns([1, 1], gap="small")
    with overview_col:
        with st.container(border=True, key="studio_surface_05"):
            st.caption(f"PROGRAM {p}")
            st.markdown(f"### {PROGRAM_LABELS[p][0]}")
            st.write(PROGRAM_LABELS[p][1])
            st.caption(f"Selected calculation: {step.get('title') or selected_id}")
    with explainer_col:
        with st.container(border=True, key="studio_surface_06"):
            st.markdown("### What you're looking at")
            st.write("The left panel shows the original SAS code. The right shows its saved "
                     "Python implementation, created with AI assistance and reviewed by the team.")
            st.caption("This is a read-only comparison; no code runs when you open this page.")

    inputs = step.get("inputs") or []
    outputs = step.get("outputs") or []
    with st.container(border=True, key="studio_surface_07"):
        st.markdown(f"**This calculation: {step.get('title') or selected_id}**")
        if inputs or outputs:
            parts = []
            if inputs:
                parts.append(f"Uses {len(inputs)} declared input dataset{'s' if len(inputs) != 1 else ''}")
            if outputs:
                parts.append(f"produces {len(outputs)} output dataset{'s' if len(outputs) != 1 else ''}")
            st.write(" and ".join(parts) + ".")
        else:
            st.caption("The program description doesn't list input or output datasets for this calculation.")

    src_col, translated = st.columns(2, gap="medium")
    with src_col:
        with st.container(border=True, key="studio_surface_08"):
            st.markdown("### Original SAS")
            st.caption("The starting point")
            st.caption(f"{step.get('source_file', 'Source not specified')} · "
                       f"lines {step.get('line_start', '?')}–{step.get('line_end', '?')}")
            with st.container(height=380, border=False):
                st.code(step.get("sas_code") or "No SAS source snippet recorded for this calculation.", language=None, wrap_lines=True)
    with translated:
        with st.container(border=True, key="studio_surface_09"):
            st.markdown("### Python version")
            st.caption("Saved AI-assisted translation, including any team edits")
            if code is None:
                st.caption("No saved Python file was found for this calculation.")
                st.info("This step may not have a separate Python file, or its file isn't in the expected folder.")
            else:
                st.caption(code_location or "Saved file")
                with st.container(height=380, border=False):
                    st.code(code, language="python", wrap_lines=True)
    with st.expander("How this calculation fits into the larger process"):
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Depends on**")
            depends = step.get("depends_on") or []
            st.write(", ".join(map(str, depends)) if depends else "No declared step dependencies")
            st.markdown("**SAS inputs**")
            st.write(", ".join(map(str, inputs)) or "Not declared")
        with c2:
            st.markdown("**SAS outputs**")
            st.write(", ".join(map(str, outputs)) or "Not declared")
            step_report = (run or {}).get("steps", {}).get(p)
            entries = step_report.get("steps", []) if isinstance(step_report, dict) else []
            matching = next((r for r in entries if isinstance(r, dict) and r.get("id") == selected_id), None)
            st.markdown("**Saved execution status**")
            st.write(matching.get("status", "Not recorded") if matching else "Not available for this step")
    st.write("")
    with st.container(border=True, key="studio_surface_10"):
        st.markdown("**Next: Check the results**")
        st.caption("The code is one part of the picture. See whether the Python output tables matched SAS.")
        st.button("Open validation", type="primary", key="code_to_results",
                  on_click=navigate_to_view, args=("Validation & Diagnostics",))


def validation_page(run: dict | None) -> None:
    render_title("CHECK THE RESULTS", "See how the results were checked",
                 "Each Python output table is compared with its original SAS counterpart.")
    sample_notice(run)
    outputs = report_outputs(run)
    if run is None:
        st.info("No saved validation report is loaded. The table below lists the expected outputs, not passing results.")
    elif actual_run_has_complete_pass(run):
        blue_success("All 10 output tables passed validation for this saved run.")
    else:
        counts = summary_counts(run)
        st.warning(f"{counts['PASS']} of {len(outputs)} output tables passed. "
                   "Review the results below for differences or incomplete checks.")

    about_a, about_b, about_c = st.columns(3, gap="small")
    with about_a:
        with st.container(border=True, key="studio_surface_11"):
            st.markdown("**What was compared**")
            st.caption("Ten expected output tables from Programs 0, 1, and 2, each paired with its SAS reference.")
    with about_b:
        with st.container(border=True, key="studio_surface_12"):
            st.markdown("**How we checked**")
            st.caption("Table structure and numerical values are compared using the project's validation rules.")
    with about_c:
        with st.container(border=True, key="studio_surface_13"):
            st.markdown("**Why this matters**")
            st.caption("Code can run successfully and still calculate different results.")

    st.write("")
    st.markdown('<div class="studio-section-title">Validation results</div>'
                '<div class="studio-section-sub">Each row is one output table. Select a table below for detailed checks.</div>',
                unsafe_allow_html=True)
    records = []
    for program, names in PROGRAM_OUTPUTS.items():
        for name in names:
            item = outputs.get(name) if run else None
            if not isinstance(item, dict):
                item = {}
            records.append({
                "Program": f"Program {program}",
                "Output table": name,
                "Status": chip_status(item.get("status", "NOT_CHECKED")),
                "Largest absolute difference": item.get("largest_difference"),
            })
    results_col, guidance_col = st.columns([1.75, 1], gap="medium")
    with results_col:
        st.dataframe(clean_table(pd.DataFrame(records)), width="stretch", hide_index=True)
    with guidance_col:
        with st.container(border=True, key="studio_surface_14"):
            st.markdown("### How to read this")
            st.markdown("**Output table** · A dataset produced by one of the programs.")
            st.markdown("**Status** · Whether its comparison passed, failed, or wasn't completed.")
            st.markdown("**Largest difference** · The biggest reported numerical difference, when available.")
    if run is None:
        return

    report = run.get("report", {})
    with st.expander("Report details and status counts"):
        counts = summary_counts(run)
        cols = st.columns(4, gap="small")
        for col, (label, value) in zip(cols, [
            ("Passed", counts["PASS"]), ("Different", counts["FAIL"]),
            ("Errors", counts["ERROR"]), ("Not checked", counts["NOT_CHECKED"]),
        ]):
            with col:
                info_card(label, str(value))
        st.caption(f"Report: {Path(run['run_dir']).name} · Report status: {report.get('status', 'Unknown')} "
                   f"· Relative tolerance {report.get('rtol', 'unknown')} "
                   f"· Absolute tolerance {report.get('atol', 'unknown')}")
    with st.container(border=True, key="studio_surface_15"):
        st.markdown("### Look at one output")
        selected = st.selectbox("Output table", list(OUTPUT_TO_PROGRAM), key="studio_output")
        entry = outputs.get(selected)
        if not isinstance(entry, dict):
            st.warning("This output has no entry in the selected validation report.")
        else:
            st.write(f"**Program:** {entry.get('program', OUTPUT_TO_PROGRAM[selected])}  ·  "
                     f"**Status:** {chip_status(entry.get('status'))}  ·  "
                     f"**Matching key:** {entry.get('key') or 'row position'}")
            if entry.get("error"):
                err = entry["error"]
                st.error(str(err.get("message", "Validation error")) if isinstance(err, dict) else str(err))
            details = entry.get("validation")
            if isinstance(details, dict):
                stats = details.get("column_stats", {})
                if isinstance(stats, dict) and stats:
                    table = []
                    for name, values in stats.items():
                        if not isinstance(values, dict):
                            continue
                        table.append({"Column": name, "Maximum absolute difference": values.get("max_abs_diff"),
                                      "Mismatched values": values.get("mismatch_count", "—")})
                    st.dataframe(clean_table(pd.DataFrame(table)), width="stretch", hide_index=True)
                else:
                    with st.expander("See detailed validation evidence"):
                        st.json(details)
            else:
                st.caption("No numeric comparison details were recorded for this output.")

    with st.expander("When something doesn't match"):
        failing = [n for n, entry in outputs.items() if isinstance(entry, dict)
                   and safe_status(entry.get("status")) in ("FAIL", "ERROR")]
        if failing:
            st.warning(f"{len(failing)} output(s) require investigation: {', '.join(failing)}")
            st.caption("The project may suggest steps to inspect. These are leads, not confirmed causes.")
        else:
            st.caption("No failures were recorded among the checked outputs in this report.")
        st.divider()
        st.markdown("**Separate test example: a deliberately changed value**")
        st.caption("A saved mortality value for hospital 010001 was changed from 0.296110 to 0.546110 "
                   "for a discrepancy test. The system flagged the mismatch and suggested steps to inspect. "
                   "This is a test example, not an issue found in the current run.")
    st.write("")
    with st.container(border=True, key="studio_surface_16"):
        st.markdown("**Next: See what the calculations produce**")
        st.caption("Open a hospital's saved ratings and quality scores to see the output in context.")
        st.button("Explore hospital ratings", type="primary", key="results_to_hospital",
                  on_click=navigate_to_view, args=("Hospital Ratings",))


@st.cache_data(show_spinner=False)
def load_hospital_data(run_dir: str, provider_id: str) -> dict:
    from src.hospital_audit import audit_hospital
    return audit_hospital(run_dir, provider_id)


@st.cache_data(show_spinner=False)
def load_hospital_comparison(run_dir: str, provider_id: str) -> dict:
    from src.hospital_discrepancy import compare_hospital
    return compare_hospital(run_dir, provider_id)


def hospital_page(run: dict | None) -> None:
    render_title("HOSPITAL RATINGS", "See what the calculations produce",
                 "Start with a hospital to see its star rating, quality scores, and comparisons with SAS.")
    sample_notice(run)
    st.text_input("Hospital ID", value="010001", max_chars=6, key="studio_hospital",
                  help="Enter a six-digit hospital ID. 010001 is the example used in the project tests.")
    st.caption("Use the example ID 010001, or enter another six-digit ID to explore its available results.")
    if run is None:
        star, score, peer = 4, 0.1716, "5 quality groups"
        scores = GROUP_PREVIEW
        audit = None
        comparison = None
        provider = "010001"
    else:
        provider = st.session_state.get("studio_hospital", "010001").strip()
        if not (provider.isdigit() and len(provider) == 6):
            st.warning("Enter a six-digit hospital ID, including any zero at the beginning.")
            return
        try:
            with st.spinner("Loading hospital results …"):
                audit = load_hospital_data(run["run_dir"], provider)
        except (ImportError, ModuleNotFoundError, FileNotFoundError, ValueError, KeyError, OSError) as exc:
            st.error("We couldn't load this hospital's saved results. No example scores will be shown as real data.")
            st.caption(str(exc))
            return
        star = audit.get("star_rating")
        score = audit.get("summary_score")
        peer = audit.get("peer_group")
        scores = [{"Quality group": name, "Score": data.get("final_standardized_score")}
                  for name, data in audit.get("groups", {}).items()]
        try:
            comparison = load_hospital_comparison(run["run_dir"], provider)
        except (ImportError, ModuleNotFoundError, FileNotFoundError, ValueError, KeyError, OSError) as exc:
            comparison = None
            st.warning("We couldn't load the SAS comparison for this hospital.")
            st.caption(str(exc))
    st.markdown(f'<span class="studio-tag">Hospital {esc(provider)} · July 2025 · Read-only</span>',
                unsafe_allow_html=True)
    st.write("")
    a, b, c = st.columns(3, gap="small")
    with a:
        with st.container(border=True, key="studio_surface_17"):
            rating_text = f"{star} of 5" if star is not None else "Unrated"
            stars_html = ""
            if isinstance(star, (int, float)) and 1 <= star <= 5:
                stars_html = ('<div class="studio-rating-stars" aria-label="' +
                              esc(f"{int(star)} out of 5 stars") + '">' +
                              ('★' * int(star)) +
                              '<span class="unfilled">' + ('★' * (5-int(star))) +
                              '</span></div>')
            st.markdown(
                '<div class="studio-rating-label">Hospital star rating</div>'
                '<div class="studio-rating-row">'
                f'<span class="studio-rating-number">{esc(rating_text)}</span>'
                '</div>'
                f'{stars_html}',
                unsafe_allow_html=True,
            )
            if not stars_html:
                st.caption("Not available for this hospital")
    with b:
        with st.container(border=True, key="studio_surface_18"):
            st.metric("Summary score", f"{score:.4f}" if isinstance(score, (int, float)) else "Not available")
            st.caption("Combined quality score, not a percentage")
    with c:
        with st.container(border=True, key="studio_surface_19"):
            st.metric("Quality groups", str(len(scores)))
            st.caption(f"Compared with: {peer or 'Not recorded'}")
    st.write("")
    left, right = st.columns([1.55, 1], gap="medium")
    with left:
        with st.container(border=True, key="studio_surface_20"):
            st.markdown("### How the hospital scored")
            st.caption("The five quality areas compared with the reference average. These are not star ratings.")
            quality_plot(scores)
    with right:
        with st.container(border=True, key="studio_surface_21"):
            st.markdown("### How this rating was calculated")
            st.markdown("**Program 0** · Prepares and standardizes hospital measurements.")
            st.markdown("**Program 1** · Calculates scores across five areas of care.")
            st.markdown("**Program 2** · Combines the scores to produce star ratings.")
            st.caption("These three programs run in order, using the previous program's outputs.")
        validation_panel(run, comparison)
    # Reuse the same columns so the quality-area explorer follows the chart
    # immediately, rather than waiting below the taller validation column.
    with left:
        with st.container(border=True, key="studio_surface_22"):
            st.markdown("### Explore quality areas")
            st.caption("Choose an area to see the measurements behind its score.")
            names = [x["Quality group"] for x in scores]
            selected = st.selectbox("Quality group", names, label_visibility="collapsed")
            if audit is None:
                st.info("These are example scores for the preview. Load a saved run to see the underlying measurements.")
            else:
                detail = audit.get("groups", {}).get(selected, {})
                data_rows = [{"Measurement": name, "Program 0": values.get("program0"),
                              "Program 1": values.get("program1")}
                             for name, values in detail.get("measures", {}).items()]
                if data_rows:
                    st.dataframe(clean_table(pd.DataFrame(data_rows)), width="stretch", hide_index=True)
                else:
                    st.caption("No measurement records available for this group.")
                st.caption(f"Measurements: {detail.get('measure_count', 'unknown')} · "
                           "A quality score isn't the same thing as its contribution to the star rating.")
            if comparison is not None:
                with st.expander("See this hospital's SAS comparisons"):
                    checks = comparison.get("checks", [])
                    st.dataframe(clean_table(pd.DataFrame([{
                        "Program": x.get("program"), "Output": x.get("output"),
                        "Status": x.get("status"), "Different fields": x.get("difference_count"),
                    } for x in checks])), width="stretch", hide_index=True)
                    bad = [x for x in checks if x.get("status") == "FAIL"]
                    if bad:
                        st.markdown("**First step where a difference appeared**")
                        diag = comparison.get("diagnostics", {})
                        st.write(diag.get("earliest_failing_stage", "Unknown"))
                        st.caption("Possible steps to investigate, not confirmed causes:")
                        for entry in bad:
                            trace = diag.get("failed_outputs", {}).get(entry.get("output"), {})
                            st.write(f"{entry.get('output')}: " +
                                     ", ".join(trace.get("investigate_first", [])))
    with right:
        with st.container(border=True, key="studio_surface_23"):
            st.markdown("### About the data")
            st.markdown("**Time period**")
            st.caption("July 2025 hospital star-rating data")
            st.divider()
            st.markdown("**What we compare against**")
            st.caption("The original SAS results. This isn't an official CMS rating service.")
            st.divider()
            st.markdown("**What the checks cover**")
            st.caption("We check this hospital separately from the full dataset.")
            st.divider()
            st.markdown("**Allowed rounding differences**")
            if run:
                r = run.get("report", {})
                st.caption(f"Relative {r.get('rtol', 'unavailable')} · "
                           f"Absolute {r.get('atol', 'unavailable')}")
            else:
                st.caption("Shown when a real saved run is selected")
    st.write("")
    with st.container(border=True, key="studio_surface_24"):
        st.markdown("**Want to trace the calculations?**")
        st.caption("Compare the program code or review the saved validation report. "
                   "These pages show the pipeline-level evidence, not just this hospital's results.")
        trace_code, trace_validation = st.columns(2, gap="small")
        with trace_code:
            st.button("See the code", key="hospital_to_code", width="stretch",
                      on_click=navigate_to_program, args=(2,))
        with trace_validation:
            st.button("Check the results", key="hospital_to_validation", width="stretch",
                      on_click=navigate_to_view, args=("Validation & Diagnostics",))


# Shared controls and navigation. This is NOT a replacement for anyone else's app.
runs = list_runs()
st.session_state.setdefault("studio_view", "Migration Overview")
NAV_ITEMS = [
    ("Migration Overview", "Home"),
    ("Code Explorer", "See the code"),
    ("Validation & Diagnostics", "Check the results"),
    ("Hospital Ratings", "Explore hospital ratings"),
]


def friendly_run_name(path: Path) -> str:
    """Make machine-generated run IDs scannable without hiding their identity."""
    raw = path.name
    try:
        date_part, short_id = raw.split("_", 1)
        parsed = datetime.strptime(date_part, "%Y%m%dT%H%M%SZ")
        return f"{parsed:%b} {parsed.day}, {parsed:%Y} · {parsed:%H:%M} UTC · {short_id[:7]}"
    except (ValueError, IndexError):
        return raw


with st.sidebar:
    st.markdown('<div class="studio-brand">✳ &nbsp;AI Migration Studio</div>', unsafe_allow_html=True)
    st.markdown('<div class="studio-brand-sub">Hospital ratings · SAS to Python</div>',
                unsafe_allow_html=True)
    st.divider()
    st.caption("PAGES")
    for internal_name, display_label in NAV_ITEMS:
        st.button(display_label, key=f"nav_{internal_name}",
                  type="primary" if st.session_state["studio_view"] == internal_name else "secondary",
                  width="stretch", on_click=navigate_to_view, args=(internal_name,))
    st.divider()
    st.caption("DATA")
    if runs:
        selected_dir = st.selectbox("Saved run", runs, format_func=friendly_run_name,
                                    help="Choose a completed run to explore its saved results. This app does not rerun the code.")
        st.caption("Saved results only · nothing runs here")
    else:
        selected_dir = None
        st.caption("No saved run found · preview only")
    st.divider()
    st.caption("Preview · Read-only")
    with st.expander("About this demo"):
        st.caption("Explore saved code and results without making changes. "
                   "This is a separate demo; it doesn't run code, call Gemini, or edit the team's original app.")

current_run = load_run(str(selected_dir)) if selected_dir is not None else None
navigation = st.session_state["studio_view"]
if navigation == "Migration Overview":
    overview_page(current_run)
elif navigation == "Code Explorer":
    code_page(current_run)
elif navigation == "Validation & Diagnostics":
    validation_page(current_run)
else:
    hospital_page(current_run)
st.divider()
st.caption("Consulting 1D · Separate, read-only preview · Results come from the selected saved run")
