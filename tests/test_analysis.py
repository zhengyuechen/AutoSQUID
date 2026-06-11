"""Pure detection + I/O: surge gate, live chunk check, clean-prefix locator, filename tags,
temperature labels, PCS102 round trip, ledger/action log, and resume bookkeeping.

Synthetic traces use the bench numbers: noise std ~2 mV, latch steps 0.2-0.8 V, win=2000 chunks.
"""
import numpy as np
import pytest

from AutoSQUID.analysis import (is_surge_spec, chunk_jump, usable_points_from_spec, truncate_to_clean,
                                fmt_npts, parse_npts, format_temp_label,
                                save_pcs102, save_temp_csv, read_daq_file,
                                log_experiment, log_action, LEDGER_COLS,
                                accepted_trace_names, scan_indices)

WIN = 2000      # _chunk_stats default chunk size
NOISE = 2e-3    # bench noise std ~2 mV


def trace(n_chunks=100, noise=NOISE, seed=0):
    "Clean white-noise trace of n_chunks x WIN points around 0 V."
    rng = np.random.default_rng(seed)
    return rng.normal(0.0, noise, n_chunks * WIN)


# ---------------------------------------------------------------- is_surge_spec (whole-trace gate)

class TestIsSurgeSpec:
    def test_clean_white_noise_passes(self):
        bad, reason = is_surge_spec(trace())
        assert not bad and reason == "ok"

    def test_empty_trace_fails(self):
        bad, _ = is_surge_spec(np.array([]))
        assert bad

    def test_rail_fails(self):
        v = trace(); v[50 * WIN + 7] = 9.8
        bad, reason = is_surge_spec(v)
        assert bad and "rail" in reason

    def test_first_chunk_offset_fails(self):
        # already-surged pre-check: whole trace sitting at +0.2 V
        bad, reason = is_surge_spec(trace() + 0.2)
        assert bad and "first-chunk" in reason

    def test_latched_step_fails(self):
        # the documented bench failure (DAQ_100us_2K_10Mpts_3): flat near 0, then a ~0.2 V step that persists
        v = trace(); v[60 * WIN:] += 0.2
        bad, reason = is_surge_spec(v)
        assert bad and "baseline deviation" in reason

    def test_flat_dead_fails(self):
        bad, reason = is_surge_spec(np.zeros(20 * WIN))
        assert bad and "flat/dead" in reason

    def test_frozen_segment_fails(self):
        v = trace(); v[70 * WIN:] = v[70 * WIN]   # output freezes at a constant value for 30% of the run
        bad, reason = is_surge_spec(v)
        assert bad and "stuck/frozen" in reason


# ---------------------------------------------------------------- chunk_jump (live per-chunk check)

class TestChunkJump:
    def test_baseline_window_rail(self):
        kind, _ = chunk_jump(0.0, 9.8, None)
        assert kind == "bad_baseline"

    def test_baseline_window_offset(self):
        kind, _ = chunk_jump(0.25, 0.3, None)     # |mean| > baseline_v before mu0 exists -> reset did not hold
        assert kind == "bad_baseline"

    def test_baseline_window_ok(self):
        assert chunk_jump(0.01, 0.05, None) is None

    def test_rail_after_baseline(self):
        kind, _ = chunk_jump(0.0, 9.8, 0.0)
        assert kind == "rail"

    def test_jump_after_baseline(self):
        kind, _ = chunk_jump(0.7, 0.9, 0.0, jump_v=0.5)
        assert kind == "jump"

    def test_within_band_ok(self):
        assert chunk_jump(0.3, 0.4, 0.0, jump_v=0.5) is None


# ---------------------------------------------------------------- usable_points_from_spec (locator)

class TestUsablePointsLocator:
    def test_full_clean(self):
        v = trace()
        assert usable_points_from_spec(v) == (len(v), None, "ok")

    def test_latched_jump_cut_at_onset(self):
        v = trace(); v[60 * WIN:] += 0.8
        n, kind, _ = usable_points_from_spec(v)
        assert kind == "jump" and n == 60 * WIN

    def test_latched_rail(self):
        v = trace(); v[70 * WIN:] = 9.8
        n, kind, _ = usable_points_from_spec(v)
        assert kind == "rail" and n == 70 * WIN

    def test_frozen_tail(self):
        v = trace(); v[70 * WIN:] = 0.0
        n, kind, _ = usable_points_from_spec(v)
        assert kind == "frozen" and n == 70 * WIN

    def test_transient_that_recovers_is_kept(self):
        # 3 bad chunks, then 47 clean to the end (> gap_tol) -> failure recovered -> full trace kept
        v = trace(); v[50 * WIN:53 * WIN] += 0.8
        n, kind, _ = usable_points_from_spec(v)
        assert kind is None and n == len(v)

    def test_gap_tol_tolerates_isolated_clean_chunk(self):
        # latched from chunk 80 with ONE clean chunk inside the failing run (<= gap_tol) -> still cut at 80
        v = trace(); v[80 * WIN:] += 0.8; v[90 * WIN:91 * WIN] -= 0.8
        n, kind, _ = usable_points_from_spec(v)
        assert kind == "jump" and n == 80 * WIN

    def test_dead_trace(self):
        n, kind, _ = usable_points_from_spec(np.zeros(20 * WIN))
        assert (n, kind) == (0, "dead")

    def test_empty(self):
        n, kind, _ = usable_points_from_spec(np.array([]))
        assert (n, kind) == (0, "dead")


