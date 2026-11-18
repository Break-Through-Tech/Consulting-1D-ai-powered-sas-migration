"""The SAS procedure plug-ins reproduce SAS, including through a translation of %kmeans."""

import unittest

import numpy as np
import pandas as pd

from src import data_io, sas_procs
from src.baseline import program2


def kmeans_as_translated(in_df, grp):
    """%kmeans from Star_Macros.sas, step by step, the way a translation would call the plug-ins."""
    summary_score2 = in_df.copy()

    # proc univariate; output out=s1 pctlpre=P pctlpts=20 to 100 by 20;
    s1 = sas_procs.univariate_percentiles(summary_score2, "summary_score", range(20, 101, 20))

    # data s2: grp = 1 if .<summary_score<=P20, 2 if P20<summary_score<=P40, and so on.
    p = s1.iloc[0]
    score = summary_score2["summary_score"]
    s2 = summary_score2[["PROVIDER_ID", "summary_score"]].copy()
    s2["grp"] = np.select([score <= p["P20"], score <= p["P40"], score <= p["P60"], score <= p["P80"]],
                          [1, 2, 3, 4], 5)

    # proc means median; class grp; -> s33
    s33 = s2.groupby("grp", as_index=False)["summary_score"].median()

    # proc fastclus maxc=5 converge=0 maxiter=1000 seed=s33; then again from those
    # centers with strict=1.
    seeds2 = sas_procs.fastclus(summary_score2, "summary_score", s33,
                                maxc=5, maxiter=1000, converge=0).cluster_centers
    run2 = sas_procs.fastclus(summary_score2, "summary_score", seeds2,
                              maxc=5, maxiter=1000, converge=0, strict=1)

    # proc sort data=Cluster_mean by summary_score; star=_n_;
    cluster_sort = run2.cluster_centers.sort_values("summary_score").reset_index(drop=True)
    cluster_sort["star"] = np.arange(1, len(cluster_sort) + 1)

    # cluster=abs(cluster), merge by cluster, keep provider_ID Star &grp.
    clusters2 = run2.out.assign(CLUSTER=run2.out["CLUSTER"].abs())
    results = clusters2.merge(cluster_sort[["Cluster", "star"]], left_on="CLUSTER", right_on="Cluster")
    return results.sort_values("PROVIDER_ID")[["PROVIDER_ID", "star", grp]]


class TranslatedKmeansTests(unittest.TestCase):
    """With the plug-ins, a line-by-line translation of %kmeans gives SAS's stars."""

    def test_every_star_matches_sas(self):
        star = data_io.load_sas_output("star_2025jul")
        rated = 0
        for peer_group, sas in star[star["report_indicator"] == 1].groupby("cnt_grp"):
            with self.subTest(peer_group=peer_group):
                ours = kmeans_as_translated(sas[["PROVIDER_ID", "summary_score", "cnt_grp"]], "cnt_grp")
                expected = sas.sort_values("PROVIDER_ID")
                self.assertEqual(list(ours["PROVIDER_ID"]), list(expected["PROVIDER_ID"]))
                np.testing.assert_array_equal(ours["star"].to_numpy(), expected["star"].to_numpy())
                rated += len(ours)
        self.assertEqual(rated, 2891)


class FastclusMatchesBaselineTests(unittest.TestCase):
    """The baseline's fastclus is pinned to all 6 runs printed in "SAS Output.htm"."""

    def test_same_centers_and_clusters_as_the_baseline(self):
        star = data_io.load_sas_output("star_2025jul")
        for peer_group in program2.PEER_GROUPS.values():
            with self.subTest(peer_group=peer_group):
                scores = star.loc[star["cnt_grp"] == peer_group, "summary_score"].to_numpy(dtype=float)
                data = pd.DataFrame({"summary_score": scores})
                seeds = program2.quintile_median_seeds(scores)

                run1 = sas_procs.fastclus(data, "summary_score", pd.DataFrame({"summary_score": seeds}),
                                          maxc=5, maxiter=1000, converge=0)
                run2 = sas_procs.fastclus(data, "summary_score", run1.cluster_centers,
                                          maxc=5, maxiter=1000, converge=0, strict=1)
                baseline1, _ = program2.fastclus(scores, seeds)
                baseline2, baseline_clusters = program2.fastclus(scores, baseline1, strict=1)

                np.testing.assert_array_equal(run1.cluster_centers["summary_score"], baseline1)
                np.testing.assert_array_equal(run2.cluster_centers["summary_score"], baseline2)
                np.testing.assert_array_equal(run2.out["CLUSTER"].abs() - 1, baseline_clusters)
                self.assertTrue(run1.converged and run2.converged)


