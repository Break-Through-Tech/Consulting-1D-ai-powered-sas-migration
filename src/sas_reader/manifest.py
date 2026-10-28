"""Turn parsed SAS blocks into translator-ready steps (the manifest).

Output format = the hand-written manifests/program1.json, so the translator,
pipeline and tests read every generated manifest the same way. Each step has:

  id, title, sas_code, source_file, line_start, line_end, inputs, outputs,
  macros_called, macro_vars, notes, kind, translate, depends_on
  + sas_type ("data", "proc sql", "macro_call", ...) and status ("supported"/"unsupported")

Step kinds:
  context  setup only (%let, libname, %include, %put); never translated
  macro    a %macro definition from Star_Macros.sas that this program calls
  group    Program 1's pattern: PROC SQL that counts into &x, then a macro call using &x
  step     any other DATA step, PROC or macro call (all of Programs 0 and 2)

Dataset names are normalised (see dataset_name): macro variables resolved,
upper case; permanent R. tables use the answer-key name, temporary tables get WORK.
  R.Std_data_&year.&quarter._analysis  ->  STD_DATA_2025JUL_ANALYSIS
  Outcomes_mortality (temporary)       ->  WORK.OUTCOMES_MORTALITY
"""

import re
from pathlib import Path

from .parse import parse_text

ROOT = Path(__file__).resolve().parents[2]
SAS_DIR = ROOT / "data" / "Project_1" / "SAS Programs"
MACROS_FILE = SAS_DIR / "Star_Macros.sas"


# ---------------------------------------------------------------------------
# Macro variables
# ---------------------------------------------------------------------------

class MacroVars:
    """What we know about macro variables: static %let values and runtime setters.

    Our programs run one after another in the same SAS session, so Program 1
    and 2 use variables set in Program 0 (e.g. &year, &MEASURE_ANALYSIS,
    &measure_OM). That's why earlier programs are passed in as "globals".
    """

    def __init__(self):
        self.static = {}     # lower name -> raw value from %let
        self.runtime = {}    # lower name -> where it is set ("step id" or file name)

    def add_block(self, block, where):
        for name, value in block.vars_set.items():
            if value is None:
                self.runtime[name.lower()] = where
                self.static.pop(name.lower(), None)
            else:
                self.static[name.lower()] = value
                self.runtime.pop(name.lower(), None)

    def resolve(self, text):
        """Replace &name / &name. with %let values we know. Unknown ones stay as written."""
        for _ in range(10):                      # values can refer to other variables
            new = re.sub(r"&(\w+)\.?",
                         lambda m: self.static.get(m.group(1).lower(), m.group(0)), text)
            if new == text:
                break
            text = new
        return text

    def describe(self, name, set_here):
        """Value for the manifest's macro_vars field."""
        key = name.lower()
        if key in set_here:
            return "runtime (set in this step)"
        if key in self.static:
            return self.resolve(self.static[key])
        if key in self.runtime:
            return f"runtime (set by {self.runtime[key]})"
        return "unknown (not set in the files read)"


def dataset_name(raw, macro_vars):
    """Normalise a dataset name so every manifest names tables the same way.

    * R.x (the permanent library = the SAS answer-key folder) -> 'X'
        R.Outcome_mortality -> OUTCOME_MORTALITY,  &RESULTS -> STAR_2025JUL
      These match program1.json and the .sas7bdat / CSV answer-key files.
    * A table with no library is a temporary WORK table -> 'WORK.X'
        Program 0 makes a WORK table 'PtExp' (a measure list) and Program 1
        writes R.PtExp (group scores); without the prefix both would be 'PTEXP'.
    * A name we can't resolve (a macro parameter inside a macro) stays as written.
    """
    name = macro_vars.resolve(raw).rstrip(".")
    if "&" in name:
        return name
    if re.fullmatch(r"\w+\.\w+", name):
        lib, table = name.split(".", 1)
        return table.upper() if lib.lower() == "r" else f"{lib.upper()}.{table.upper()}"
    return f"WORK.{name.upper()}"


# ---------------------------------------------------------------------------
# Building the manifest
# ---------------------------------------------------------------------------

def _rel(path):
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return Path(path).as_posix()


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _leading_comment_start(first_line, prev_end, comments):
    """Where a step really starts, so its banner comment travels with it.

    Matches the hand-written program1.json: a step starts at the last
    '*****' section banner after the previous step; without a banner, at the
    comment lines touching the step. Header comments separated by a blank
    line are left out.
    """
    between = [c for c in comments if prev_end < c.first_line and c.last_line < first_line]
    banners = [c for c in between if c.is_banner]
    if banners:
        return banners[-1].first_line
    start = first_line
    for c in sorted(between, key=lambda c: c.last_line, reverse=True):
        if c.last_line == start - 1:
            start = c.first_line
    return start


