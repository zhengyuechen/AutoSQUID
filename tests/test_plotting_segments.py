"""plotting.plot_segment_psd_folder: ledger-free, folder-based per-segment PSDs.

Headless (Agg). Tiny synthetic PCS102 files via save_pcs102; clean_only=False so the test exercises the
discovery / exclusion / block-splitting / meta logic (not the is_surge_spec gate)."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pytest

from AutoSQUID.analysis import save_pcs102
from AutoSQUID.plotting import plot_segment_psd_folder

SEG = 1000


def _mk(d, name, n):
    save_pcs102(d / name, np.linspace(-1e-3, 1e-3, n), 500e-6)


def _mk_arr(d, name, v):
    save_pcs102(d / name, np.asarray(v, dtype=float), 500e-6)


def _run(d, locate_usable=False, **kw):   # as-saved by default so the discovery tests are deterministic
    fig, ax = plt.subplots()
    out = plot_segment_psd_folder(str(d), segment_length=SEG, conversion=1.0, clean_only=False,
                                  locate_usable=locate_usable, ax=ax, **kw)
    plt.close(fig)
    return out


def test_discovers_splits_and_meta(tmp_path):
    _mk(tmp_path, "DAQ_Jun05_500us_400mK_2500pts_1.txt", 2500)         # 2 full 1000-pt segments
    _mk(tmp_path, "DAQ_Jun05_500us_400mK_3000pts_2.txt", 3000)         # 3 segments
    _mk(tmp_path, "DAQ_Jun05_500us_900mK_2000pts_1.txt", 2000)         # 2 segments (other temp)
    _mk(tmp_path, "DAQ_Jun05_500us_400mK_500pts_3.txt", 500)           # too short -> excluded
    _mk(tmp_path, "DAQ_Jun05_500us_400mK_2000pts_4_SURGE.txt", 2000)   # SURGE -> excluded
    _mk(tmp_path, "DAQ_Jun05_500us_400mK_2000pts_5_BADBASE.txt", 2000) # BADBASE -> excluded
    _mk(tmp_path, "DAQ_Jun05_100us_400mK_2000pts_1.txt", 2000)         # other interval -> excluded

    f, stack, meta = _run(tmp_path, scan_interval_us=500)
    assert stack.shape == (7, len(f))                                  # 2 + 3 + 2 = 7 segments
    assert list(meta.columns) == ["filename", "segment_index", "start_point", "end_point",
                                  "scan_interval_us", "temp_label", "n_points_file",
                                  "usable_points", "usable_seconds", "locator_kind", "locator_reason",
                                  "segment_ok", "segment_reason"]
    assert set(meta["filename"]) == {"DAQ_Jun05_500us_400mK_2500pts_1.txt",
                                     "DAQ_Jun05_500us_400mK_3000pts_2.txt",
                                     "DAQ_Jun05_500us_900mK_2000pts_1.txt"}
    assert set(meta["scan_interval_us"]) == {500}
    r = meta[meta["filename"] == "DAQ_Jun05_500us_400mK_2500pts_1.txt"]
    assert list(r["segment_index"]) == [0, 1]
    assert list(r["start_point"]) == [0, 1000]
    assert list(r["end_point"]) == [1000, 2000]
    assert set(r["n_points_file"]) == {2500}
    assert set(r["temp_label"]) == {"400mK"}


def test_temp_label_filter(tmp_path):
    _mk(tmp_path, "DAQ_Jun05_500us_400mK_2000pts_1.txt", 2000)
    _mk(tmp_path, "DAQ_Jun05_500us_900mK_2000pts_2.txt", 2000)
    f, stack, meta = _run(tmp_path, scan_interval_us=500, temp_label="900mK")
    assert set(meta["filename"]) == {"DAQ_Jun05_500us_900mK_2000pts_2.txt"}
    assert set(meta["temp_label"]) == {"900mK"}
    assert stack.shape[0] == 2


def test_no_usable_segments_returns_empty(tmp_path):
    _mk(tmp_path, "DAQ_Jun05_500us_400mK_500pts_1.txt", 500)           # shorter than one window
    f, stack, meta = _run(tmp_path, scan_interval_us=500)
    assert stack.size == 0 and len(meta) == 0


def test_locate_usable_truncates_before_jump(tmp_path):
    # a clean 4000-pt prefix (4 full windows) then a +1 V latched jump (2000 pts)
    v = np.concatenate([np.linspace(-1e-3, 1e-3, 4000), np.full(2000, 1.0)])
    _mk_arr(tmp_path, "DAQ_Jun05_500us_400mK_6000pts_1.txt", v)
    _, st_off, m_off = _run(tmp_path, scan_interval_us=500, locate_usable=False)   # as-saved -> 6 windows
    _, st_on,  m_on  = _run(tmp_path, scan_interval_us=500, locate_usable=True)    # cut before the jump
    assert st_off.shape[0] == 6
    assert (m_off["locator_kind"] == "").all() and (m_off["locator_reason"] == "as-saved").all()
    assert st_on.shape[0] < 6                                          # jump tail dropped before segmenting
    assert m_on["usable_points"].iloc[0] < 6000
    assert m_on["locator_kind"].iloc[0] != ""                         # a jump/rail/freeze was located


def test_segment_length_must_be_positive(tmp_path):
    with pytest.raises(ValueError, match="must be positive"):
        plot_segment_psd_folder(str(tmp_path), 500, 0)


def test_segment_clean_only_drops_failing_window(tmp_path):
    SEG2 = 2000                                                       # >= is_surge_spec win, so a window is gateable
    v = np.concatenate([np.linspace(-1e-3, 1e-3, SEG2)] * 3)         # 3 clean windows
    v[SEG2 + 500] = 10.0                                             # a railed sample inside window 1
    _mk_arr(tmp_path, "DAQ_Jun05_500us_400mK_6000pts_1.txt", v)

    def run(seg_clean):
        fig, ax = plt.subplots()
        out = plot_segment_psd_folder(str(tmp_path), 500, SEG2, conversion=1.0,
                                      locate_usable=False, segment_clean_only=seg_clean, ax=ax)
        plt.close(fig)
        return out

    _, st_off, m_off = run(False)                                    # keeps all 3 full windows
    _, st_on,  m_on  = run(True)                                     # drops the railed window
    assert st_off.shape[0] == 3
    assert m_off["segment_ok"].all() and (m_off["segment_reason"] == "ok").all()
    assert "segment_ok" in m_on.columns and "segment_reason" in m_on.columns
    assert st_on.shape[0] == 2                                       # the failing window is gone
    assert list(m_on["segment_index"]) == [0, 2]                     # dropped window absent from meta
