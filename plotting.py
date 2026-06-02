"""Quick-look plots: read this run's clean traces (+ their temperature logs) from disk and plot them.

Reads everything back from OUTDIR, so it works after a kernel restart and never holds the big arrays in
memory. Hardware-free (matplotlib + pandas).
"""
import re
import pandas as pd
import matplotlib.pyplot as plt

from .analysis import read_daq_file


def clean_trace_names(cfg):
    "Every CLEAN trace ({base}_{i}.txt, bare index = no outcome suffix) on disk for cfg's intervals, in acquisition order."
    names = []
    for tau in cfg.scan_intervals:
        base = cfg.base_name(tau)
        pat = re.compile(rf"{re.escape(base)}_(\d+)\.txt")
        found = [(int(m.group(1)), p.name) for p in cfg.outdir.glob(f"{base}_*.txt")
                 if (m := pat.fullmatch(p.name))]
        names.extend(nm for _, nm in sorted(found))
    return names


def plot_run(cfg, filename_list=None):
    "Plot voltage-vs-time for each clean trace, then MXC temperature-vs-time from each trace's _temp.csv."
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
        temp_path = cfg.outdir / filename.replace(".txt", "_temp.csv")
        if not temp_path.exists():
            print(f"no temp log: {temp_path.name}"); continue
        tdf = pd.read_csv(temp_path)                        # columns: time_s, T_K
        plt.figure(figsize=(11, 2.6))
        plt.plot(tdf["time_s"], tdf["T_K"], "o-", ms=3)
        plt.xlabel("Time (s)"); plt.ylabel("MXC T (K)"); plt.title(temp_path.name)
        plt.show()
