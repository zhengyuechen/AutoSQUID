"""analysis.backfill_ledger: reconstruct MISSING ledger values post-run — fill blanks only, never
overwrite a present value, never invent timestamp/n_resets/event, never write nan."""
import csv

import numpy as np

from AutoSQUID.config import Config
from AutoSQUID.analysis import backfill_ledger, save_pcs102, LEDGER_COLS


def _write_ledger(d, rows):
    lines = ["\t".join(LEDGER_COLS)]
    for r in rows:
        lines.append("\t".join(str(r.get(c, "")) for c in LEDGER_COLS))
    (d / "experiment_log.txt").write_text("\n".join(lines) + "\n")


def _read_ledger(d):
    with open(d / "experiment_log.txt") as f:
        return list(csv.DictReader(f, delimiter="\t"))


class TestSegmentFields:
    def test_fills_segment_fields_from_usable_points(self, tmp_path):
        "An old trials-era row with usable_points but blank segment fields, reinterpreted in segment mode."
        fn = "DAQ_Jun05_500us_400mK_8p4Mpts_1.txt"
        (tmp_path / fn).write_text("stub")                       # file present; usable_points already in ledger -> no data read
        _write_ledger(tmp_path, [dict(filename=fn, outcome="CLEAN", usable_points=8_400_000,
                                      usable_seconds="4200.000", scan_interval_us=500, mean_V="0.0", std_V="0.001")])
        changes = backfill_ledger(tmp_path, cfg=Config(n_points=10_000_000, segment_goal=(20, 1_000_000)), dry_run=True)
        f = changes[0]["filled"]
        assert f["goal_mode"] == "segment"
        assert f["segment_length"] == "1000000"
        assert f["accepted_segments"] == "8"                     # 8.4M // 1M
        assert f["goal_progress"] == "8"
        assert _read_ledger(tmp_path)[0]["accepted_segments"] == ""   # dry_run did NOT write

    def test_dry_run_false_writes(self, tmp_path):
        fn = "DAQ_Jun05_500us_400mK_8p4Mpts_1.txt"
        (tmp_path / fn).write_text("stub")
        _write_ledger(tmp_path, [dict(filename=fn, outcome="CLEAN", usable_points=8_400_000)])
        backfill_ledger(tmp_path, cfg=Config(n_points=10_000_000, segment_goal=(20, 1_000_000)), dry_run=False)
        row = _read_ledger(tmp_path)[0]
        assert row["accepted_segments"] == "8" and row["goal_mode"] == "segment"

    def test_never_overwrites_present_value(self, tmp_path):
        fn = "DAQ_Jun05_500us_400mK_8p4Mpts_1.txt"
        (tmp_path / fn).write_text("stub")
        _write_ledger(tmp_path, [dict(filename=fn, outcome="CLEAN", usable_points=8_400_000, accepted_segments=99)])
        changes = backfill_ledger(tmp_path, cfg=Config(n_points=10_000_000, segment_goal=(20, 1_000_000)), dry_run=True)
        assert "accepted_segments" not in changes[0]["filled"]    # present (even if wrong) -> untouched

    def test_cumulative_goal_progress(self, tmp_path):
        rows = []
        for i, up in [(1, 6_000_000), (2, 8_000_000)]:
            fn = f"DAQ_Jun05_500us_400mK_{i}Mpts_{i}.txt"
            (tmp_path / fn).write_text("stub")
            rows.append(dict(filename=fn, outcome="CLEAN", usable_points=up))
        _write_ledger(tmp_path, rows)
        changes = backfill_ledger(tmp_path, cfg=Config(n_points=10_000_000, segment_goal=(20, 1_000_000)), dry_run=True)
        assert [c["filled"]["goal_progress"] for c in changes] == ["6", "14"]   # 6, then 6+8


class TestFromData:
    def test_fills_mean_std_outcome_usable_from_daq_file(self, tmp_path):
        v = np.linspace(-1e-3, 1e-3, 5000)                       # a real saved trace; tiny ramp (not flat -> not 'dead')
        fn = "DAQ_Jun05_4us_400mK_5000pts_1.txt"
        save_pcs102(tmp_path / fn, v, 4e-6)
        _write_ledger(tmp_path, [dict(filename=fn)])             # everything blank except filename
        f = backfill_ledger(tmp_path, dry_run=True)[0]["filled"]
        assert f["usable_points"] == "5000"
        assert f["scan_interval_us"] == "4"
        assert "mean_V" in f and "std_V" in f
        assert f["outcome"] in ("CLEAN", "SURGE")                # inferred via is_surge_spec


class TestTemperature:
    def test_fills_temp_from_csv_only(self, tmp_path):
        fn = "DAQ_Jun05_500us_400mK_8p4Mpts_1.txt"
        (tmp_path / fn).write_text("stub")
        (tmp_path / "TEMP_Jun05_500us_400mK_8p4Mpts_1.csv").write_text("time_s,T_K\n0,0.4001\n30,0.4002\n")
        _write_ledger(tmp_path, [dict(filename=fn, usable_points=8_400_000)])
        f = backfill_ledger(tmp_path, dry_run=True)[0]["filled"]
        assert f["T_start_K"] == "0.400100"
        assert f["T_end_K"] == "0.400200"

    def test_no_temp_csv_leaves_blank_no_nan(self, tmp_path):
        fn = "DAQ_Jun05_500us_400mK_8p4Mpts_1.txt"
        (tmp_path / fn).write_text("stub")                       # no TEMP csv
        _write_ledger(tmp_path, [dict(filename=fn, usable_points=8_400_000)])
        f = backfill_ledger(tmp_path, dry_run=True)[0]["filled"]
        assert "T_start_K" not in f and "T_end_K" not in f       # left blank, never nan


class TestSafety:
    def test_never_invents_timestamp_resets_event(self, tmp_path):
        fn = "DAQ_Jun05_500us_400mK_8p4Mpts_1.txt"
        (tmp_path / fn).write_text("stub")
        _write_ledger(tmp_path, [dict(filename=fn, usable_points=8_400_000)])
        f = backfill_ledger(tmp_path, cfg=Config(n_points=10_000_000, segment_goal=(20, 1_000_000)), dry_run=True)[0]["filled"]
        assert not ({"timestamp", "n_resets", "event"} & set(f))
