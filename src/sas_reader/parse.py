"""Read one SAS file and split it into blocks (DATA steps, PROCs, macros, ...).

This is the low-level half of the SAS Reader. It knows SAS *syntax*, not our
pipeline: it turns text into a list of `Block` objects with line numbers, the
datasets each block reads and writes, and the macro variables it uses or sets.
`manifest.py` turns those blocks into translator-ready steps.

How it works (3 passes, each one small):
  1. scan_statements()  - walk the text once, drop comments, split at ';'
  2. build_blocks()     - group statements into DATA/PROC/macro/context blocks
  3. _fill_datasets()   - regexes that pull dataset names out of each block

Scope and limits (on purpose, see issue #36):
  * Only the SAS that appears in our 4 files under data/Project_1/SAS Programs.
    Parker confirmed a general SAS parser is not needed. Anything we don't
    recognise becomes an "unsupported" block instead of a guess.
  * Macros are NOT expanded or executed. A %macro body is parsed only to learn
    which datasets it reads/writes; a macro call is one block.
  * Macro variables set at run time (PROC SQL INTO :x, CALL SYMPUTX) are
    recorded as "runtime"; their values are not computed.
"""

import re
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Pass 1: statements
# ---------------------------------------------------------------------------

@dataclass
class Statement:
    text: str          # statement text with comments removed, without the ';'
    first_line: int    # 1-based line where the statement starts
    last_line: int     # 1-based line of its closing ';'


@dataclass
class Comment:
    first_line: int
    last_line: int
    is_banner: bool    # a "*******" section banner (used to find a step's start)


def scan_statements(text):
    """Split SAS source into statements and comments, keeping line numbers.

    SAS rules we follow (all of them appear in our files):
      * /* ... */ is a comment anywhere, even in the middle of a statement.
      * '*' at the START of a statement begins a comment that ends at the next
        ';' (e.g. the ***** banners and the stray '************;' on line 80
        of Program 1). A '*' in the middle of a statement is multiplication.
      * A ';' inside quotes does not end a statement ('' and "" both occur).
    """
    statements, comments = [], []
    i, line, n = 0, 1, len(text)
    buf, buf_start = [], None           # current statement text and start line

    def at_statement_start():
        return not "".join(buf).strip()

    while i < n:
        ch = text[i]

        # Block comment: skip it, but keep counting lines.
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end == -1 else end + 2
            chunk = text[i:end]
            comments.append(Comment(line, line + chunk.count("\n"), False))
            line += chunk.count("\n")
            buf.append(" ")             # a comment separates tokens like a space
            i = end
            continue

        # Statement comment: '*' as the first thing in a statement, up to ';'.
        if ch == "*" and at_statement_start():
            end = text.find(";", i)
            end = n if end == -1 else end + 1
            chunk = text[i:end]
            banner = chunk.lstrip().startswith("*****")
            comments.append(Comment(line, line + chunk.count("\n"), banner))
            line += chunk.count("\n")
            buf, buf_start = [], None
            i = end
            continue

        # Quoted string: copy it whole so a ';' inside cannot split it.
        if ch in "'\"":
            end = text.find(ch, i + 1)
            end = n if end == -1 else end + 1
            chunk = text[i:end]
            if buf_start is None:
                buf_start = line
            buf.append(chunk)
            line += chunk.count("\n")
            i = end
            continue

        if ch == ";":
            stmt = " ".join("".join(buf).split())   # collapse whitespace
            if stmt:
                statements.append(Statement(stmt, buf_start, line))
            buf, buf_start = [], None
            i += 1
            continue

        if ch == "\n":
            line += 1
        elif not ch.isspace() and buf_start is None:
            buf_start = line
        buf.append(ch)
        i += 1

    leftover = " ".join("".join(buf).split())
    if leftover:                         # text after the last ';' (not in our files)
        statements.append(Statement(leftover, buf_start, line))
    return statements, comments


# ---------------------------------------------------------------------------
# Pass 2: blocks
# ---------------------------------------------------------------------------

@dataclass
class Block:
    kind: str                  # "data", "proc", "macro_def", "macro_call", "let", "context", "unsupported"
    statements: list
    proc_name: str = ""        # e.g. "sql", "transpose" (PROC blocks only)
    macro_name: str = ""       # macro_def / macro_call
    params: list = field(default_factory=list)      # macro_def parameter names
    args: list = field(default_factory=list)        # macro_call arguments, as written
    inner: list = field(default_factory=list)       # macro_def: the blocks inside its body
    inputs: list = field(default_factory=list)      # dataset names as written (may contain &vars)
    outputs: list = field(default_factory=list)
    vars_used: list = field(default_factory=list)   # macro variables referenced (&x)
    vars_set: dict = field(default_factory=dict)    # name -> "static value" or None (= runtime)
    notes: list = field(default_factory=list)

    @property
    def first_line(self):
        return self.statements[0].first_line

    @property
    def last_line(self):
        return self.statements[-1].last_line

    @property
    def sas_type(self):
        """Short label used in titles and in the manifest's sas_type field."""
        if self.kind == "proc":
            return f"proc {self.proc_name}"
        return {"macro_def": "macro", "macro_call": "macro_call"}.get(self.kind, self.kind)


