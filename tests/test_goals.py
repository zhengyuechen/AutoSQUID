"""goals.py policy: mode, validation, contribution, progress, done-ness — pure, no hardware.

Includes a fixture-ledger counting test (NOT the real Jun-05 folder) that reproduces the
55 usable 1M-segments measured across the eight 500us JUMP traces.
"""
import pytest

from AutoSQUID.config import Config
from AutoSQUID import goals
from AutoSQUID.analysis import LEDGER_COLS


class TestMode:
    def test_trials(self):
        cfg = Config(n_trials=2)
        assert goals.goal_mode(cfg) == "trials"
        assert goals.unit(cfg) == "traces"
        assert goals.target_count(cfg) == 2

    def test_segment(self):
        cfg = Config(segment_goal=(20, 1_000_000))
        assert goals.goal_mode(cfg) == "segment"
        assert goals.unit(cfg) == "segments"
        assert goals.target_count(cfg) == 20


class TestValidate:
    def test_both_set_raises(self):
        with pytest.raises(RuntimeError, match="comment one out"):
            goals.validate_goal(Config(n_trials=2, segment_goal=(20, 1_000_000)))

    def test_neither_set_raises(self):
        with pytest.raises(RuntimeError, match="no acquisition target"):
            goals.validate_goal(Config())

    def test_run_shorter_than_segment_raises(self):
        with pytest.raises(RuntimeError, match="can't hold one full segment"):
            goals.validate_goal(Config(n_points=500_000, segment_goal=(20, 1_000_000)))

    def test_valid_trials(self):
        goals.validate_goal(Config(n_trials=2))                                          # no raise

    def test_valid_segment(self):
        goals.validate_goal(Config(n_points=10_000_000, segment_goal=(20, 1_000_000)))   # no raise

    def test_zero_segments_raises(self):
        with pytest.raises(RuntimeError, match="positive integers"):
            goals.validate_goal(Config(n_points=10_000_000, segment_goal=(0, 1_000_000)))

    def test_zero_length_raises(self):
        with pytest.raises(RuntimeError, match="positive integers"):
            goals.validate_goal(Config(n_points=10_000_000, segment_goal=(20, 0)))

    def test_negative_raises(self):
        with pytest.raises(RuntimeError, match="positive integers"):
            goals.validate_goal(Config(n_points=10_000_000, segment_goal=(-5, 1_000_000)))

    def test_malformed_tuple_raises(self):
        with pytest.raises(RuntimeError, match="pair"):
            goals.validate_goal(Config(n_points=10_000_000, segment_goal=(1_000_000,)))   # not length-2

    def test_non_integer_length_raises(self):
        with pytest.raises(RuntimeError, match="positive integers"):
            goals.validate_goal(Config(n_points=10_000_000, segment_goal=(20, 1.5e6)))    # float length

    def test_nonpositive_n_trials_raises(self):
        with pytest.raises(RuntimeError, match="positive integer"):
            goals.validate_goal(Config(n_trials=0))


class TestContribution:
    def test_segment_clean_floor(self):
        cfg = Config(n_points=10_000_000, segment_goal=(20, 1_000_000))
        assert goals.contribution(cfg, "CLEAN", 8_400_000) == 8      # floor(8.4M / 1M)
        assert goals.contribution(cfg, "CLEAN", 7_934_000) == 7      # the 900mK_4 case (trims below 8M)

    def test_segment_non_clean_zero(self):
        cfg = Config(n_points=10_000_000, segment_goal=(20, 1_000_000))
        for oc in ("SURGE", "DEAD", "BAD_BASELINE"):
            assert goals.contribution(cfg, oc, 8_400_000) == 0

    def test_trials_accept_gate(self):
        cfg = Config(n_points=10_000_000, n_trials=2, min_usable_frac=0.9)
        assert goals.contribution(cfg, "CLEAN", 10_000_000) == 1
        assert goals.contribution(cfg, "CLEAN", 8_500_000) == 0      # below 0.9 * n_points
        assert goals.contribution(cfg, "SURGE", 10_000_000) == 0


class TestDoneAndStr:
    def test_is_done(self):
        cfg = Config(segment_goal=(20, 1_000_000))
        assert not goals.is_done(19, cfg)
        assert goals.is_done(20, cfg)
        assert goals.is_done(21, cfg)

    def test_progress_str(self):
        assert goals.progress_str(8, Config(segment_goal=(20, 1_000_000))) == "8/20 segments"
        assert goals.progress_str(1, Config(n_trials=2)) == "1/2 traces"


