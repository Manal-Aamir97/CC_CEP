"""Tests for the statistics and the analysis pipeline (synthetic data only).

    python -m unittest analysis.test_analysis -v
"""
import csv
import math
import shutil
import tempfile
import unittest
from pathlib import Path

from analysis import stats
from analysis.analyse import analyse
from analysis.make_synthetic_logs import generate


class StatsTests(unittest.TestCase):
    def test_t_distribution_matches_table_values(self):
        for df, q in ((4, 2.776445105), (10, 2.228138852), (29, 2.045229642)):
            self.assertAlmostEqual(stats.t_cdf(q, df), 0.975, places=6)
            self.assertAlmostEqual(stats.t_quantile(0.975, df), q, places=5)
        self.assertAlmostEqual(stats.t_cdf(0.0, 7), 0.5, places=12)

    def test_paired_t(self):
        result = stats.paired_t([1, 2, 3, 4, 5])
        self.assertAlmostEqual(result["t"], 3 / (math.sqrt(2.5) / math.sqrt(5)), places=9)
        self.assertEqual(result["df"], 4)
        self.assertAlmostEqual(result["p"], 2 * (1 - stats.t_cdf(result["t"], 4)), places=12)
        low, high = result["ci95"]
        self.assertAlmostEqual((low + high) / 2, 3.0, places=9)
        self.assertAlmostEqual(result["cohen_dz"], 3 / math.sqrt(2.5), places=9)

    def test_wilcoxon_exact_values(self):
        self.assertAlmostEqual(stats.wilcoxon([1] * 0 + list(range(1, 11)))["p"], 2 / 1024, places=12)
        self.assertAlmostEqual(stats.wilcoxon([1, 2, 3, 4, 5, 6])["p"], 2 / 64, places=12)
        mixed = stats.wilcoxon([1, 2, 3, -4, 5])  # W- = 4: 7 of 32 sign patterns have W <= 4
        self.assertEqual((mixed["W_plus"], mixed["W_minus"], mixed["method"]), (11, 4, "exact"))
        self.assertAlmostEqual(mixed["p"], 14 / 32, places=12)
        self.assertAlmostEqual(mixed["rank_biserial"], (11 - 4) / 15, places=12)

    def test_wilcoxon_zeros_and_ties(self):
        result = stats.wilcoxon([0, 0, -1, -1, -2, 3])
        self.assertEqual((result["n_used"], result["n_zero"]), (4, 2))
        self.assertTrue(result["method"].startswith("normal"))
        self.assertEqual(stats.wilcoxon([0, 0])["p"], 1.0)

    def test_ranks_and_spearman(self):
        self.assertEqual(stats.ranks([10, 20, 20, 30]), [1, 2.5, 2.5, 4])
        self.assertAlmostEqual(stats.spearman([1, 2, 3, 4], [10, 20, 30, 40]), 1.0)
        self.assertAlmostEqual(stats.spearman([1, 2, 3, 4], [4, 3, 2, 1]), -1.0)


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.logs, cls.out = cls.tmp / "logs", cls.tmp / "out"
        generate(cls.logs, n=8, seed=3)
        cls.summary = analyse(cls.logs, cls.out)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_counts(self):
        self.assertEqual(self.summary, {"participants": 8, "complete": 8, "rows": 48, "synthetic": True})

    def test_outputs_exist_and_warn_about_synthetic_data(self):
        text = (self.out / "summary.md").read_text(encoding="utf-8")
        self.assertIn("SYNTHETIC DATA", text)
        self.assertIn("## Main comparison", text)
        for name in ("per_task.csv", "per_participant.csv", "advice_review.csv",
                     "figures/errors_by_condition.svg", "figures/category_by_condition.svg"):
            self.assertTrue((self.out / name).exists(), name)

    def test_per_participant_numbers_add_up(self):
        with open(self.out / "per_task.csv", encoding="utf-8") as f:
            tasks = list(csv.DictReader(f))
        with open(self.out / "per_participant.csv", encoding="utf-8") as f:
            people = list(csv.DictReader(f))
        for person in people:
            mine = [t for t in tasks if t["participant_id"] == person["participant_id"]]
            for cond in ("unassisted", "assisted"):
                total = sum(int(t["error_count"]) for t in mine if t["condition"] == cond)
                self.assertEqual(int(person[f"errors_{cond}"]), total)
            self.assertEqual(int(person["errors_setA"]) + int(person["errors_setB"]),
                             int(person["errors_unassisted"]) + int(person["errors_assisted"]))

    def test_existing_logs_are_never_overwritten(self):
        with self.assertRaises(SystemExit):
            generate(self.logs, n=2)


if __name__ == "__main__":
    unittest.main()
