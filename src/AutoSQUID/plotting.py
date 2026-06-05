"""Quick-look plots: read this run's clean traces (+ their temperature logs) from disk and plot them.

Reads everything back from OUTDIR, so it works after a kernel restart and never holds the big arrays in
memory. Hardware-free (matplotlib + pandas).
"""
import re
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from .analysis import read_daq_file, is_surge_spec
from .config import require_fields


def clean_trace_names(cfg):
    "Every CLEAN trace (bare index = no outcome suffix) on disk for cfg's intervals, matched by date-agnostic core (any day), in index order."
    names = []
    for tau in cfg.scan_intervals:
        core = cfg.core_name(tau)
        pat = re.compile(rf"DAQ_(?:[^_]+_)?{re.escape(core)}_(\d+)\.txt")   # bare index, any date -> a clean trace
        found = [(int(m.group(1)), p.name) for p in cfg.outdir.glob("DAQ_*.txt")
                 if (m := pat.fullmatch(p.name))]
        names.extend(nm for _, nm in sorted(found))
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
    "One-sided Welch PSD (Phi_0^2/Hz) of one trace, overlaying every segment count in P on one log-log axis; clean_only gates the trace with is_surge_spec (skips it on failure); conversion is the per-cooldown Phi_0/V factor."
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
    plt.xlabel("Frequency (Hz)"); plt.ylabel(r"PSD ($\Phi_0^2$/Hz)")
    plt.title(f"PSD — {filename}"); plt.legend(); plt.tight_layout(); plt.show()
