"""声级碰撞抗干扰复核。

交班时担心几声碰撞（瞬时声级）把复测引到错误时段。对选定批次沿用原峰判法：
各档一起剔除同一组声级时刻（风机读数照留），在最多三个时刻的组合里找出
能换峰或让峰消失的最少一组；同大小按时间最早的一组。全无反例才报稳定。

大组合空间按批枚举并落检查点：中断后续跑剩余组合，重试不新增判定。
"""

from datetime import timedelta
from itertools import combinations
from math import comb

from .analysis import build_grid, scan_lags, select_peak

# 最多剔除三个声级时刻。
MAX_REMOVAL = 3
# 每批改多少个组合，批间才写检查点并续租，普通扫描照常完成。
BATCH_SIZE = 1000

STATE_LABEL = {"stable": "抗干扰稳定", "vulnerable": "存在干扰反例", "no_peak": "原扫描无峰"}
STATE_RULE = (
    "各档同时剔除同一组声级时刻、风机读数照留，沿用原峰判法（|r| 取大、12 位小数比较，"
    "再比配对数、绝对滞后、滞后值）重算；在 1 至 3 个时刻的组合中取最少剔除数，同数取时间最早的一组。"
)


def _slot_time(prepared, slot):
    return (prepared["start"] + timedelta(seconds=slot * prepared["interval"])).isoformat(sep=" ")


def _candidate_slots(prepared):
    # 只剔除有声级读数的时刻（缺测格本就不参与配对），按时间从早到晚。
    return sorted(prepared["sound"])


def _peak_after_removal(prepared, removed):
    """剔除一组声级时刻后，用原口径逐档重算并按原峰判法选峰（风机读数照留）。"""
    sound = {slot: value for slot, value in prepared["sound"].items() if slot not in removed}
    rows = scan_lags(prepared["fan"], sound, prepared["span"], prepared["interval"],
                     prepared["max_lag"], prepared["min_pairs"])
    return select_peak(rows), rows


def _peak_brief(row):
    if row is None:
        return None
    return {
        "lag_samples": row["lag_samples"],
        "lag_seconds": row["lag_seconds"],
        "pearson_r": round(row["pearson_r"], 6),
        "pair_count": row["pair_count"],
    }


def _peak_text(brief):
    if brief is None:
        return "无可判读峰"
    return (f"{brief['lag_samples']:+d} 格（{brief['lag_seconds']:+d} 秒），"
            f"r={brief['pearson_r']:.4f}，{brief['pair_count']} 对")


def _table(title, columns, rows):
    return {"title": title, "columns": [{"key": key, "label": label} for key, label in columns], "rows": rows}


def _presentation(result, candidate_count):
    """把判定结果转成页面与 CSV 共用的指标、证据表和说明。"""
    original = result["original_peak"]
    counterexample = result["counterexample"]
    metrics = [
        {"label": "抗干扰判定", "value": STATE_LABEL[result["state"]], "detail": result["headline"]},
        {"label": "原峰（未剔除）", "value": _peak_text(original),
         "detail": "沿用原峰判法，作为剔除前后的对照基准"},
        {"label": "已枚举组合数", "value": result["evaluated_combinations"],
         "detail": f"覆盖 1 至 {result['checked_levels']} 个时刻的组合"},
        {"label": "可剔除声级时刻", "value": candidate_count, "detail": "仅含有声级读数的时间格，按时间排序"},
    ]
    if counterexample is not None:
        new_peak = counterexample["new_peak"]
        metrics.append({"label": "最少剔除数", "value": counterexample["removal_count"],
                        "detail": "同剔除数下取时间最早的一组"})
        metrics.append({"label": "剔除后峰位", "value": _peak_text(new_peak),
                        "detail": f"判定类型：{counterexample['kind']}"})
        evidence_rows = [
            {"order": index + 1, "timestamp": stamp, "slot": slot,
             "change": counterexample["kind"], "new_peak": _peak_text(new_peak)}
            for index, (stamp, slot) in enumerate(
                zip(counterexample["removed_timestamps"], counterexample["removed_slots"]))
        ]
        tables = [_table("抗干扰反例证据（同一组声级时刻）", (
            ("order", "剔除次序"), ("timestamp", "剔除的声级采样时刻"), ("slot", "网格槽位"),
            ("change", "峰变化"), ("new_peak", "剔除后峰位"),
        ), evidence_rows)]
        sections = [
            {"title": "判定依据", "text": result["criterion"]},
            {"title": "交班提示", "text": (
                f"表中 {counterexample['removal_count']} 个声级时刻疑似碰撞，足以{counterexample['kind']}；"
                "复测窗口应避开这些时刻或先核对现场声源，风机读数全程保留未剔除。"
            )},
        ]
    else:
        tables = []
        if result["state"] == "stable":
            sections = [
                {"title": "判定依据", "text": result["criterion"]},
                {"title": "交班提示", "text": "查全无反例才报稳定：可按原峰时段安排复测，并记录枚举的组合数与口径。"},
            ]
        else:
            sections = [
                {"title": "判定依据", "text": result["criterion"]},
                {"title": "交班提示", "text": "原来无峰已标明，不做组合剔除；先补齐缺测或选取风机调频时段后再跑抗干扰检查。"},
            ]
    return {**result, "metrics": metrics, "tables": tables, "sections": sections, "state_label": STATE_LABEL[result["state"]]}