def _write_ledger(d, rows):
    "Write a minimal current-schema ledger (header = LEDGER_COLS) with the given rows."
    lines = ["\t".join(LEDGER_COLS)]
    for r in rows:
        lines.append("\t".join(str(r.get(c, "")) for c in LEDGER_COLS))
    (d / "experiment_log.txt").write_text("\n".join(lines) + "\n")


def _seg_row(fn, segs, seg_len=1_000_000):
    return dict(filename=fn, outcome="CLEAN", goal_mode="segment",
                segment_length=seg_len, accepted_segments=segs)


class TestSegmentScanProgress:
    def test_reproduces_55_from_fixture_ledger(self, tmp_path):
        "Fixture ledger with the Jun-05 500us segment yields -> scan_progress sums them per (interval,temp)."
        yields = {"400mK": [6, 8, 8, 8], "900mK": [2, 8, 8, 7]}
        rows = []
        for temp, segs in yields.items():
            for i, ns in enumerate(segs, start=1):
                fn = f"DAQ_Jun05_500us_{temp}_10Mpts_{i}_JUMP.txt"
                (tmp_path / fn).write_text("stub")                       # file must exist on disk
                rows.append(_seg_row(fn, ns))
        _write_ledger(tmp_path, rows)
        c400 = Config(scan_interval_s=[500e-6], segment_goal=(20, 1_000_000), temp_label="400mK")
        c900 = Config(scan_interval_s=[500e-6], segment_goal=(20, 1_000_000), temp_label="900mK")
        p400 = goals.scan_progress(tmp_path, c400.id_core(500e-6), c400)
        p900 = goals.scan_progress(tmp_path, c900.id_core(500e-6), c900)
        assert (p400, p900, p400 + p900) == (30, 25, 55)

    def test_filters_by_segment_length(self, tmp_path):
        "A 2M goal must not count 1M-segment rows. (Real no-`_JUMP` filename: a truncated-clean trace has no suffix.)"
        fn = "DAQ_Jun05_500us_400mK_8p4Mpts_1.txt"          # real saved style: usable-npts tag, no _JUMP suffix
        (tmp_path / fn).write_text("stub")
        _write_ledger(tmp_path, [_seg_row(fn, 8, seg_len=1_000_000)])
        c2m = Config(scan_interval_s=[500e-6], segment_goal=(20, 2_000_000), temp_label="400mK")
        assert goals.scan_progress(tmp_path, c2m.id_core(500e-6), c2m) == 0

    def test_skips_missing_file(self, tmp_path):
        "A ledgered row whose file was deleted does not count."
        _write_ledger(tmp_path, [_seg_row("DAQ_Jun05_500us_400mK_10Mpts_1_JUMP.txt", 8)])  # no file on disk
        c = Config(scan_interval_s=[500e-6], segment_goal=(20, 1_000_000), temp_label="400mK")
        assert goals.scan_progress(tmp_path, c.id_core(500e-6), c) == 0

    def test_excludes_trials_mode_rows(self, tmp_path):
        "A trials-mode row (even with a stray accepted_segments) must not count toward segment progress."
        fn = "DAQ_Jun05_500us_400mK_10Mpts_1.txt"
        (tmp_path / fn).write_text("stub")
        _write_ledger(tmp_path, [dict(filename=fn, outcome="CLEAN", goal_mode="trials",
                                      segment_length="", accepted_segments=8)])
        c = Config(scan_interval_s=[500e-6], segment_goal=(20, 1_000_000), temp_label="400mK")
        assert goals.scan_progress(tmp_path, c.id_core(500e-6), c) == 0


class TestSegmentTraceNames:
    def test_lists_contributing_traces_in_index_order(self, tmp_path):
        "segment_trace_names returns contributing (>=1 window) segment-mode files, index order, 0-window dropped."
        from AutoSQUID.analysis import segment_trace_names
        rows = []
        for i, ns in [(2, 8), (1, 6), (3, 0)]:                  # out of order; idx 3 contributes 0 -> excluded
            fn = f"DAQ_Jun05_500us_400mK_10Mpts_{i}_JUMP.txt"
            (tmp_path / fn).write_text("stub")
            rows.append(_seg_row(fn, ns))
        _write_ledger(tmp_path, rows)
        c = Config(scan_interval_s=[500e-6], segment_goal=(20, 1_000_000), temp_label="400mK")
        got = segment_trace_names(tmp_path, c.id_core(500e-6), c.segment_len)
        assert got == ["DAQ_Jun05_500us_400mK_10Mpts_1_JUMP.txt",
                       "DAQ_Jun05_500us_400mK_10Mpts_2_JUMP.txt"]