# Statements that only configure the session or write to the log. They are
# kept (so every line is accounted for) but never translated.
CONTEXT_WORDS = {"%put", "libname", "options", "title", "%include"}
# Macro-language words that are statements, not calls to one of our macros.
MACRO_KEYWORDS = {"%macro", "%mend", "%let", "%put", "%include", "%if", "%do", "%end", "%else", "%then"}


def _first_word(stmt):
    m = re.match(r"\s*(%?[A-Za-z_][\w]*)", stmt)
    return m.group(1).lower() if m else ""


def build_blocks(statements):
    """Group statements into blocks with a small state machine.

    Rules (SAS behaviour we rely on):
      * DATA or PROC opens a block; RUN or QUIT closes it.
      * A new DATA/PROC also closes an open block (SAS does this implicitly).
      * %macro ... %mend is one block; its body is parsed recursively.
      * "ods output x=y;" before a PROC belongs to that PROC (Star_Macros.sas).
      * %if/%do/%end are only supported inside a DATA step (Program 0, line 130).
    """
    blocks, current, pending_ods = [], None, []
    i = 0
    while i < len(statements):
        st = statements[i]
        word = _first_word(st.text)

        if word == "%macro":
            # Collect everything up to the matching %mend (our macros don't nest).
            if current:
                blocks.append(current)
                current = None
            body = []
            i += 1
            while i < len(statements) and _first_word(statements[i].text) != "%mend":
                body.append(statements[i])
                i += 1
            mend = statements[i] if i < len(statements) else st
            name, params = _parse_macro_header(st.text)
            blocks.append(Block("macro_def", [st] + body + [mend], macro_name=name,
                                params=params, inner=build_blocks(body)))
            i += 1
            continue

        if word in {"data", "proc"}:
            if current:
                blocks.append(current)
            proc = _first_word(st.text[4:]) if word == "proc" else ""
            current = Block(word, pending_ods + [st], proc_name=proc)
            pending_ods = []
        elif word in {"run", "quit"}:
            if current:
                current.statements.append(st)
                blocks.append(current)
                current = None
            else:
                blocks.append(Block("unsupported", [st], notes=["RUN/QUIT with no open step"]))
        elif current is not None:
            # Inside a DATA/PROC: everything until RUN/QUIT is part of it,
            # including %if/%do/%end and ods output.
            if word in {"%if", "%do", "%end", "%else"}:
                current.notes.append("contains %if/%do/%end (macro logic inside the step)")
            current.statements.append(st)
        elif word == "ods":
            pending_ods.append(st)
        elif word == "%let":
            blocks.append(Block("let", [st]))
        elif word in CONTEXT_WORDS:
            blocks.append(Block("context", [st]))
        elif word.startswith("%") and word not in MACRO_KEYWORDS:
            name = word[1:]
            blocks.append(Block("macro_call", [st], macro_name=name, args=_split_args(st.text)))
        else:
            blocks.append(Block("unsupported", [st], notes=[f"statement not recognised: {st.text[:60]}"]))
        i += 1

    if current:
        blocks.append(current)
    for b in blocks:
        _fill_datasets(b)
    return blocks


def _parse_macro_header(stmt):
    """'%macro kmeans(in=, out=, grp=)' -> ('kmeans', ['in', 'out', 'grp'])."""
    m = re.match(r"\s*%macro\s+(\w+)\s*(?:\((.*)\))?", stmt, re.I | re.S)
    name, raw = m.group(1), m.group(2) or ""
    params = [p.split("=")[0].strip() for p in raw.split(",") if p.strip()]
    return name, params


def _split_args(stmt):
    """'%grp_score(a, OM, b(x,y))' -> ['a', 'OM', 'b(x,y)']  (commas inside () don't split)."""
    start = stmt.find("(")
    if start == -1:
        return []
    inner = stmt[start + 1: stmt.rfind(")")]
    args, depth, cur = [], 0, ""
    for ch in inner:
        if ch == "," and depth == 0:
            args.append(cur.strip())
            cur = ""
            continue
        depth += ch == "("
        depth -= ch == ")"
        cur += ch
    if cur.strip():
        args.append(cur.strip())
    return args


