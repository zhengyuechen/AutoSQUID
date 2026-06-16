"""Quick-look plots: read this run's clean traces (+ their temperature logs) from disk and plot them.

Reads everything back from OUTDIR, so it works after a kernel restart and never holds the big arrays in
memory. Hardware-free (matplotlib + pandas).
"""
import os
import re

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from .analysis import (read_daq_file, is_surge_spec, usable_points_from_spec, truncate_to_clean,
                       accepted_trace_names, fmt_npts)
from .config import require_fields


def clean_trace_names(cfg):
    "Every ACCEPTED clean trace for cfg's intervals — exactly the ones the ledger counted toward n_trials (via accepted_trace_names), so plotting-selection matches acquisition counting. Index order."
    names = []
    for tau in cfg.scan_intervals:
        names.extend(accepted_trace_names(cfg.outdir, cfg.id_core(tau)))
    return names


def plot_run(cfg, filename_list=None):
    "Plot voltage-vs-time for each clean trace, then MXC temperature-vs-time from each trace's TEMP_*.csv."
    require_fields(cfg, ["data_root", "user"], "plot_run")
    path = str(cfg.outdir)
    names = filename_list if filename_list is not None else clean_trace_names(cfg)
    if not names:
        print("no clean traces found for this config in", path); return

    for filename in names:
        header, df = read_daq_file(path, filename)
        dt = header["SCANINTVAL"]
        t = (df.index.to_numpy() - 1) * dt                 # POINT index is 1-based -> time starts at 0
        y = df["CHAN_01(V)"].to_numpy()
        plt.figure(figsize=(11, 3.4))
        plt.plot(t, y, lw=0.4)
        plt.xlabel("Time (s)"); plt.ylabel("Array Voltage (V)"); plt.title(filename)
        plt.show()

    for filename in names:
        temp_path = cfg.outdir / filename.replace("DAQ", "TEMP", 1).replace(".txt", ".csv")
        if not temp_path.exists():
            print(f"no temp log: {temp_path.name}"); continue
        tdf = pd.read_csv(temp_path)                        # columns: time_s, T_K
        plt.figure(figsize=(11, 2.6))
        plt.plot(tdf["time_s"], tdf["T_K"], "o-", ms=3)
        plt.xlabel("Time (s)"); plt.ylabel("MXC T (K)"); plt.title(temp_path.name)
        plt.show()

def plot_usable(path, filename, usable_s=None, show_dropped=True):
    """Plot only the USABLE (clean, pre-jump) part of a saved JUMP trace. `usable_s` = the clean-prefix
    duration in seconds (e.g. the experiment log's `usable_seconds`); if None it is located with
    `usable_points_from_spec`. SURGE files are skipped (a surge has no usable part). `show_dropped` draws
    the discarded post-jump tail faintly for context. Returns (usable_points, usable_seconds)."""
    if "_SURGE" in filename:
        print(f"{filename}: surged -> not usable; skipping"); return 0, 0.0
    header, df = read_daq_file(path, filename)
    dt = header["SCANINTVAL"]
    v = df["CHAN_01(V)"].to_numpy()
    t = (df.index.to_numpy() - 1) * dt                       # 1-based POINT index -> time from 0
    n_use = int(round(usable_s / dt)) if usable_s is not None else usable_points_from_spec(v)[0]
    n_use = max(0, min(n_use, len(v)))
    plt.figure(figsize=(11, 3.4))
    if show_dropped and n_use < len(v):
        plt.plot(t[n_use:], v[n_use:], lw=0.4, color="lightgray", label="dropped (post-jump)")
    plt.plot(t[:n_use], v[:n_use], lw=0.4, color="steelblue",
             label=f"usable {n_use * dt:.1f} s ({n_use:,} pts)")
    plt.xlabel("Time (s)"); plt.ylabel("Array Voltage (V)")
    plt.title(f"{filename} — usable part"); plt.legend(loc="upper right")
    plt.tight_layout(); plt.show()
    return n_use, n_use * dt


def plot_overlay(t, v, temp_t, temp_T, title=""):
    """Overlay voltage (t,v) and a CONTINUOUS temperature line on one shared time axis."""
    fig, ax_v = plt.subplots(figsize=(11, 4))
    ax_T = ax_v.twinx()                                  # second y-axis sharing the same x (time)

    T_cont = np.interp(t, temp_t, temp_T)                # interpolate the 30 s samples onto the trace's time grid

    l1, = ax_v.plot(t, v, lw=0.4, color="steelblue", label="Array voltage (V)")
    l2, = ax_T.plot(t, T_cont, lw=1.6, color="crimson", label="MXC T (K)")   # smooth continuous line

    ax_v.set_xlabel("Time (s)")
    ax_v.set_ylabel("Array voltage (V)", color="steelblue"); ax_v.tick_params(axis="y", colors="steelblue")
    ax_T.set_ylabel("MXC T (K)",        color="crimson");   ax_T.tick_params(axis="y", colors="crimson")
    ax_v.set_title(title)
    ax_v.legend(handles=[l1, l2], loc="upper right")
    plt.tight_layout(); plt.show()

