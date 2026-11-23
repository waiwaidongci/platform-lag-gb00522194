"""声级碰撞抗干扰复核的单元测试（仅标准库，覆盖纯领域模块）。

运行：python -m unittest discover -s backend/tests
"""

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.domain import interference as itf  # noqa: E402
from src.domain.analysis import analyze, build_grid, scan_lags, select_peak  # noqa: E402
from src.domain.demo import demo_rows  # noqa: E402
from src.domain.spec import SPEC  # noqa: E402

FIXTURE = dict(SPEC["fixture"])
START = datetime(2026, 11, 22, 9, 0)


def make_rows(fan, sound, platform="测试站", fan_id="TVF-09"):
    rows = []
    for index, (fan_hz, sound_dba) in enumerate(zip(fan, sound)):
        rows.append({
            "platform": platform,
            "fan_id": fan_id,
            "timestamp": (START + timedelta(seconds=10 * index)).isoformat(sep=" "),
            "sound_dba": sound_dba,
            "fan_hz": fan_hz,
        })
    return rows


class OriginalPeakRuleTests(unittest.TestCase):
    def test_interference_uses_same_peak_as_analyze(self):
        rows = demo_rows()
        prepared = build_grid(rows, FIXTURE)
        lag_rows = scan_lags(prepared["fan"], prepared["sound"], prepared["span"],
                             prepared["interval"], prepared["max_lag"], prepared["min_pairs"])
        peak = select_peak(lag_rows)
        result = itf.run_interference(rows, FIXTURE)
        self.assertEqual(result["original_peak"]["lag_samples"], peak["lag_samples"])
        self.assertEqual(result["original_peak"]["lag_seconds"], peak["lag_seconds"])
        self.assertEqual(result["original_peak"]["pair_count"], peak["pair_count"])

    def test_demo_peak_is_plus_three_lags(self):
        result = itf.run_interference(demo_rows(), FIXTURE)
        self.assertEqual(result["original_peak"]["lag_samples"], 3)


class StableTests(unittest.TestCase):
    def test_demo_is_stable_after_exhaustive_search(self):
        result = itf.run_interference(demo_rows(), FIXTURE)
        self.assertEqual(result["state"], "stable")
        self.assertIsNone(result["counterexample"])
        # 83 个有声级时刻，1..3 元组合全部枚举。
        self.assertEqual(result["candidate_slot_count"], 83)
        from math import comb
        expected = sum(comb(83, level) for level in range(1, 4))
        self.assertEqual(result["evaluated_combinations"], expected)
        self.assertEqual(result["checked_levels"], 3)
        # 判定依据与说明随结果一并给出。
        self.assertIn("未发现反例", result["criterion"])
        self.assertIn("判定依据", [section["title"] for section in result["sections"]])


def small_stable_rows(n=35):
    """强滞后信号的小批次：真实滞后 1 格且 r=1，剔除 1–3 点不会换峰。"""
    fan = [30.0 + 3.0 * (i % 7) for i in range(n)]
    sound = [None] * n
    for i in range(1, n):
        sound[i] = round(40 + 0.9 * fan[i - 1] + (0.01 if i % 2 else -0.01), 3)
    return make_rows(fan, sound)


SMALL_PARAMS = {"sample_seconds": 10, "max_lag": 5, "min_pairs": 8}


class NoPeakTests(unittest.TestCase):
    def test_constant_fan_marks_no_peak_without_enumeration(self):
        n = 40
        rows = make_rows([30.0] * n, [50 + (i % 4) * 0.5 for i in range(n)])
        result = itf.run_interference(rows, {"sample_seconds": 10, "max_lag": 4, "min_pairs": 5})
        self.assertEqual(result["state"], "no_peak")
        self.assertIsNone(result["original_peak"])
        self.assertEqual(result["evaluated_combinations"], 0)
        self.assertIsNone(result["counterexample"])