class PercentileTests(unittest.TestCase):
    def test_percentile_between_two_values_takes_the_next_one(self):
        # n=8: P40 is at 3.2, so PCTLDEF=5 uses the 4th value. numpy's default gives 2.8.
        self.assertEqual(sas_procs.percentile(np.arange(8.0), 40), 3.0)

    def test_percentile_on_a_value_averages_it_with_the_next(self):
        # n=10: P20 is exactly at 2, so PCTLDEF=5 averages the 2nd and 3rd values.
        self.assertEqual(sas_procs.percentile(np.arange(10.0), 20), 1.5)

    def test_0_and_100_are_the_min_and_max(self):
        self.assertEqual(sas_procs.percentile([3.0, 1.0, 2.0], 0), 1.0)
        self.assertEqual(sas_procs.percentile([3.0, 1.0, 2.0], 100), 3.0)

    def test_missing_values_are_left_out(self):
        self.assertEqual(sas_procs.percentile([np.nan, 0.0, 1.0, 2.0, 3.0], 50), 1.5)

    def test_unsupported_percentiles_raise(self):
        for pct in (20.5, 101, -1):
            with self.subTest(pct=pct), self.assertRaises(NotImplementedError):
                sas_procs.percentile([1.0, 2.0], pct)

    def test_univariate_output_has_one_column_per_percentile(self):
        s1 = sas_procs.univariate_percentiles(pd.DataFrame({"x": np.arange(10.0)}), "x", range(20, 101, 20))
        self.assertEqual(list(s1.columns), ["P20", "P40", "P60", "P80", "P100"])
        self.assertEqual(s1.iloc[0].tolist(), [1.5, 3.5, 5.5, 7.5, 9.0])


class FastclusTests(unittest.TestCase):
    @staticmethod
    def run_fastclus(values, seeds, **options):
        options = {"maxc": len(seeds), "maxiter": 1000, "converge": 0, **options}
        return sas_procs.fastclus(pd.DataFrame({"x": values}), "x", pd.DataFrame({"x": seeds}), **options)

    def test_strict_outlier_is_left_out_of_the_mean_and_numbered_negative(self):
        result = self.run_fastclus([0.0, 0.0, 0.0, 9.0], [0.0, 100.0], strict=1)
        self.assertEqual(result.cluster_centers["x"].tolist(), [0.0, 100.0])  # 2.25 if the outlier counted
        self.assertEqual(result.out["CLUSTER"].tolist(), [1, 1, 1, -1])
        self.assertEqual(result.out["DISTANCE"].tolist(), [0.0, 0.0, 0.0, 9.0])

    def test_without_strict_the_outlier_moves_the_mean(self):
        result = self.run_fastclus([0.0, 0.0, 0.0, 9.0], [0.0, 100.0])
        self.assertEqual(result.cluster_centers["x"].tolist(), [2.25, 100.0])
        self.assertEqual(result.out["CLUSTER"].tolist(), [1, 1, 1, 1])

    def test_out_keeps_the_input_rows_and_columns(self):
        data = pd.DataFrame({"PROVIDER_ID": ["b", "a", "c"], "x": [5.0, 0.0, 6.0]}, index=[7, 8, 9])
        out = sas_procs.fastclus(data, "x", pd.DataFrame({"x": [0.0, 5.0]}),
                                 maxc=2, maxiter=1000, converge=0).out
        self.assertEqual(list(out.columns), ["PROVIDER_ID", "x", "CLUSTER", "DISTANCE"])
        self.assertEqual(list(out.index), [7, 8, 9])
        self.assertEqual(out["CLUSTER"].tolist(), [2, 1, 2])
        self.assertEqual(out["DISTANCE"].tolist(), [0.5, 0.0, 0.5])  # final centers are 0 and 5.5
        self.assertNotIn("CLUSTER", data)

    def test_maxiter_stops_before_convergence(self):
        result = self.run_fastclus([0.0, 1.0, 10.0, 11.0, 30.0], [0.0, 11.0], maxiter=1)
        self.assertFalse(result.converged)
        self.assertEqual(result.cluster_centers["x"].tolist(), [0.5, 17.0])

    def test_unsupported_options_raise(self):
        cases = {
            "SAS's default CONVERGE=0.02": {"converge": 0.02},
            "fewer seeds than MAXC": {"maxc": 3},
            "MAXITER=0": {"maxiter": 0},
            "negative STRICT": {"strict": -1},
        }
        for name, options in cases.items():
            with self.subTest(name), self.assertRaises(NotImplementedError):
                self.run_fastclus([0.0, 1.0, 2.0], [0.0, 2.0], **options)

    def test_unsupported_data_raises(self):
        cases = {
            "equal seeds": ([0.0, 1.0], [1.0, 1.0]),
            "missing value": ([0.0, np.nan], [0.0, 1.0]),
            "missing seed": ([0.0, 1.0], [0.0, np.nan]),
        }
        for name, (values, seeds) in cases.items():
            with self.subTest(name), self.assertRaises(NotImplementedError):
                self.run_fastclus(values, seeds)

    def test_more_than_one_var_raises(self):
        data = pd.DataFrame({"x": [0.0, 1.0], "y": [0.0, 1.0]})
        with self.assertRaises(NotImplementedError):
            sas_procs.fastclus(data, ["x", "y"], data, maxc=2, maxiter=10, converge=0)


if __name__ == "__main__":
    unittest.main()