def _get_window(window_type, N):
    "Window array of length N + its power normalization U = mean(w^2), for the Welch PSD."
    builders = {"rectangle": np.ones, "hann": np.hanning, "hanning": np.hanning,
                "hamming": np.hamming, "blackman": np.blackman}
    if window_type.lower() not in builders:
        raise ValueError(f"Unknown window type: {window_type}")
    w = builders[window_type.lower()](N)
    return w, np.mean(w ** 2)


def _psd_welch(phi, dt, P, window):
    "One-sided Welch PSD of phi: split into P segments, mean-removed + windowed, averaged. Returns (f, S[phi^2/Hz])."
    Kp = len(phi) // P
    gamma = Kp * dt
    w, U = _get_window(window, Kp)
    f = np.fft.rfftfreq(Kp, dt)
    acc = np.zeros(len(f))
    for p in range(P):
        seg = phi[p * Kp:(p + 1) * Kp]
        seg = (seg - seg.mean()) * w
        acc += (2 / (gamma * U)) * np.abs(dt * np.fft.rfft(seg)) ** 2
    return f, acc / P


def plot_psd(path, filename, conversion=1, P=(10, 100, 1000, 10000), window="hanning", clean_only=True):
    "One-sided Welch PSD (f_0^2/Hz) of one trace, overlaying every segment count in P on one log-log axis; clean_only gates the trace with is_surge_spec (skips it on failure); conversion is the per-cooldown f_0/V factor."
    header, df = read_daq_file(path, filename)
    dt = header["SCANINTVAL"]
    v = df["CHAN_01(V)"].to_numpy()
    bad, reason = is_surge_spec(v)                             # mandatory pre-PSD integrity gate
    if bad:
        print(f"[integrity] {filename}: {reason}"
              + ("  -> skipped (clean_only)" if clean_only else "  -> plotted anyway"))
        if clean_only:
            return
    phi = v * conversion                                      # V -> Phi_0 (per-cooldown factor)
    P_list = [P] if isinstance(P, int) else list(P)
    plt.figure(figsize=(7, 5))
    for p in P_list:
        if len(phi) // p < 2:                                 # segment too short to FFT -> skip this P
            print(f"  {filename} P={p}: segment < 2 pts, skipped"); continue
        f, S = _psd_welch(phi, dt, p, window)
        plt.loglog(f[1:], S[1:], lw=0.8, label=f"P={p}")   # drop the f=0 (DC) bin — invalid on a log axis
    plt.xlabel("Frequency (Hz)"); plt.ylabel(r"PSD ($f_0^2$/Hz)")
    plt.title(f"PSD — {filename}"); plt.legend(); plt.tight_layout(); plt.show()


def _win_label(seg_len):
    "Short window-size label for axes/titles: 1_000_000 -> '1M', 2_500_000 -> '2p5M', 1000 -> '1000'."
    return fmt_npts(int(seg_len)).replace("pts", "")


def _seg_temp(fn):
    "Temperature token parsed from a DAQ filename (between <tau>us_ and _<npts>pts), else '' — e.g. '400mK'."
    m = re.search(r"_\d+us_([^_]+)_[0-9pM]+pts_", str(fn))
    return m.group(1) if m else ""