def _bind_args(macro_def, args):
    """Match call arguments to the macro's parameters (positional or name=value)."""
    bound, pos = {}, 0
    params_lower = [p.lower() for p in macro_def.params]
    for arg in args:
        m = re.match(r"(\w+)\s*=\s*(.*)$", arg, re.S)
        if m and m.group(1).lower() in params_lower:
            bound[m.group(1).lower()] = m.group(2).strip()
        elif pos < len(params_lower):
            bound[params_lower[pos]] = arg
            pos += 1
    return bound


def _param_of(name):
    """'&out_avg' / '&indsn.' -> 'out_avg' / 'indsn'; non-parameter names -> None."""
    m = re.fullmatch(r"&(\w+)\.?", name)
    return m.group(1).lower() if m else None


def parse_program(sas_path, macros_path=MACROS_FILE, globals_paths=()):
    """Parse one SAS program into a list of manifest steps (dicts).

    sas_path      the program to parse (e.g. Program 0, 1 or 2)
    macros_path   file with the %macro definitions it calls (Star_Macros.sas)
    globals_paths earlier programs whose %let / INTO values this one uses
    """
    sas_path = Path(sas_path)
    lines = sas_path.read_text(encoding="utf-8", errors="replace").splitlines()
    blocks, comments = parse_text("\n".join(lines))

    # 1. Macro variables known before this program starts.
    mv = MacroVars()
    for g in globals_paths:
        for b in parse_text(Path(g).read_text(encoding="utf-8", errors="replace"))[0]:
            mv.add_block(b, Path(g).name)

    # 2. Macro definitions this program can call.
    macro_defs = {}
    if macros_path and Path(macros_path).exists():
        for b in parse_text(Path(macros_path).read_text(encoding="utf-8", errors="replace"))[0]:
            if b.kind == "macro_def":
                macro_defs[b.macro_name.lower()] = b

    # 3. Group blocks into program-level steps.
    #    Each item: {"kind", "blocks"}. %put right after a step is part of it
    #    (it only logs that step's result).
    items = []
    i = 0
    while i < len(blocks):
        b = blocks[i]
        nxt = blocks[i + 1] if i + 1 < len(blocks) else None
        is_put = b.kind == "context" and b.statements[0].text.lower().startswith("%put")

        if is_put and items and items[-1]["kind"] != "context":
            items[-1]["blocks"].append(b)
        elif b.kind in {"let", "context"}:
            if items and items[-1]["kind"] == "context":
                items[-1]["blocks"].append(b)        # consecutive setup lines = one step
            else:
                items.append({"kind": "context", "blocks": [b]})
        elif (b.kind == "proc" and b.proc_name == "sql" and b.vars_set and not b.outputs
              and nxt is not None and nxt.kind == "macro_call"
              and {v.lower() for v in b.vars_set} & {v.lower() for v in nxt.vars_used}):
            # Program 1 pattern: count measures INTO &x_cnt, then call the macro with &x_cnt.
            items.append({"kind": "group", "blocks": [b, nxt]})
            i += 1
        else:
            items.append({"kind": "step", "blocks": [b]})
        i += 1

    # 4. Turn items into manifest steps.
    steps, ids, producers = [], set(), {}      # producers: dataset -> step id that wrote it last
    macros_used = []

    def unique(base):
        base = base or "step"
        new, n = base, 2
        while new in ids:
            new, n = f"{base}_{n}", n + 1
        ids.add(new)
        return new

    prev_end = 0
    program_steps = []
    for item in items:
        bl = item["blocks"]
        main = bl[-1] if item["kind"] == "group" else bl[0]
        first, last = bl[0].first_line, bl[-1].last_line
        start = _leading_comment_start(first, prev_end, comments)
        prev_end = last

        set_here = {v.lower() for b in bl for v in b.vars_set}
        used = list(dict.fromkeys(v for b in bl for v in b.vars_used))
        inputs, outputs, notes, called = [], [], [], []
        for b in bl:
            notes += b.notes
            if b.kind == "macro_call":
                called.append(b.macro_name)
                mdef = macro_defs.get(b.macro_name.lower())
                if mdef is None:
                    notes.append(f"macro %{b.macro_name} is not defined in {Path(macros_path).name}")
                    continue
                bound = _bind_args(mdef, b.args)
                for raw in mdef.inputs:
                    p = _param_of(raw)
                    if p is None:
                        inputs.append(dataset_name(raw, mv))
                    elif p in bound:
                        inputs.append(dataset_name(bound[p], mv))
                for raw in mdef.outputs:
                    p = _param_of(raw)
                    if p in bound:
                        outputs.append(dataset_name(bound[p], mv))
            else:
                inputs += [dataset_name(x, mv) for x in b.inputs]
                outputs += [dataset_name(x, mv) for x in b.outputs]
        inputs, outputs = list(dict.fromkeys(inputs)), list(dict.fromkeys(outputs))

        unsupported = any(b.kind == "unsupported" for b in bl) or any(
            b.kind == "macro_call" and b.macro_name.lower() not in macro_defs for b in bl)

        if item["kind"] == "group":
            plain = [a for a in main.args if re.fullmatch(r"[A-Za-z_]\w*", a)]
            step_id = unique(_slug(plain[0]) if plain else f"call_{main.macro_name}")
            title = f"Group step: %{main.macro_name} for {plain[0] if plain else '?'}"
            sas_type = "proc sql + macro_call"
        elif item["kind"] == "context":
            step_id = unique(f"context_{first}")
            title = "Setup: " + ", ".join(dict.fromkeys(b.statements[0].text.split()[0] for b in bl))
            sas_type = "context"
        elif main.kind == "macro_call":
            step_id = unique(f"call_{main.macro_name}")
            title = f"Call %{main.macro_name}" + (f" -> {', '.join(outputs)}" if outputs else "")
            sas_type = "macro_call"
        else:
            label = (outputs or inputs or [""])[0]
            step_id = unique(_slug(f"{main.sas_type} {label}"))
            title = f"{main.sas_type.upper()}" + (f" -> {', '.join(outputs)}" if outputs else
                                                  f" (reads {', '.join(inputs)})" if inputs else "")
            sas_type = main.sas_type

        for name in called:
            if name.lower() in macro_defs and name.lower() not in macros_used:
                macros_used.append(name.lower())

        program_steps.append({
            "id": step_id,
            "title": title,
            "sas_code": "\n".join(lines[start - 1:last]),
            "source_file": _rel(sas_path),
            "line_start": start,
            "line_end": last,
            "inputs": inputs,
            "outputs": outputs,
            "macros_called": called,
            "macro_vars": {v: mv.describe(v, set_here) for v in used}
                          if item["kind"] != "context" else
                          {k: (mv.resolve(val) if val is not None else "runtime")
                           for b in bl for k, val in b.vars_set.items()},
            "notes": "; ".join(dict.fromkeys(notes)),
            "kind": item["kind"],
            "translate": item["kind"] != "context" and not unsupported,
            "depends_on": [],                           # filled below
            "sas_type": sas_type,
            "status": "unsupported" if unsupported else "supported",
            "_sets": set_here,
        })
        # Record what this program now knows (for later steps' macro_vars/depends_on).
        for b in bl:
            mv.add_block(b, step_id)

    # 5. Macro definition steps go first (the program steps call them).
    macro_steps = []
    for name in macros_used:
        d = macro_defs[name]
        def to_name(raw):
            p = _param_of(raw)
            return f"<{p} macro parameter>" if p else dataset_name(raw, mv)
        macro_steps.append({
            "id": unique(f"{d.macro_name}_macro"),
            "title": f"Macro %{d.macro_name} (definition)",
            "sas_code": "\n".join(Path(macros_path).read_text(encoding="utf-8", errors="replace")
                                  .splitlines()[d.first_line - 1:d.last_line]),
            "source_file": _rel(macros_path),
            "line_start": d.first_line,
            "line_end": d.last_line,
            "inputs": list(dict.fromkeys(to_name(x) for x in d.inputs)),
            "outputs": list(dict.fromkeys(to_name(x) for x in d.outputs)),
            "macros_called": [],
            "macro_vars": {p: "macro parameter" for p in d.params},
            "notes": "Translate once as a function; the call steps reuse it.",
            "kind": "macro",
            "translate": True,
            "depends_on": [],
            "sas_type": "macro",
            "status": "supported",
            "_sets": set(),
        })
    macro_id = {s["id"][:-len("_macro")].lower(): s["id"] for s in macro_steps}

    # 6. depends_on: the macro definitions a step calls, the earlier step that
    #    wrote each dataset it reads, and the earlier step that set each
    #    runtime macro variable it uses (e.g. PROC SQL INTO :measure_in).
    var_setter = {}
    for s in program_steps:
        deps = [macro_id[m.lower()] for m in s["macros_called"] if m.lower() in macro_id]
        if s["kind"] != "context":
            deps += [producers[x] for x in s["inputs"] if x in producers]
            deps += [var_setter[v.lower()] for v in s["macro_vars"] if v.lower() in var_setter
                     and v.lower() not in s["_sets"]]
        s["depends_on"] = [d for d in dict.fromkeys(deps) if d != s["id"]]
        if s["kind"] != "context":
            for x in s["outputs"]:
                producers[x] = s["id"]
            for v in s["_sets"]:
                var_setter[v] = s["id"]

    steps = macro_steps + program_steps
    for s in steps:
        s.pop("_sets")
    return steps