def test_truncate_to_clean_returns_prefix_and_seconds():
    fs = 10_000.0
    v = trace(); v[60 * WIN:] += 0.8
    clean, n, secs, kind, _ = truncate_to_clean(v, fs)
    assert n == 60 * WIN and len(clean) == n and kind == "jump"
    assert secs == pytest.approx(n / fs)


# ---------------------------------------------------------------- filename tags + temperature labels

class TestNptsTags:
    @pytest.mark.parametrize("n,tag", [
        (10_000_000, "10Mpts"), (1_000_000, "1Mpts"),
        (8_454_000, "8p4Mpts"),                          # floored to 0.1 M
        (100_000, "0p1Mpts"), (99_999, "99999pts"), (20_000, "20000pts"),
    ])
    def test_fmt(self, n, tag):
        assert fmt_npts(n) == tag

    @pytest.mark.parametrize("tag,n", [
        ("10Mpts", 10_000_000), ("8p4Mpts", 8_400_000), ("0p1Mpts", 100_000), ("20000pts", 20_000),
    ])
    def test_parse(self, tag, n):
        assert parse_npts(tag) == n

    def test_parse_unrecognized(self):
        assert parse_npts("garbage") is None

    @pytest.mark.parametrize("n", [10_000_000, 8_400_000, 100_000, 20_000])
    def test_round_trip_on_floored_values(self, n):
        assert parse_npts(fmt_npts(n)) == n


@pytest.mark.parametrize("T,label", [
    (0.010, "10mK"), (0.0142, "14mK"), (0.0146, "15mK"),   # <100 mK: 1 mK steps
    (0.123, "120mK"), (0.35, "350mK"),                     # 100 mK-1 K: 10 mK steps
    (1.0, "1K"), (1.5, "1p5K"), (2.3, "2p3K"),             # >=1 K: 0.1 K, 'p' decimal
])
def test_format_temp_label(T, label):
    assert format_temp_label(T) == label


# ---------------------------------------------------------------- PCS102 + temp CSV round trip

class TestPcs102IO:
    def test_save_read_round_trip(self, tmp_path):
        rng = np.random.default_rng(1)
        v = rng.normal(0.0, NOISE, 5000)
        fname = "DAQ_Jun08_100us_14mK_5000pts_1.txt"
        save_pcs102(tmp_path / fname, v, 100e-6)
        header, df = read_daq_file(str(tmp_path), fname)
        assert header["DATAPOINTS"] == 5000
        assert header["CHANNELS"] == 1
        assert header["SCANINTVAL"] == pytest.approx(100e-6)
        assert df.index[0] == 1 and df.index[-1] == 5000               # 1-based POINT index
        assert np.allclose(df["CHAN_01(V)"].to_numpy(), v, atol=1e-6)  # %.6f write precision


def test_save_temp_csv(tmp_path):
    p = tmp_path / "TEMP_x.csv"
    save_temp_csv(p, [(0.0, 0.0141), (30.0, 0.0142)])
    lines = p.read_text().strip().splitlines()
    assert lines[0] == "time_s,T_K" and len(lines) == 3


# ---------------------------------------------------------------- ledger + action log

class TestLedger:
    def test_header_written_once_then_appends(self, tmp_path):
        p = tmp_path / "experiment_log.txt"
        log_experiment(p, {"timestamp": "t0", "outcome": "CLEAN", "accepted": 1})
        log_experiment(p, {"timestamp": "t1", "outcome": "SURGE", "accepted": 0})
        lines = p.read_text().splitlines()
        assert lines[0] == "\t".join(LEDGER_COLS) and len(lines) == 3
        assert lines[1].split("\t")[LEDGER_COLS.index("outcome")] == "CLEAN"
        assert lines[2].split("\t")[LEDGER_COLS.index("accepted")] == "0"

    def test_action_log(self, tmp_path):
        p = tmp_path / "action_log.txt"
        log_action(p, "RESET", "2 tries")
        log_action(p, "MEASURE", "DAQ_x_1")
        lines = p.read_text().splitlines()
        assert lines[0] == "timestamp\taction\tdetail" and len(lines) == 3
        assert lines[1].endswith("RESET\t2 tries")


# ---------------------------------------------------------------- resume bookkeeping

ID = "100us_14mK"


def _ledger_row(outdir, filename, accepted):
    log_experiment(outdir / "experiment_log.txt",
                   {"timestamp": "t", "filename": filename, "accepted": accepted, "outcome": "CLEAN"})