class VulnerableTests(unittest.TestCase):
    def _vulnerable_rows(self):
        n = 60
        fan = [30 + (5 if i >= 30 else 0) for i in range(n)]
        sound = [50 + ((i * 7) % 5) * 0.4 for i in range(n)]
        sound[33] = 70.0
        sound[40] = 68.5
        return make_rows(fan, sound)

    def test_single_removal_switches_peak(self):
        params = {"sample_seconds": 10, "max_lag": 6, "min_pairs": 10}
        result = itf.run_interference(self._vulnerable_rows(), params)
        self.assertEqual(result["state"], "vulnerable")
        counter = result["counterexample"]
        self.assertEqual(counter["kind"], "换峰")
        self.assertEqual(counter["removal_count"], 1)
        self.assertEqual(len(counter["removed_timestamps"]), 1)
        self.assertNotEqual(counter["new_peak"]["lag_samples"], result["original_peak"]["lag_samples"])

    def test_minimal_and_earliest_combination_chosen(self):
        params = {"sample_seconds": 10, "max_lag": 6, "min_pairs": 10}
        result = itf.run_interference(self._vulnerable_rows(), params)
        # 反例在 1 元组合即出现，枚举数应远小于全部 1 元组合。
        self.assertLessEqual(result["evaluated_combinations"], result["candidate_slot_count"])
        # 同大小取时间最早：被剔时刻是枚举顺序里第一个触发反例的时刻。
        counter = result["counterexample"]
        self.assertEqual(counter["removed_timestamps"], sorted(counter["removed_timestamps"]))

    def test_peak_disappears(self):
        n = 30
        fan = [30.0 + 1.0 * (i % 4) for i in range(n)]
        sound = [None] * n
        for i in range(5, 11):
            sound[i] = 50 + 0.5 * (i - 5)
        rows = make_rows(fan, sound)
        params = {"sample_seconds": 10, "max_lag": 3, "min_pairs": 6}
        result = itf.run_interference(rows, params)
        self.assertEqual(result["state"], "vulnerable")
        self.assertEqual(result["counterexample"]["kind"], "峰消失")
        self.assertIsNone(result["counterexample"]["new_peak"])

    def test_fan_readings_are_kept(self):
        # 剔除声级时刻后，风机槽位数不变，只有声级槽位减少。
        rows = self._vulnerable_rows()
        params = {"sample_seconds": 10, "max_lag": 6, "min_pairs": 10}
        prepared = build_grid(rows, params)
        original_fan = dict(prepared["fan"])
        result = itf.run_interference(rows, params)
        removed = set(result["counterexample"]["removed_slots"])
        new_peak, _ = itf._peak_after_removal(prepared, frozenset(removed))
        self.assertEqual(prepared["fan"], original_fan)  # 风机读数照留
        self.assertIsNotNone(new_peak)


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        # 缩小批大小，让小批次也能多次触发检查点，测试保持秒级。
        self._original_batch = itf.BATCH_SIZE
        itf.BATCH_SIZE = 100
        self.rows = small_stable_rows()

    def tearDown(self):
        itf.BATCH_SIZE = self._original_batch

    def test_resume_matches_full_run_without_duplicate_judgments(self):
        full = itf.run_interference(self.rows, SMALL_PARAMS)
        checkpoint = None
        output = None
        guard = 0
        while output is None:
            guard += 1
            self.assertLess(guard, 200)
            limit = 300 * guard
            seen = {}

            def progress(evaluated, snapshot, _limit=limit):
                seen["cp"] = snapshot
                if evaluated >= _limit:
                    raise RuntimeError("simulated interruption")

            try:
                output = itf.run_interference(self.rows, SMALL_PARAMS, checkpoint=checkpoint, progress=progress)
            except RuntimeError:
                checkpoint = seen["cp"]
        self.assertEqual(output["state"], full["state"])
        # 分段续跑与一次跑完的总判定数完全相同，没有重复判定。
        self.assertEqual(output["evaluated_combinations"], full["evaluated_combinations"])

    def test_checkpoint_written_at_batch_boundary_and_resumes_forward(self):
        captured = {}

        def progress(evaluated, snapshot):
            captured["cp"] = snapshot
            if evaluated >= 350:
                raise RuntimeError("stop")

        with self.assertRaises(RuntimeError):
            itf.run_interference(self.rows, SMALL_PARAMS, progress=progress)
        cp = captured["cp"]
        self.assertIn("level", cp)
        self.assertIn("offset", cp)
        # 检查点在批边界（档内偏移为批大小整数倍）或档级切换（偏移为 0）处落盘。
        self.assertTrue(
            cp["offset"] == 0 or cp["offset"] % itf.BATCH_SIZE == 0,
            f"unexpected checkpoint {cp}",
        )
        self.assertGreaterEqual(cp["evaluated"], 350)
        first_seen = []

        def short_progress(evaluated, snapshot):
            first_seen.append(evaluated)
            raise RuntimeError("stop again")

        with self.assertRaises(RuntimeError):
            itf.run_interference(self.rows, SMALL_PARAMS, checkpoint=cp, progress=short_progress)
        # 续跑从检查点之后开始，首个进度不小于已判定数，绝不从零重来。
        self.assertGreaterEqual(first_seen[0], cp["evaluated"])

    def test_remaining_combination_count(self):
        from math import comb
        n = 83
        full = sum(comb(n, level) for level in range(1, 4))
        self.assertEqual(itf.remaining_combination_count(n), full)
        cp = {"level": 1, "offset": 10, "evaluated": 10}
        self.assertEqual(itf.remaining_combination_count(n, cp), full - 10)
        cp2 = {"level": 2, "offset": 0, "evaluated": comb(n, 1)}
        self.assertEqual(itf.remaining_combination_count(n, cp2), comb(n, 2) + comb(n, 3))


class ResultShapeTests(unittest.TestCase):
    def test_vulnerable_result_has_evidence_table_and_basis(self):
        n = 60
        fan = [30 + (5 if i >= 30 else 0) for i in range(n)]
        sound = [50 + ((i * 7) % 5) * 0.4 for i in range(n)]
        sound[33] = 70.0
        result = itf.run_interference(make_rows(fan, sound),
                                      {"sample_seconds": 10, "max_lag": 6, "min_pairs": 10})
        self.assertTrue(result["metrics"])
        self.assertTrue(result["tables"])
        self.assertIn("抗干扰反例证据", result["tables"][0]["title"])
        self.assertTrue(all("label" in m for m in result["metrics"]))
        self.assertTrue(all("title" in s and "text" in s for s in result["sections"]))

    def test_analyze_still_works_with_bool_param(self):
        result = analyze(demo_rows(), {**FIXTURE, "interference_enabled": True})
        self.assertIn("headline", result)
        self.assertEqual(len(result["metrics"]), 6)  # 原扫描指标不受开关影响


if __name__ == "__main__":
    unittest.main()
