"""One mocked run_cycle proving the segment-mode invariant: the DAQ acquires cfg.n_points
(the RUN length), while progress is counted as usable_points // segment_length (the windows).

Hardware/IO is monkeypatched at the measurement-module level: acquire, truncate, surge-check,
reset, and the two savers. No nidaqmx/pyserial calls are made.
"""
import csv

import numpy as np

from AutoSQUID.config import Config
from AutoSQUID import measurement as M


def _read_ledger(path):
    with open(path) as f:
        return list(csv.DictReader(f, delimiter="\t"))


def test_segment_mode_acquires_n_points_counts_windows(tmp_path, monkeypatch):
    # segment_goal=(2, 3): stop after 2 clean 3-pt windows. n_points=10 -> one clean run yields 10//3 = 3 windows.
    cfg = Config(scan_interval_s=[1.0], n_points=10, segment_goal=(2, 3), temp_label="0mK",
                 temp_logger=False, data_root=str(tmp_path), user="u", date="d", daq_ai="Dev1/ai0")

    acquired_n = []
    def fake_acquire(c, ai, n, fs):
        acquired_n.append(n)                       # record the requested DAQ length
        return np.zeros(n), n, None                # clean full-length run, no live flag
    monkeypatch.setattr(M, "acquire_finite_chunked", fake_acquire)
    monkeypatch.setattr(M, "truncate_to_clean", lambda v, fs, **kw: (v, len(v), len(v) / fs, None, "ok"))
    monkeypatch.setattr(M, "is_surge_spec", lambda v, **kw: (False, "ok"))
    monkeypatch.setattr(M, "reset_and_verify", lambda c, **kw: 1)        # reset always clears
    monkeypatch.setattr(M, "save_pcs102", lambda *a, **k: None)
    monkeypatch.setattr(M, "save_temp_log", lambda *a, **k: None)

    result = M.run_cycle(cfg, 1.0)

    assert result == "ok"
    assert acquired_n == [cfg.n_points]                              # acquired the RUN length (10), NOT segment_length (3)

    acq = [r for r in _read_ledger(cfg.outdir / "experiment_log.txt") if r["filename"]]
    assert len(acq) == 1
    r = acq[0]
    assert r["goal_mode"] == "segment"
    assert int(r["accepted_segments"]) == 3                          # 10 // 3
    assert int(r["goal_progress"]) == 3                             # cumulative segments
    assert r["n_clean"] == ""                                        # trace-count column blank in segment mode
    assert int(r["accepted"]) == 0                                  # `accepted` not overloaded
    assert int(r["segment_length"]) == 3
    assert r["T_start_K"] == "" and r["T_end_K"] == ""             # temp_logger off -> blank, never nan
