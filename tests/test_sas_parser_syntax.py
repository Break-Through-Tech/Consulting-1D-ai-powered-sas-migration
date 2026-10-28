"""One test per SAS construct the parser supports (issue #36).

Each snippet is copied from, or modelled on, a real line in our 4 SAS files,
so these tests document exactly what the parser handles. Parser correctness
only: nothing here translates or runs Python.

Run:  python -m pytest tests/test_sas_parser_syntax.py -q
"""

import tempfile
import unittest
from pathlib import Path

from src.sas_reader.manifest import parse_program
from src.sas_reader.parse import parse_text, scan_statements


def blocks(sas):
    return parse_text(sas)[0]


def program(sas, macros=None, globals_sas=None):
    """Parse SAS text as a program file (optionally with a macro file / earlier program)."""
    tmp = Path(tempfile.mkdtemp())
    (tmp / "prog.sas").write_text(sas)
    macros_path = None
    if macros:
        macros_path = tmp / "macros.sas"
        macros_path.write_text(macros)
    globals_paths = []
    if globals_sas:
        (tmp / "g.sas").write_text(globals_sas)
        globals_paths = [tmp / "g.sas"]
    return parse_program(tmp / "prog.sas", macros_path, globals_paths)


class CommentsAndStatements(unittest.TestCase):
    def test_block_comment_inside_statement_is_ignored(self):
        # Program 1 line 34: into: measure_OM_cnt /*number of measures in this domain*/
        stmts, _ = scan_statements("proc sql;\n select x into: a /*note; with semicolon*/\n from t;\nquit;")
        self.assertEqual([s.text for s in stmts], ["proc sql", "select x into: a from t", "quit"])

    def test_star_comment_only_at_statement_start(self):
        # '*...;' banners are comments; '*' inside a statement is multiplication.
        stmts, comments = scan_statements("*****\n* Banner *\n*****;\ndata a; x = 2 * y; run;")
        self.assertEqual([s.text for s in stmts], ["data a", "x = 2 * y", "run"])
        self.assertTrue(comments[0].is_banner)

    def test_stray_star_comment_after_statement(self):
        # Program 1 line 80: %grp_score(...);************;
        stmts, comments = scan_statements("%m(a);************;")
        self.assertEqual([s.text for s in stmts], ["%m(a)"])
        self.assertEqual(len(comments), 1)

    def test_semicolon_inside_quotes(self):
        stmts, _ = scan_statements("title 'a;b';\nrun;")
        self.assertEqual(stmts[0].text, "title 'a;b'")

    def test_line_numbers(self):
        stmts, _ = scan_statements("\n\nproc sql;\n select 1\n from t;\nquit;")
        self.assertEqual((stmts[1].first_line, stmts[1].last_line), (4, 5))


class DataSteps(unittest.TestCase):
    def test_one_line_data_step(self):
        # Program 0 line 108
        (b,) = blocks("DATA R.less100_measure;SET less100_measure;RUN;*0;")
        self.assertEqual((b.kind, b.outputs, b.inputs), ("data", ["R.less100_measure"], ["less100_measure"]))

    def test_data_null_with_call_symputx(self):
        # Program 0 lines 118-121
        (b,) = blocks("DATA _null_;\n call symputx('NOBS',NOBS);\n set Less100_measure nobs=NOBS;\nRUN;")
        self.assertEqual(b.outputs, [])
        self.assertEqual(b.inputs, ["Less100_measure"])
        self.assertEqual(b.vars_set, {"NOBS": None})            # None = runtime value

    def test_dataset_options_are_stripped(self):
        (b,) = blocks("DATA outcomes_mortality (where = (col1>100)); SET t(drop=a rename=(b=c)); RUN;")
        self.assertEqual((b.outputs, b.inputs), (["outcomes_mortality"], ["t"]))

    def test_merge_and_conditional_set(self):
        # Star_Macros.sas: 'merge a b; by id;' and 'if _n_=1 then set s1;'
        (b,) = blocks("data r; merge a(keep=x) b; by id; if _n_=1 then set s1; run;")
        self.assertEqual(b.inputs, ["a", "b", "s1"])

    def test_if_do_end_inside_data_step(self):
        # Program 0 lines 126-133: the only %if in our files
        (b,) = blocks("DATA x; SET y;\n %if &NOBS>0 %then %do;\n drop &measure_exclude;\n %end;\nRUN;")
        self.assertEqual(b.kind, "data")
        self.assertIn("contains %if/%do/%end", b.notes[0])
        self.assertIn("measure_exclude", b.vars_used)

    def test_new_step_closes_open_step(self):
        # SAS ends a DATA step at the next DATA/PROC even without RUN.
        bs = blocks("data a; set b;\nproc sort data=a; by x; run;")
        self.assertEqual([b.kind for b in bs], ["data", "proc"])


