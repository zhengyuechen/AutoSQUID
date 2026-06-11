"""The measurement-cycle state machine: run_cycle (one scan interval). The interval loop lives in the
notebook (§2); resolve_temp_label sets the auto temperature label before the loop runs.

Collects cfg.n_trials CLEAN traces per interval within cfg.max_attempts real acquisitions; each acquisition
is order-indexed (clean or truncated-clean -> {base}_{i}.txt, surge -> {base}_{i}_SURGE.txt, bad-baseline
-> {base}_{i}_BADBASE.txt); resumes from
existing files (never overwrites); a bad-baseline / non-clearing reset is systemic and stops the sweep.
"""
import time
import datetime
import serial

from .analysis import (is_surge_spec, save_pcs102, log_experiment, log_action,
                        scan_indices, format_temp_label, truncate_to_clean)
from .config import require_fields
from .serial_io import fire_reset
from .daq import acquire_finite_chunked, daq_read
from .temperature import read_temp, temp_logging, log_temp_now, save_temp_log


def reset_and_verify(cfg, tries=5, settle_s=0.3):
    "Fire reset + verify the locked baseline up to `tries` times; return the reset-try count that cleared the baseline (|mean| < cfg.baseline_v), or 0 if it never cleared within `tries`."
    with serial.Serial(cfg.port, cfg.baud, bytesize=8, parity="N", stopbits=1, timeout=1) as ser:
        for k in range(1, tries + 1):
            fire_reset(ser, cfg)
            time.sleep(settle_s)
            m = daq_read(cfg, cfg.daq_ai, cfg.verify_n, cfg.verify_fs).mean()
            cleared = abs(m) < cfg.baseline_v
            print(f"    Reset try {k}: mean={m:+.4f} V -> {'CLEARED' if cleared else 'still off-zero'}")
            if cleared:
                return k
    return 0

def resolve_temp_label(cfg):
    "If cfg.temp_label == 'auto', read + validate the MXC temperature and set the rounded label on cfg."
    if cfg.temp_label == "auto":
        T = read_temp(cfg)
        if T is None or not (0 < T < 1000):
            raise RuntimeError(f"bad MXC temperature read: {T} (logger down / wrong channel?)")
        cfg.temp_label = format_temp_label(T)
    return cfg.temp_label


# Acquisition/locator trigger kinds -> ledger `event` (provenance). SURGE is NOT here — it is reserved
# for the integrity `outcome` (is_surge_spec). 'frozen'/'dead' come only from the post-hoc locator.
_EVENT = {"jump": "JUMP", "rail": "RAIL", "frozen": "FROZEN", "dead": "DEAD",
          "bad_baseline": "BAD_BASELINE", None: "NONE"}

def _classify(cfg, v, flag):
    """Separate provenance from integrity. Returns (event, outcome, tag, reason):
      event   = the acquisition trigger (JUMP/RAIL/BAD_BASELINE from the live flag, else NONE) — provenance,
                kept even when the saved prefix is clean, so a slip is never silently lost.
      outcome = the integrity of the (already-truncated) prefix: BAD_BASELINE (systemic stop), DEAD
                (0-point prefix, nothing to save), else the POST-RUN surge check -> SURGE or CLEAN.
      tag     = filename suffix ('' / 'SURGE' / 'BADBASE')."""
    event = _EVENT.get(flag["kind"] if flag else None, "NONE")
    if event == "BAD_BASELINE":
        return event, "BAD_BASELINE", "BADBASE", flag["reason"]
    if len(v) == 0:
        return event, "DEAD", "", "no usable data (0-point prefix)"
    surged, sreason = is_surge_spec(v, rail_v=cfg.rail_v, first_chunk_v=cfg.baseline_v)
    return (event, "SURGE", "SURGE", sreason) if surged else (event, "CLEAN", "", "ok")


