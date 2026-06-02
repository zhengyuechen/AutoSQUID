"""The measurement-cycle state machine: run_cycle (one scan interval). The interval loop lives in the
notebook (§2); resolve_temp_label sets the auto temperature label before the loop runs.

Collects cfg.n_trials CLEAN traces per interval within cfg.max_attempts real acquisitions; each acquisition
is order-indexed (clean -> {base}_{i}.txt, failed -> {base}_{i}_{JUMP|SURGE|RAIL|BADBASE}.txt); resumes from
existing files (never overwrites); a bad-baseline / non-clearing reset is systemic and stops the sweep.
"""
import time
import datetime
import serial

from .analysis import (is_surge_spec, save_pcs102, save_temp_csv, log_experiment, scan_indices,
                        format_temp_label)
from .serial_io import fire_reset
from .daq import acquire_finite_chunked, daq_read
from .temperature import TempLogger, read_temp


def reset_and_verify(cfg, tries=5, settle_s=0.3):
    "Fire reset + verify the locked baseline up to `tries` times; True once |mean| < cfg.baseline_v."
    with serial.Serial(cfg.port, cfg.baud, bytesize=8, parity="N", stopbits=1, timeout=1) as ser:
        for k in range(1, tries + 1):
            fire_reset(ser, cfg)
            time.sleep(settle_s)
            m = daq_read(cfg, cfg.daq_ai, cfg.verify_n, cfg.verify_fs).mean()
            cleared = abs(m) < cfg.baseline_v
            print(f"  reset try {k}: mean={m:+.4f} V -> {'CLEARED' if cleared else 'still off-zero'}")
            if cleared:
                return True
    return False

def resolve_temp_label(cfg):
    "If cfg.temp_label == 'auto', read + validate the MXC temperature and set the rounded label on cfg."
    if cfg.temp_label == "auto":
        T = read_temp(cfg)
        if T is None or not (0 < T < 1000):
            raise RuntimeError(f"bad MXC temperature read: {T} (logger down / wrong channel?)")
        cfg.temp_label = format_temp_label(T)
    return cfg.temp_label

def run_cycle(cfg, scan_interval_s):
    """One scan interval: collect cfg.n_trials CLEAN traces within cfg.max_attempts real acquisitions.
    Returns 'reset_fail' (a non-clearing/bad-baseline reset -> stop the sweep) or 'ok'."""
    fs = 1.0 / scan_interval_s
    tau_us = int(round(scan_interval_s * 1e6))
    base = cfg.base_name(scan_interval_s)
    ledger = cfg.outdir / "experiment_log.txt"
    est = cfg.n_points * scan_interval_s
    n_resets = 0

    def _log(outcome, **kw):
        "Append a ledger row, filling the per-interval constants; caller passes the varying fields."
        log_experiment(ledger, dict(timestamp=datetime.datetime.now().isoformat(timespec="seconds"),
                       scan_interval_us=tau_us, n_target=cfg.n_points, n_resets=n_resets, outcome=outcome, **kw))

    def _reset(tag, n_clean):
        "reset_and_verify (counts toward n_resets); log RESET_FAIL on failure. Returns ok."
        nonlocal n_resets
        ok = reset_and_verify(cfg); n_resets += 1
        if not ok:
            _log("RESET_FAIL", n_clean=n_clean, attempt=tag, filename="")
        return ok

    n_clean, i = scan_indices(cfg.outdir, base)
    if n_clean >= cfg.n_trials:
        print(f"\n=== {base} · already has {n_clean}/{cfg.n_trials} clean; skipping ==="); return "ok"
    if n_clean or i > 1:
        print(f"\n=== {base} · resuming: {n_clean}/{cfg.n_trials} clean on disk; next index {i} ===")
    if not _reset(0, n_clean):
        print("  reset did not clear (systemic); STOPPING."); return "reset_fail"

    n_attempts = 0
    while n_clean < cfg.n_trials and n_attempts < cfg.max_attempts:
        print(f"\n=== {base} · index {i} · {n_attempts}/{cfg.max_attempts} used · {n_clean}/{cfg.n_trials} clean · "
              f"{cfg.n_points} pts @ {fs:.0f} Hz (~{est:.0f} s) ===")
        _t0 = datetime.datetime.now()
        print(f"    start {_t0:%H:%M:%S} · expected done ~{_t0 + datetime.timedelta(seconds=est):%H:%M:%S}  (~{est:.0f} s)")
        tl = TempLogger(cfg); tl.start(); t0 = time.time()
        try:
            v, got, flag = acquire_finite_chunked(cfg, cfg.daq_ai, cfg.n_points, fs)
        finally:
            tl.stop(); tl.join(timeout=cfg.temp_every_s + 5)

        kind = flag["kind"] if flag else None        # classify -> (outcome, file tag, reason); tag is ONE token
        if   kind == "bad_baseline": outcome, tag, reason = "BAD_BASELINE", "BADBASE", flag["reason"]
        elif kind == "rail":         outcome, tag, reason = "RAIL", "RAIL", flag["reason"]
        elif kind == "jump":         outcome, tag, reason = "JUMP", "JUMP", flag["reason"]
        else:
            surged, sreason = is_surge_spec(v, rail_v=cfg.rail_v, first_chunk_v=cfg.baseline_v)
            outcome, tag, reason = ("SURGE", "SURGE", sreason) if surged else ("CLEAN", "", "ok")

        if outcome != "BAD_BASELINE": n_attempts += 1
        if outcome == "CLEAN":        n_clean += 1
        stem = f"{base}_{i}" + (f"_{tag}" if tag else "")
        save_pcs102(cfg.outdir / f"{stem}.txt", v, scan_interval_s)
        save_temp_csv(cfg.outdir / f"{stem}_temp.csv", tl.samples)
        Ts = [s[1] for s in tl.samples] or [float("nan")]
        _log(outcome, n_acquired=got, n_clean=n_clean, attempt=n_attempts, filename=f"{stem}.txt",
             jump_index=flag["index"] if flag else -1, jump_time_s=f"{flag['time_s']:.3f}" if flag else "",
             mean_V=f"{v.mean():.6f}", std_V=f"{v.std():.6f}", T_start_K=f"{Ts[0]:.6f}", T_end_K=f"{Ts[-1]:.6f}")
        print(f"  {outcome} ({reason}) · idx {i} · N={got} · {time.time()-t0:.0f}s · "
              f"mean={v.mean():+.4f} std={v.std():.4f} V · T {Ts[0]:.4f}->{Ts[-1]:.4f} K · {n_clean}/{cfg.n_trials} clean")
        i += 1

        if outcome == "BAD_BASELINE":
            print("  bad baseline -> reset is not holding (systemic); STOPPING."); return "reset_fail"
        if outcome == "CLEAN":
            continue
        if n_clean < cfg.n_trials and n_attempts < cfg.max_attempts and not _reset(n_attempts, n_clean):
            print("  reset did not clear (systemic); STOPPING."); return "reset_fail"

    if n_clean >= cfg.n_trials:
        print(f"  DONE: {n_clean}/{cfg.n_trials} clean traces in {n_attempts} acquisition(s) this run.")
    else:
        _log("BUDGET_EXHAUSTED", n_clean=n_clean, attempt=n_attempts, jump_index=-1, jump_time_s="", filename="")
        print(f"  BUDGET EXHAUSTED: {n_clean}/{cfg.n_trials} clean after {cfg.max_attempts} acquisitions; moving on.")
    return "ok"