class Procs(unittest.TestCase):
    def test_proc_data_and_out(self):
        (b,) = blocks("PROC TRANSPOSE data=Measure_volume0 out=measure_volume_t0; RUN;")
        self.assertEqual((b.proc_name, b.inputs, b.outputs), ("transpose", ["Measure_volume0"], ["measure_volume_t0"]))

    def test_proc_sort_in_place(self):
        (b,) = blocks("proc sort data=report_indicator; by provider_id; run;")
        self.assertEqual(b.outputs, ["report_indicator"])

    def test_output_statement_and_fastclus_seed(self):
        # Star_Macros.sas %kmeans
        (u,) = blocks("proc univariate data=s noprint; var x; output out=s1 pctlpre=P pctlpts=20 to 100 by 20; run;")
        self.assertEqual(u.outputs, ["s1"])
        (f,) = blocks("proc fastclus data=s maxc=5 out=clusters seed=seeds2 strict=1; var x; run;")
        self.assertEqual((f.inputs, f.outputs), (["s", "seeds2"], ["clusters"]))

    def test_ods_output_before_proc(self):
        # Star_Macros.sas %grp_score: 'ods output summary=new(drop=variable);' then PROC MEANS
        (b,) = blocks("ods output summary=new(drop=variable);\nproc means data=dat0 mean std; var avg; run;")
        self.assertEqual((b.inputs, b.outputs), (["dat0"], ["new"]))

    def test_sql_into_from_and_create_table(self):
        (a,) = blocks("PROC SQL; select count(m) into: measure_cnt from include_measure0; QUIT;")
        self.assertEqual((a.inputs, a.outputs, a.vars_set), (["include_measure0"], [], {"measure_cnt": None}))
        (b,) = blocks("proc sql; create table dat2 as select * from dat0, new; quit;")
        self.assertEqual((b.inputs, b.outputs), (["dat0", "new"], ["dat2"]))


class Macros(unittest.TestCase):
    MACROS = (
        "%macro kmeans(in=, out=, grp=);\n data s2; set &in; run;\n data &out; set s2; run;\n%mend kmeans;\n"
        "%macro grp(indsn, gp, out_avg);\n data &out_avg; set &indsn.; run;\n%mend;\n"
    )

    def test_macro_definition(self):
        (k, g) = blocks(self.MACROS)
        self.assertEqual((k.kind, k.macro_name, k.params), ("macro_def", "kmeans", ["in", "out", "grp"]))
        self.assertEqual((k.inputs, k.outputs), (["&in"], ["&out"]))  # s2 is internal, not listed
        self.assertEqual(g.params, ["indsn", "gp", "out_avg"])

    def test_keyword_and_positional_calls(self):
        steps = program("%kmeans(in=cnt_s10, out=cnt_s1, grp=cnt_grp);\n%grp(R.In, OM, R.Outcome_x);\n",
                        macros=self.MACROS)
        by_id = {s["id"]: s for s in steps}
        self.assertEqual(by_id["call_kmeans"]["inputs"], ["WORK.CNT_S10"])
        self.assertEqual(by_id["call_kmeans"]["outputs"], ["WORK.CNT_S1"])
        self.assertEqual(by_id["call_kmeans"]["depends_on"], ["kmeans_macro"])
        self.assertEqual(by_id["call_grp"]["outputs"], ["OUTCOME_X"])
        # Macro definitions come first and only the ones this program calls.
        self.assertEqual([s["kind"] for s in steps[:2]], ["macro", "macro"])

    def test_let_resolution_and_names(self):
        # Program 0: %LET year=2025; %LET quarter=Jul; DATA All_data_&year.&quarter;
        steps = program("DATA All_data_&year.&quarter; SET R.x_&year.&quarter._analysis; RUN;",
                        globals_sas="%LET year = 2025;\n%LET quarter = Jul;\n")
        self.assertEqual(steps[0]["outputs"], ["WORK.ALL_DATA_2025JUL"])     # temporary table
        self.assertEqual(steps[0]["inputs"], ["X_2025JUL_ANALYSIS"])         # permanent R. table
        self.assertEqual(steps[0]["macro_vars"], {"year": "2025", "quarter": "Jul"})

    def test_group_pattern(self):
        # Program 1: PROC SQL count INTO &x_cnt, then a macro call using &x_cnt -> one "group" step.
        sas = ("*****\n* Outcomes - Mortality *\n*****;\nPROC SQL; select count(m) into: m_cnt from t; QUIT;\n"
               "%grp(R.In, OM, R.Outcome_x, &m_cnt);\n")
        steps = program(sas, macros="%macro grp(indsn, gp, out_avg, n);\n data &out_avg; set &indsn; run;\n%mend;")
        group = steps[-1]
        self.assertEqual((group["id"], group["kind"], group["line_start"]), ("om", "group", 1))
        self.assertEqual(group["macro_vars"]["m_cnt"], "runtime (set in this step)")

    def test_unknown_statement_is_unsupported(self):
        steps = program("x = 1;\n")
        self.assertEqual(steps[0]["status"], "unsupported")
        self.assertFalse(steps[0]["translate"])

    def test_put_joins_previous_step(self):
        steps = program("PROC SQL; select a into: v from t; QUIT;\n%put &v;\n")
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["line_end"], 2)


if __name__ == "__main__":
    unittest.main()