def run_cycle(cfg, scan_interval_s):
    """One scan interval: collect cfg.n_trials CLEAN traces within cfg.max_attempts real acquisitions.
    Returns 'reset_fail' (a non-clearing/bad-baseline reset -> stop the sweep) or 'ok'."""
    require_fields(cfg, ["data_root", "user"], "run_cycle")
    if cfg.temp_label == "auto":
        require_fields(cfg, ["temp_reader"], "run_cycle with temp_label='auto'")
    require_fields(cfg, ["daq_ai"], "run_cycle")
    fs = 1.0 / scan_interval_s
    tau_us = int(round(scan_interval_s * 1e6))
    base = cfg.base_name(scan_interval_s)            # display stem (target npts); the real per-trace stem uses usable npts
    id_core = cfg.id_core(scan_interval_s)           # interval+temp identity for matching existing traces (any date/npts)
    cfg.outdir.mkdir(parents=True, exist_ok=True)    # ensure the output folder exists before any reset/write
    ledger = cfg.outdir / "experiment_log.txt"
    actionlog = cfg.outdir / "action_log.txt"
    est = cfg.n_points * scan_interval_s
    n_resets = 0

    def _log(outcome, **kw):
        "Append a ledger row, filling the per-interval constants; caller passes the varying fields."
        log_experiment(ledger, dict(timestamp=datetime.datetime.now().isoformat(timespec="seconds"),
                       scan_interval_us=tau_us, n_target=cfg.n_points, n_resets=n_resets, outcome=outcome, **kw))

    def _action(action, detail=""):
        "Append a line to the action log (actions + attempted names; never measurement results)."
        log_action(actionlog, action, detail)

    def _reset(tag, n_clean):
        "reset_and_verify (counts toward n_resets); log RESET with the #tries it took to clear / RESET_FAIL. Returns ok."
        nonlocal n_resets
        ntry = reset_and_verify(cfg); n_resets += 1
        ok = ntry > 0
        _action("RESET" if ok else "RESET_FAIL", f"{ntry} tries" if ok else "not cleared")
        if not ok:
            _log("RESET_FAIL", n_clean=n_clean, attempt=tag, filename="")
        return ok

    n_clean, i = scan_indices(cfg.outdir, id_core)   # exact accepted count from the ledger; next index from the filesystem
    if n_clean >= cfg.n_trials:
        print(f"\n=== {base} · already has {n_clean}/{cfg.n_trials} clean; skipping ==="); return "ok"
    if n_clean or i > 1:
        print(f"\n=== {base} · resuming: {n_clean}/{cfg.n_trials} clean on disk; next index {i} ===")
    if not _reset(i, n_clean):
        print("    Reset did not clear (systemic); STOPPING."); return "reset_fail"

    n_attempts = 0

    def _attempt(idx):
        """One acquisition: acquire -> (post-hoc locate prefix when no live flag fired) -> classify (event +
        integrity) -> save the NONEMPTY prefix named by its usable point count -> log. A CLEAN trace is
        `accepted` (counts toward n_trials) only if usable >= cfg.min_usable_frac*n_points.
        Returns (outcome, accepted, slipped)."""
        nonlocal n_clean, n_attempts
        print(f"\n=== {base} · index {idx} · {n_attempts}/{cfg.max_attempts} used · {n_clean}/{cfg.n_trials} clean · "
              f"{cfg.n_points} pts @ {fs:.0f} Hz (~{est:.0f} s) ===")
        _t0 = datetime.datetime.now()
        print(f"    start {_t0:%H:%M:%S} · expected done ~{_t0 + datetime.timedelta(seconds=est):%H:%M:%S}  (~{est:.0f} s)")
        log_temp_now(cfg, _action)                            # one-shot MXC TEMP action line (no-op if temp_logger off)
        _action("MEASURE", f"{base}_{idx}")                   # attempted name + index (no result)
        t0 = time.time()
        with temp_logging(cfg) as tl:                         # background MXC logger (no-op if temp_logger off)
            v, got, flag = acquire_finite_chunked(cfg, cfg.daq_ai, cfg.n_points, fs)
        if flag is None:                                      # clean finish or live-check-off: locate the prefix POST-HOC
            v, got, _, kind, reason = truncate_to_clean(v, fs, jump_v=cfg.jump_v, rail_v=cfg.rail_v,
                                                         n_baseline_chunks=cfg.baseline_chunks)
            if kind is not None:                              # a post-hoc jump/rail/freeze ended the prefix -> synth the event flag
                flag = {"kind": kind, "reason": reason}
        # (jump/rail: acquire OWNS the boundary — no re-detection here; bad_baseline: v is the short baseline read)
        event, outcome, tag, reason = _classify(cfg, v, flag)
        usable_n = got; usable_s = got / fs
        bad_base = outcome == "BAD_BASELINE"
        accepted = outcome == "CLEAN" and usable_n >= cfg.min_usable_frac * cfg.n_points
        slipped = (usable_n < cfg.n_points) or outcome != "CLEAN"   # truncated (any path) or not-clean -> lock slipped
        if not bad_base: n_attempts += 1
        if accepted:     n_clean += 1
        filename = ""
        if outcome != "DEAD":                                 # save diagnostics only when the prefix is nonempty
            npts_name = cfg.n_points if bad_base else usable_n
            stem = f"{cfg.base_name(scan_interval_s, npts_name)}_{idx}" + (f"_{tag}" if tag else "")
            save_pcs102(cfg.outdir / f"{stem}.txt", v, scan_interval_s)
            save_temp_log(cfg, cfg.outdir / (stem.replace("DAQ", "TEMP", 1) + ".csv"), tl.samples)
            filename = f"{stem}.txt"
        Ts = [s[1] for s in tl.samples] or [float("nan")]
        mV = f"{v.mean():.6f}" if len(v) else ""; sV = f"{v.std():.6f}" if len(v) else ""
        _log(outcome, event=event, n_clean=n_clean, attempt=idx, filename=filename,
             usable_points=usable_n, usable_seconds=f"{usable_s:.3f}", accepted=int(accepted),
             mean_V=mV, std_V=sV, T_start_K=f"{Ts[0]:.6f}", T_end_K=f"{Ts[-1]:.6f}")
        print(f"    event={event} outcome={outcome} ({reason}) · idx {idx} · usable={usable_n} pts ({usable_s:.0f}s) · "
              f"accepted={accepted} · {time.time()-t0:.0f}s · {n_clean}/{cfg.n_trials} clean")
        if bad_base:
            _action("BAD_BASELINE", reason)
        return outcome, accepted, slipped

    def _summary():
        "Final per-interval status: DONE, or BUDGET_EXHAUSTED (logs the exhausted ledger row)."
        if n_clean >= cfg.n_trials:
            print(f"    DONE: {n_clean}/{cfg.n_trials} clean traces in {n_attempts} acquisition(s) this run.")
        else:
            _log("BUDGET_EXHAUSTED", n_clean=n_clean, attempt="", filename="")
            print(f"    BUDGET EXHAUSTED: {n_clean}/{cfg.n_trials} clean after {cfg.max_attempts} acquisitions; moving on.")

    pending_slip = False
    while n_clean < cfg.n_trials and n_attempts < cfg.max_attempts:
        outcome, accepted, slipped = _attempt(i)
        i += 1
        if outcome == "BAD_BASELINE":
            print("    Bad baseline -> reset is not holding (systemic); STOPPING."); return "reset_fail"
        pending_slip = slipped
        if not slipped:
            continue                              # full clean trace, lock intact -> next acquisition without a reset
        # a truncated (jump/rail) or surged trace -> the lock slipped; reset before the next attempt
        if n_clean < cfg.n_trials and n_attempts < cfg.max_attempts:
            if not _reset(i, n_clean):
                print("    Reset did not clear (systemic); STOPPING."); return "reset_fail"
            pending_slip = False                  # cleared by the in-loop reset
    if pending_slip:                              # the final accepted prefix left the lock slipped -> clear it (via _reset:
        if not _reset(i, n_clean):                # counts n_resets + logs RESET/RESET_FAIL) before returning
            print("    Exit reset did not clear (systemic); STOPPING."); return "reset_fail"
    _summary()
    return "ok"


