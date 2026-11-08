"""Program 2 baseline translation reproduces the SAS outputs exactly."""

import unittest

import numpy as np
import pandas as pd

from src import data_io
from src.baseline import program0, program1, program2
from src.validate import compare_detailed

RTOL = ATOL = 1e-9  # same tolerance as the program 0 and program 1 baseline tests

# Every PROC FASTCLUS run printed in "SAS Output.htm", per peer group:
# (initial seeds run 1, cluster means run 1, cluster means run 2). Run 2 starts from
# the run 1 means. SAS prints about 10 significant digits, so these only compare
# to PRINTED_ATOL; the tests against the .sas7bdat files use the tighter tolerance.
PRINTED_ATOL = 1e-8
SAS_FASTCLUS = {
    "1) # of groups=3": (
        [-1.075897798, -0.369234379, -0.020160090, 0.242651559, 0.574409493],
        [-2.082680624, -0.960224623, -0.310787824, 0.214574155, 0.696221575],
        [-1.871330516, -0.960224623, -0.310787824, 0.214574155, 0.696221575],
    ),
    "2) # of groups=4": (
        [-0.4920550930, -0.0701243264, 0.1811880066, 0.4402623451, 0.8273162032],
        [-0.856673463, -0.260085297, 0.183353178, 0.637073971, 1.210515424],
        [-0.856673463, -0.260085297, 0.183353178, 0.637073971, 1.210515424],
    ),
    "3) # of groups=5": (
        [-0.6266777626, -0.2965118129, -0.0651943857, 0.1477890902, 0.4755826464],
        [-0.8899492643, -0.4555298134, -0.0990849135, 0.2458216615, 0.6715807741],
        [-0.8787331365, -0.4547061014, -0.0990849135, 0.2458216615, 0.6715807741],
    ),
}


def assert_matches_sas(df, name):
    # Compared against the .sas7bdat ground truth.
    pd.testing.assert_frame_equal(df, data_io.load_sas_output(name), check_dtype=False,
                                  check_exact=False, rtol=RTOL, atol=ATOL)


def sas_program1_outputs():
    return {name: data_io.load_sas_output(name) for name in program1.GROUP_OUTPUTS.values()}


class Program2Tests(unittest.TestCase):
    """Starts from the SAS program 1 tables, so only program 2 logic is under test."""

    @classmethod
    def setUpClass(cls):
        cls.result = program2.run(sas_program1_outputs())
        cls.star = cls.result["star_2025jul"]
        cls.sas_star = data_io.load_sas_output("star_2025jul")
        cls.national = cls.result["national_average_2025jul"]
        cls.sas_national = data_io.load_sas_output("national_average_2025jul")

    def test_outputs_match_sas(self):
        self.assertEqual(sorted(self.result), ["national_average_2025jul", "star_2025jul"])
        for name, df in self.result.items():
            with self.subTest(name=name):
                assert_matches_sas(df, name)

    def test_row_counts_match_sas(self):
        self.assertEqual(len(self.star), len(self.sas_star))
        self.assertEqual(len(self.star), 4566)
        self.assertEqual(len(self.national), len(self.sas_national))
        self.assertEqual(len(self.national), 1)

    def test_every_hospitals_star_matches_exactly(self):
        # Stars are categories, so no tolerance. Compared by hospital, not by position.
        ours = self.star.set_index("PROVIDER_ID")["star"]
        sas = self.sas_star.set_index("PROVIDER_ID")["star"]
        self.assertEqual(sorted(ours.index), sorted(sas.index))
        np.testing.assert_array_equal(ours.loc[sas.index].to_numpy(), sas.to_numpy())

    def test_star_counts(self):
        # Guards the test above against passing because nothing was rated.
        counts = self.star["star"].value_counts().sort_index().to_dict()
        self.assertEqual(counts, {1.0: 233, 2.0: 661, 3.0: 939, 4.0: 767, 5.0: 291})
        self.assertEqual(int(self.star["star"].isna().sum()), 1675)

    def test_summary_scores_match(self):
        ours = self.star.set_index("PROVIDER_ID")["summary_score"]
        sas = self.sas_star.set_index("PROVIDER_ID")["summary_score"]
        np.testing.assert_allclose(ours.loc[sas.index].to_numpy(), sas.to_numpy(), rtol=RTOL, atol=ATOL)

    def test_national_summary_average_matches(self):
        np.testing.assert_allclose(self.national["Summary_Score_Nat"], self.sas_national["Summary_Score_Nat"],
                                   rtol=RTOL, atol=ATOL)

    def test_star_passes_project_validator(self):
        result = compare_detailed(self.star, self.sas_star, "PROVIDER_ID")
        self.assertTrue(result["passed"], result["mismatches"])

    def test_national_average_passes_project_validator(self):
        # One row and no PROVIDER_ID, so the validator aligns rows by position.
        result = compare_detailed(self.national, self.sas_national, None)
        self.assertTrue(result["passed"], result["mismatches"])