class TestResumeBookkeeping:
    def _make_outdir(self, tmp_path):
        f1 = "DAQ_Jun07_100us_14mK_10Mpts_1.txt"        # accepted, on disk
        f2 = "DAQ_Jun08_100us_14mK_8p4Mpts_2.txt"       # accepted, truncated tag, other date
        f3 = "DAQ_Jun08_100us_14mK_10Mpts_3_SURGE.txt"  # on disk, not accepted
        f4 = "DAQ_Jun07_100us_50mK_10Mpts_7.txt"        # different temp -> different id_core
        for f in (f1, f2, f3, f4):
            (tmp_path / f).touch()
        _ledger_row(tmp_path, f1, 1)
        _ledger_row(tmp_path, f2, 1)
        _ledger_row(tmp_path, f3, 0)
        _ledger_row(tmp_path, "DAQ_Jun05_100us_14mK_10Mpts_9.txt", 1)   # accepted but file deleted
        return f1, f2

    def test_accepted_trace_names(self, tmp_path):
        f1, f2 = self._make_outdir(tmp_path)
        # ledger-accepted AND still on disk, in index order; SURGE (accepted=0) and the deleted idx-9 excluded
        assert accepted_trace_names(tmp_path, ID) == [f1, f2]

    def test_scan_indices(self, tmp_path):
        self._make_outdir(tmp_path)
        n_clean, nxt = scan_indices(tmp_path, ID)
        assert n_clean == 2     # accepted rows whose file exists (dates and npts tags differ -> still counted)
        assert nxt == 4         # filesystem max index for this id_core (the _SURGE file's 3) + 1

    def test_empty_dir(self, tmp_path):
        assert scan_indices(tmp_path, ID) == (0, 1)


# ---------------------------------------------------------------- pre-0.2 ledger upgrade (migration)

OLD_LEDGER_COLS = ["timestamp", "scan_interval_us", "n_target", "n_acquired", "n_clean", "attempt",
                   "outcome", "jump_index", "jump_time_s", "n_resets", "mean_V", "std_V",
                   "T_start_K", "T_end_K", "filename"]      # the 0.1.4 (15-column) schema


def _write_old_ledger(path, rows):
    "Write a pre-0.2 (15-column) ledger: header + one tab-joined row per dict."
    with open(path, "w") as f:
        f.write("\t".join(OLD_LEDGER_COLS) + "\n")
        for r in rows:
            f.write("\t".join(str(r.get(c, "")) for c in OLD_LEDGER_COLS) + "\n")


class TestLedgerMigration:
    def _make_old_folder(self, tmp_path):
        clean = "DAQ_Jun07_100us_14mK_10Mpts_1.txt"          # pre-0.2 clean trace (no suffix)
        surge = "DAQ_Jun07_100us_14mK_10Mpts_2_SURGE.txt"
        (tmp_path / clean).touch()
        (tmp_path / surge).touch()
        _write_old_ledger(tmp_path / "experiment_log.txt", [
            {"timestamp": "t0", "outcome": "CLEAN", "filename": clean, "n_acquired": 10_000_000},
            {"timestamp": "t1", "outcome": "SURGE", "filename": surge, "n_acquired": 5_000_000},
        ])
        return clean, surge

    def test_old_clean_trace_counts_on_resume(self, tmp_path):
        # THE upgrade-path bug: a pre-0.2 clean trace must NOT count as zero after migrating to 0.2
        clean, _ = self._make_old_folder(tmp_path)
        n_clean, nxt = scan_indices(tmp_path, ID)            # migrates (read path) THEN counts
        assert n_clean == 1                                  # CLEAN row -> accepted=1; SURGE row excluded
        assert nxt == 3                                      # filesystem max index (the _SURGE file's 2) + 1
        assert accepted_trace_names(tmp_path, ID) == [clean]

    def test_migration_backs_up_and_rewrites_header(self, tmp_path):
        self._make_old_folder(tmp_path)
        p = tmp_path / "experiment_log.txt"
        accepted_trace_names(tmp_path, ID)                   # Path input -> exercises the f"{path}.bak" fix
        assert (tmp_path / "experiment_log.txt.bak").exists()
        lines = p.read_text().splitlines()
        assert lines[0] == "\t".join(LEDGER_COLS)            # header now the 16-column schema
        assert all(len(ln.split("\t")) == len(LEDGER_COLS) for ln in lines[1:])   # no ragged rows

    def test_append_after_migration_is_consistent(self, tmp_path):
        self._make_old_folder(tmp_path)
        p = tmp_path / "experiment_log.txt"
        log_experiment(p, {"timestamp": "t2", "outcome": "CLEAN", "accepted": 1,
                           "filename": "DAQ_Jun11_100us_14mK_10Mpts_3.txt"})      # a fresh 0.2 row
        widths = {len(ln.split("\t")) for ln in p.read_text().splitlines()}
        assert widths == {len(LEDGER_COLS)}                  # every row 16 cols -> no pandas ParserError

    def test_idempotent(self, tmp_path):
        self._make_old_folder(tmp_path)
        p = tmp_path / "experiment_log.txt"
        accepted_trace_names(tmp_path, ID)                   # 1st: migrates
        after_first = p.read_text()
        accepted_trace_names(tmp_path, ID)                   # 2nd: header already current -> no-op
        assert p.read_text() == after_first