def plot_segment_psd_folder(path, scan_interval_us, segment_length, conversion=1,
                            temp_label=None, locate_usable=True, clean_only=False,
                            segment_clean_only=False, jump_v=0.5, rail_v=9.5, baseline_chunks=1,
                            window="hanning", ax=None):
    """Folder-based (LEDGER-FREE) segment PSDs for one scan interval: find the matching DAQ files in `path`,
    locate each file's clean pre-jump prefix (default), split that into full `segment_length` blocks, take one
    one-sided Welch PSD (phi_0^2/Hz) per block, and overlay them — each segment faint gray, the MEAN across all
    blocks in black. No experiment ledger is read, so it works on old / hand-copied / partially-migrated folders.

    Files are matched as `DAQ_*_{scan_interval_us}us_*pts_*.txt`; `_SURGE`/`_BADBASE` files and any file whose
    usable prefix is shorter than one `segment_length` are excluded; `temp_label` (e.g. "400mK") further filters
    by the trace's temp token. `locate_usable` (default True) runs `truncate_to_clean` (with `jump_v`/`rail_v`/
    `baseline_chunks`) per file and segments only the clean prefix — a NEW AutoSQUID file (already a clean prefix)
    comes back whole, a LEGACY file saved WITH its post-jump tail is cut before segmentation; set it False to
    segment each file exactly as saved (debugging raw files). `clean_only` checks the WHOLE located prefix with
    `is_surge_spec` (skips the file on failure); `segment_clean_only` instead checks each individual
    `segment_length` window and drops only the failing ones. `conversion` is the per-cooldown phi_0/V factor.
    NOTE: the blocks are ADJACENT windows of one acquisition, not independent acquisitions — read the spread
    accordingly. A `segment_clean_only`-dropped window does NOT appear in `meta` (it's left out of the PSD).

    Returns (f, psd_stack, meta): `psd_stack` is (n_segments, n_freq); `meta` has one row per KEPT segment —
    filename, segment_index, start_point, end_point, scan_interval_us, temp_label, n_points_file, usable_points,
    usable_seconds, locator_kind, locator_reason, segment_ok, segment_reason."""
    seg_len = int(segment_length)
    if seg_len <= 0:
        raise ValueError("segment_length must be positive")
    cols = ["filename", "segment_index", "start_point", "end_point", "scan_interval_us", "temp_label",
            "n_points_file", "usable_points", "usable_seconds", "locator_kind", "locator_reason",
            "segment_ok", "segment_reason"]
    pat = re.compile(rf"DAQ_.*_{int(scan_interval_us)}us_.*pts_.*\.txt$")
    files = sorted(f for f in os.listdir(path)
                   if pat.match(f) and "_SURGE" not in f and "_BADBASE" not in f
                   and (temp_label is None or f"_{temp_label}_" in f))
    f_axis, stack, rows = None, [], []
    for fn in files:
        header, df = read_daq_file(path, fn)
        dt = header["SCANINTVAL"]
        v = df["CHAN_01(V)"].to_numpy()
        n_file = len(v)
        if locate_usable:                                        # cut the clean pre-jump prefix BEFORE segmenting
            v, usable_n, usable_s, kind, reason = truncate_to_clean(
                v, 1.0 / dt, jump_v=jump_v, rail_v=rail_v, n_baseline_chunks=baseline_chunks)
        else:
            usable_n, usable_s, kind, reason = n_file, n_file * dt, None, "as-saved"
        if usable_n < seg_len:                                   # not even one full window of usable data
            continue
        if clean_only:
            bad, sreason = is_surge_spec(v)
            if bad:
                print(f"[integrity] {fn}: {sreason} -> skipped (clean_only)"); continue
        tlab = _seg_temp(fn)
        for k in range(usable_n // seg_len):
            a, b = k * seg_len, (k + 1) * seg_len
            seg = v[a:b]
            segment_ok, segment_reason = True, "ok"
            if segment_clean_only:
                bad, segment_reason = is_surge_spec(seg)
                segment_ok = not bad
                if bad:
                    continue                                     # drop ONLY this window (not added to stack/meta)
            f_axis, S = _psd_welch(seg * conversion, dt, 1, window)
            stack.append(S)
            rows.append(dict(filename=fn, segment_index=k, start_point=a, end_point=b,
                             scan_interval_us=int(scan_interval_us), temp_label=tlab, n_points_file=n_file,
                             usable_points=int(usable_n), usable_seconds=round(usable_s, 3),
                             locator_kind=kind or "", locator_reason=reason,
                             segment_ok=segment_ok, segment_reason=segment_reason))
    meta = pd.DataFrame(rows, columns=cols)
    if not stack:
        print(f"no usable segments at {scan_interval_us}us"
              + (f" / {temp_label}" if temp_label else "") + f" in {path}")
        return f_axis, np.empty((0, 0)), meta

    psd_stack = np.array(stack)
    mean = psd_stack.mean(axis=0)
    own_ax = ax is None
    if own_ax:
        _, ax = plt.subplots(figsize=(7, 5))
    for S in psd_stack:                                       # raw per-segment PSDs, faint and behind
        ax.loglog(f_axis[1:], S[1:], lw=0.4, color="0.8")
    ax.loglog(f_axis[1:], mean[1:], lw=1.4, color="black", label="mean")
    win = _win_label(seg_len)
    ax.set_xlabel("Frequency (Hz)"); ax.set_ylabel(r"PSD ($\phi_0^2$/Hz)")
    ax.set_title(f"Segment PSDs — {scan_interval_us}us" + (f" / {temp_label}" if temp_label else "")
                 + f"  ({len(psd_stack)} × {win} from {meta['filename'].nunique()} files)")
    ax.legend()
    if own_ax:
        plt.tight_layout(); plt.show()
    return f_axis, psd_stack, meta