class Program2FromOurProgram1Tests(unittest.TestCase):
    """The hand-written programs 0, 1 and 2 chained together from the SAS input."""

    def test_outputs_match_sas(self):
        p0 = program0.run(data_io.load_input())
        result = program2.run(program1.run(p0.std_data_analysis, p0.group_measures))
        for name, df in result.items():
            with self.subTest(name=name):
                assert_matches_sas(df, name)


class FastclusMatchesPrintedSasRuns(unittest.TestCase):
    """Each stage of %kmeans, against what PROC FASTCLUS printed for every peer group."""

    @classmethod
    def setUpClass(cls):
        star = data_io.load_sas_output("star_2025jul")
        cls.scores = {group: star.loc[star["cnt_grp"] == group, "summary_score"].to_numpy(dtype=float)
                      for group in SAS_FASTCLUS}

    def test_initial_seeds(self):
        # SAS printing the seeds we supply is the evidence that REPLACE=FULL changed nothing.
        for group, (seeds, _, _) in SAS_FASTCLUS.items():
            with self.subTest(group=group):
                np.testing.assert_allclose(program2.quintile_median_seeds(self.scores[group]), seeds,
                                           rtol=0, atol=PRINTED_ATOL)

    def test_cluster_means_for_both_runs(self):
        for group, (seeds, run1_means, run2_means) in SAS_FASTCLUS.items():
            with self.subTest(group=group):
                scores = self.scores[group]
                first, _ = program2.fastclus(scores, program2.quintile_median_seeds(scores))
                second, _ = program2.fastclus(scores, first, strict=program2.STRICT)
                np.testing.assert_allclose(first, run1_means, rtol=0, atol=PRINTED_ATOL)
                np.testing.assert_allclose(second, run2_means, rtol=0, atol=PRINTED_ATOL)


class SummaryScoreTests(unittest.TestCase):
    @staticmethod
    def tables(**scores):
        return {table: pd.DataFrame({"PROVIDER_ID": ["010001"], "grp_score": [scores.get(table, np.nan)],
                                     "total_cnt": [3.0]})
                for table in program2.SCORE_COLUMNS}

    def test_all_groups_present(self):
        out = program2.summary_scores(self.tables(outcome_mortality=1.0, outcome_readmission=2.0,
                                                  outcome_safety=3.0, ptexp=4.0, process=5.0))
        self.assertAlmostEqual(out.loc[0, "summary_score"], 0.22 * (1 + 2 + 3 + 4) + 0.12 * 5)

    def test_missing_group_weight_is_redistributed(self):
        # The example in the SAS comments: Safety missing, so 22/100 becomes 22/78.
        out = program2.summary_scores(self.tables(outcome_mortality=1.0, outcome_readmission=1.0,
                                                  ptexp=1.0, process=1.0))
        self.assertTrue(np.isnan(out.loc[0, "weight_Outcomes_Safety"]))
        self.assertAlmostEqual(out.loc[0, "weight_Outcomes_Mortality"], 22 / 78)
        self.assertAlmostEqual(out.loc[0, "weight_Process"], 12 / 78)
        self.assertAlmostEqual(out.loc[0, "summary_score"], 1.0)  # remaining weights sum to 1

    def test_no_groups_scores_zero_like_sas(self):
        # Every sum_weight_ave[k] is 0, and SAS sums them. Such a hospital never reports.
        out = program2.summary_scores(self.tables())
        self.assertEqual(out.loc[0, "summary_score"], 0.0)