# ---------------------------------------------------------------------------
# Pass 3: datasets and macro variables
# ---------------------------------------------------------------------------

# A dataset name as written in our files: R.less100_measure, All_data_&year.&quarter,
# &RESULTS, &out_avg ...  (letters, digits, _, &, and '.' for librefs/macro delimiters)
NAME = r"[A-Za-z_&][\w&.]*"


def _strip_options(text):
    """Remove dataset options in parentheses: 'a(drop=x) b(where=(y>1))' -> 'a b'."""
    out, depth = "", 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(depth - 1, 0)
        elif depth == 0:
            out += ch
    return out


def _names(text):
    """Dataset names in a list like 'a(opts) b nobs=n' (skips key=value options)."""
    return [w for w in _strip_options(text).split()
            if "=" not in w and re.fullmatch(NAME, w) and w.lower() != "_null_"]


def _after_keyword(text, keyword):
    """Every 'keyword=NAME' in a statement, e.g. data=, out=, seed=."""
    return re.findall(rf"\b{keyword}\s*=\s*({NAME})", text, re.I)


def _fill_datasets(b):
    """Set b.inputs / b.outputs / b.vars_used / b.vars_set from the block's text."""
    texts = [s.text for s in b.statements]
    for t in texts:
        # &name or &name. (the dot just ends the name). '&=NOBS' in %put is not a variable.
        for v in re.findall(r"&+(\w+)", t):
            if v not in b.vars_used:
                b.vars_used.append(v)

    if b.kind == "data":
        head = texts[0]
        b.outputs += _names(re.sub(r"^\s*data\s+", "", head, flags=re.I))
        for t in texts[1:]:
            # SET/MERGE may follow 'if _n_=1 then' (Star_Macros.sas), so search anywhere.
            m = re.search(r"\b(?:set|merge)\s+(.*)$", t, re.I)
            if m:
                b.inputs += _names(m.group(1))
            m = re.match(r"\s*call\s+symputx?\s*\(\s*'(\w+)'", t, re.I)
            if m:
                b.vars_set[m.group(1)] = None          # runtime value

    elif b.kind == "proc":
        for t in texts:
            if _first_word(t) == "ods":
                # ods output Summary=new  ->  'new' is written by this PROC
                b.outputs += re.findall(rf"\w+\s*=\s*({NAME})", _strip_options(t))
                continue
            b.inputs += _after_keyword(t, "data") + _after_keyword(t, "seed")   # seed= is a dataset in PROC FASTCLUS
            b.outputs += _after_keyword(t, "out")
        if b.proc_name == "sort" and not b.outputs:
            b.outputs = list(b.inputs)                 # PROC SORT without out= sorts in place
        if b.proc_name == "sql":
            for t in texts:
                m = re.search(rf"\bcreate\s+table\s+({NAME})", t, re.I)
                if m:
                    b.outputs.append(m.group(1))
                m = re.search(r"\bfrom\s+(.*?)(?:\bwhere\b|\bgroup\s+by\b|\border\s+by\b|$)", t, re.I)
                if m:
                    # 'from dat0, new' -> both are inputs (only the first word of each item; the rest is an alias)
                    b.inputs += [part.split()[0] for part in m.group(1).split(",") if part.split()]
                for v in re.findall(r"\binto\s*:\s*(\w+)", t, re.I):
                    b.vars_set[v] = None               # runtime value from the query

    elif b.kind == "let":
        m = re.match(r"\s*%let\s+(\w+)\s*=\s*(.*)$", texts[0], re.I | re.S)
        if m:
            b.vars_set[m.group(1)] = m.group(2).strip()

    elif b.kind == "macro_def":
        # A macro's own inputs/outputs: datasets read or written inside its body
        # that are not temporary tables it creates itself.
        created = set()
        for inner in b.inner:
            for name in inner.inputs:
                if name.lower() not in created and name not in b.inputs:
                    b.inputs.append(name)
            for name in inner.outputs:
                created.add(name.lower())
        # Outputs that matter to callers are the ones named by a parameter (&out_avg, &out ...).
        b.outputs = []
        for inner in b.inner:
            for name in inner.outputs:
                if name.startswith("&") and name not in b.outputs:
                    b.outputs.append(name)
        b.vars_used = [v for v in b.vars_used if v.lower() not in {p.lower() for p in b.params}]

    # De-duplicate while keeping order.
    b.inputs = list(dict.fromkeys(b.inputs))
    b.outputs = list(dict.fromkeys(b.outputs))


def parse_text(text):
    """Text of one SAS file -> (blocks, comments)."""
    statements, comments = scan_statements(text)
    return build_blocks(statements), comments