def run_interference(rows, params, checkpoint=None, progress=None):
    """对一个批次执行抗干扰复核。

    checkpoint 为上次中断时保存的检查点（dict），给出则从该位置续跑剩余组合；
    progress(evaluated_total, checkpoint) 每批回调一次，由 worker 续租并持久化进度。
    """
    prepared = build_grid(rows, params)
    original_rows = scan_lags(prepared["fan"], prepared["sound"], prepared["span"],
                              prepared["interval"], prepared["max_lag"], prepared["min_pairs"])
    original_peak = select_peak(original_rows)
    base = {
        "original_peak": _peak_brief(original_peak),
        "original_lag_rows": original_rows,
        "candidate_slot_count": len(_candidate_slots(prepared)),
    }

    # 原来无峰就标明，不进入组合枚举。
    if original_peak is None:
        return _presentation({
            **base,
            "state": "no_peak",
            "headline": "原扫描没有可判读的相关峰，抗干扰复核无峰可撼动。",
            "criterion": "全部档位配对不足或配对内序列恒定，先补齐缺测或选取风机调频时段后重跑。",
            "evaluated_combinations": 0,
            "checked_levels": 0,
            "remaining_combinations": 0,
            "checkpoint": None,
            "counterexample": None,
        }, base["candidate_slot_count"])

    candidates = _candidate_slots(prepared)
    original_lag = original_peak["lag_samples"]

    # 恢复检查点：只记录已完成的档级与该档内已枚举的组合数，重试不新增判定。
    checkpoint = dict(checkpoint or {})
    start_level = int(checkpoint.get("level", 1))
    start_offset = int(checkpoint.get("offset", 0))
    evaluated_before = int(checkpoint.get("evaluated", 0))
    evaluated_here = 0

    def snapshot(level, offset):
        return {"level": level, "offset": offset, "evaluated": evaluated_before + evaluated_here}

    for level in range(1, MAX_REMOVAL + 1):
        if level < start_level:
            # 低档此前已查尽且无反例，跳过且不重复计数。
            continue
        offset = 0
        batch = 0
        for combo in combinations(candidates, level):
            if level == start_level and offset < start_offset:
                offset += 1
                continue
            new_peak, _rows = _peak_after_removal(prepared, frozenset(combo))
            evaluated_here += 1
            offset += 1
            batch += 1
            if new_peak is None:
                kind = "峰消失"
            elif new_peak["lag_samples"] != original_lag:
                kind = "换峰"
            else:
                kind = None
            if kind is not None:
                return _presentation({
                    **base,
                    "state": "vulnerable",
                    "headline": f"剔除 {level} 个声级时刻后{kind}，原峰（{original_lag:+d} 格）可被碰撞干扰。",
                    "criterion": STATE_RULE,
                    "evaluated_combinations": evaluated_before + evaluated_here,
                    "checked_levels": level,
                    "remaining_combinations": 0,
                    "checkpoint": None,
                    "counterexample": {
                        "kind": kind,
                        "removal_count": level,
                        "removed_timestamps": [_slot_time(prepared, slot) for slot in combo],
                        "removed_slots": list(combo),
                        "new_peak": _peak_brief(new_peak),
                    },
                }, base["candidate_slot_count"])
            if batch >= BATCH_SIZE:
                if progress is not None:
                    progress(evaluated_before + evaluated_here, snapshot(level, offset))
                batch = 0
        # 该档枚举尽且无反例，进入下一档。
        start_level = level + 1
        start_offset = 0
        if progress is not None:
            progress(evaluated_before + evaluated_here, snapshot(level + 1, 0))

    # 1–3 个时刻的组合全部查尽且无反例，才报稳定。
    return _presentation({
        **base,
        "state": "stable",
        "headline": f"剔除任意 1 至 {MAX_REMOVAL} 个声级时刻均未换峰或令峰消失，原峰对碰撞干扰稳定。",
        "criterion": (
            f"枚举 {len(candidates)} 个有声级读数时刻的全部 1–{MAX_REMOVAL} 元组合，"
            "各档同时剔除同组声级时刻、风机读数照留，沿用原峰判法重算，未发现反例。"
        ),
        "evaluated_combinations": evaluated_before + evaluated_here,
        "checked_levels": MAX_REMOVAL,
        "remaining_combinations": 0,
        "checkpoint": None,
        "counterexample": None,
    }, base["candidate_slot_count"])


def remaining_combination_count(candidate_count, checkpoint=None):
    """供页面展示剩余工作量：检查点之后还有多少组合待枚举。"""
    start_level = int((checkpoint or {}).get("level", 1))
    start_offset = int((checkpoint or {}).get("offset", 0))
    total = 0
    for level in range(1, MAX_REMOVAL + 1):
        size = comb(candidate_count, level)
        if level == start_level:
            total += max(size - start_offset, 0)
        elif level > start_level:
            total += size
    return total