class ReportTests(unittest.TestCase):
    @staticmethod
    def report_row(**counts):
        tables = {table: pd.DataFrame({"PROVIDER_ID": ["010001"], "total_cnt": [float(counts.get(table, 0))]})
                  for table in program2.COUNT_COLUMNS}
        return program2.report(tables).iloc[0]

    def test_three_groups_including_safety_reports(self):
        row = self.report_row(outcome_safety=3, ptexp=3, process=3)
        self.assertEqual((row["Total_measure_group_cnt"], row["MortSafe_Group_cnt"], row["report_indicator"]),
                         (3.0, 1.0, 1.0))

    def test_three_groups_without_mortality_or_safety_does_not_report(self):
        row = self.report_row(outcome_readmission=3, ptexp=3, process=3)
        self.assertEqual((row["Total_measure_group_cnt"], row["MortSafe_Group_cnt"], row["report_indicator"]),
                         (3.0, 0.0, 0.0))

    def test_two_measures_do_not_count_as_a_group(self):
        row = self.report_row(outcome_mortality=3, outcome_safety=3, ptexp=2)
        self.assertEqual((row["Total_measure_group_cnt"], row["report_indicator"]), (2.0, 0.0))


class QuintileSeedTests(unittest.TestCase):
    def test_percentile_between_two_values_takes_the_next_one(self):
        # n=8: P40 is at 3.2, so PCTLDEF=5 uses the 4th value (3) and 3 falls in quintile 2.
        # numpy's default percentile would give 2.8 here and move 3 to quintile 3.
        np.testing.assert_array_equal(program2.quintile_median_seeds(np.arange(8.0)),
                                      [0.5, 2.5, 4.0, 5.5, 7.0])

    def test_percentile_on_a_value_averages_it_with_the_next(self):
        # n=10: P20 is exactly at 2, so PCTLDEF=5 averages the 2nd and 3rd values (1.5).
        np.testing.assert_array_equal(program2.quintile_median_seeds(np.arange(10.0)),
                                      [0.5, 2.5, 4.5, 6.5, 8.5])


class FastclusTests(unittest.TestCase):
    def test_strict_leaves_outlier_out_of_means_but_still_labels_it(self):
        scores = np.array([0.0, 0.0, 0.0, 9.0])
        centers, clusters = program2.fastclus(scores, np.array([0.0, 100.0]), strict=1.0)
        self.assertEqual(centers[0], 0.0)  # would be 2.25 if the outlier counted
        np.testing.assert_array_equal(clusters, [0, 0, 0, 0])

    def test_without_strict_outlier_moves_the_mean(self):
        centers, _ = program2.fastclus(np.array([0.0, 0.0, 0.0, 9.0]), np.array([0.0, 100.0]))
        self.assertEqual(centers[0], 2.25)

    def test_stars_rank_clusters_by_mean(self):
        # Five tight, well separated clusters listed out of order.
        scores = np.array([50.0] * 5 + [10.0] * 5 + [40.0] * 5 + [20.0] * 5 + [30.0] * 5)
        np.testing.assert_array_equal(program2.kmeans(scores),
                                      [5] * 5 + [1] * 5 + [4] * 5 + [2] * 5 + [3] * 5)


class GroupOutputNameTests(unittest.TestCase):
    """Program 2 should accept uppercase and lowercase group output names."""

    def test_uppercase_names_give_the_same_result(self):
        lower = sas_program1_outputs()
        upper = {name.upper(): frame for name, frame in lower.items()}

        expected = program2.run(lower)
        actual = program2.run(upper)

        for name in expected:
            with self.subTest(name=name):
                pd.testing.assert_frame_equal(expected[name], actual[name])

    def test_public_functions_accept_uppercase_names(self):
        upper = {
            name.upper(): frame
            for name, frame in sas_program1_outputs().items()
        }

        program2.summary_scores(upper)
        program2.report(upper)

    def test_national_average_accepts_uppercase_names(self):
        lower = sas_program1_outputs()
        upper = {name.upper(): frame for name, frame in lower.items()}

        star = program2.run(lower)["star_2025jul"]

        pd.testing.assert_frame_equal(
            program2.national_average(star, lower),
            program2.national_average(star, upper),
        )

    def test_names_differing_only_by_case_are_rejected(self):
        outputs = sas_program1_outputs()
        name, frame = next(iter(outputs.items()))

        outputs[name.upper()] = frame

        with self.assertRaisesRegex(ValueError, name.lower()):
            program2.run(outputs)


if __name__ == "__main__":
    unittest.main()
