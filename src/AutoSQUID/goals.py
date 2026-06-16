"""Goal/progress policy for the measurement cycle — pure bookkeeping, no hardware.

Two mutually-exclusive goals decide when a scan interval is done:
  trials  (cfg.n_trials)       -> stop after N accepted full traces (each n_points long)
  segment (cfg.segment_goal)   -> stop after N full clean `segment_length` windows harvested
                                  from the n_points runs (including the clean prefixes of JUMP traces)

`measurement.run_cycle` owns the hardware state machine and calls these for mode detection,
contribution counting, resume progress, done-ness, and display. Keeping the counting policy out
of measurement.py keeps the hardware loop free of mode special-cases. The acquisition length is
ALWAYS cfg.n_points — segment_goal never changes it.
"""
from .analysis import accepted_trace_names, segment_progress


def goal_mode(cfg):
    "Active acquisition goal: 'segment' if cfg.segment_goal is set, else 'trials'."
    return "segment" if cfg.segment_goal is not None else "trials"


def validate_goal(cfg):
    "Exactly one of cfg.n_trials / cfg.segment_goal, well-formed (positive ints; segment_goal a 2-tuple; n_points >= segment_length). Raise otherwise."
    def _pos_int(x):
        return isinstance(x, int) and not isinstance(x, bool) and x > 0
    has_trials = cfg.n_trials is not None
    has_seg = cfg.segment_goal is not None
    if has_trials and has_seg:
        raise RuntimeError("both cfg.n_trials and cfg.segment_goal are set — comment one out to continue")
    if not has_trials and not has_seg:
        raise RuntimeError("no acquisition target set — set cfg.n_trials OR cfg.segment_goal (e.g. (20, 1_000_000))")
    if has_trials and not _pos_int(cfg.n_trials):
        raise RuntimeError(f"cfg.n_trials must be a positive integer, got {cfg.n_trials!r}")
    if has_seg:
        sg = cfg.segment_goal
        if not (isinstance(sg, (tuple, list)) and len(sg) == 2):
            raise RuntimeError(f"cfg.segment_goal must be a (n_segments, segment_length) pair, got {sg!r}")
        n_seg, seg_len = sg
        if not (_pos_int(n_seg) and _pos_int(seg_len)):
            raise RuntimeError(f"cfg.segment_goal values must be positive integers, got {sg!r}")
        if cfg.n_points < seg_len:
            raise RuntimeError(f"n_points ({cfg.n_points}) < segment_length ({seg_len}) — a run can't hold one full segment")


def target_count(cfg):
    "Stopping target in the active unit: segment count (segment mode) or trial count (trials mode)."
    return cfg.segment_goal[0] if goal_mode(cfg) == "segment" else cfg.n_trials


def unit(cfg):
    "Display unit for the active goal: 'segments' or 'traces'."
    return "segments" if goal_mode(cfg) == "segment" else "traces"


def contribution(cfg, outcome, usable_points):
    """How much a just-saved trace adds to progress in the active unit.
    segment mode: floor(usable_points / segment_length) full windows, but only for a CLEAN prefix
    (JUMP-truncated-but-clean contributes; SURGE/DEAD/BAD_BASELINE -> 0).
    trials mode: 1 accepted trace iff CLEAN and usable_points >= min_usable_frac * n_points, else 0."""
    if goal_mode(cfg) == "segment":
        return (usable_points // cfg.segment_len) if outcome == "CLEAN" else 0
    return 1 if (outcome == "CLEAN" and usable_points >= cfg.min_usable_frac * cfg.n_points) else 0


def scan_progress(outdir, id_core, cfg):
    "Progress already on disk for this (interval,temp): summed segments (segment mode) or accepted-trace count (trials)."
    if goal_mode(cfg) == "segment":
        return segment_progress(outdir, id_core, cfg.segment_len)
    return len(accepted_trace_names(outdir, id_core))


def is_done(progress, cfg):
    "Has the active goal been met?"
    return progress >= target_count(cfg)


def progress_str(progress, cfg):
    "Human-facing progress, e.g. '8/20 segments'."
    return f"{progress}/{target_count(cfg)} {unit(cfg)}"
