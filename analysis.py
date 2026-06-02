"""Pure analysis + I/O for SQUID traces: surge/jump detection, temperature labels, PCS102 read/write,
the experiment ledger, and resume bookkeeping.

No instrument hardware — numpy + pandas + stdlib only (the detection/label functions need only numpy).
"""
import os
import re
import datetime
import numpy as np
import pandas as pd


def is_surge_spec(v, 
                  win=2000, 
                  n_baseline_chunks=1, jump_sigma=6.0, rail_v=9.5, first_chunk_v=0.1):
    """Post-hoc surge detector: rail catch, already-surged first-chunk pre-check,
    and baseline-deviation |mu_chunk - mu0|/sigma0 > jump_sigma. Returns (bad, reason)."""
    v = np.asarray(v, dtype=float)
    if np.max(np.abs(v)) > rail_v:
        return True, f"railed (|V|max={np.max(np.abs(v)):.2f} > {rail_v})"
    nc = max(len(v) // win, 1)
    chunks = np.array_split(v, nc)
    mu = np.array([c.mean() for c in chunks])
    sd = np.array([c.std() for c in chunks])
    if abs(mu[0]) > first_chunk_v:
        return True, f"first-chunk mean {mu[0]:+.3f} V > {first_chunk_v} V (already surged?)"
    k = min(n_baseline_chunks, nc)
    mu0 = mu[:k].mean()
    sigma0 = max(sd[:k].mean(), 1e-7)
    dev = np.abs(mu - mu0) / sigma0
    if dev.max() > jump_sigma:
        return True, f"baseline deviation {dev.max():.1f} sigma0 at chunk {int(dev.argmax())}/{nc}"
    return False, "ok"


def chunk_jump(seg_mean, seg_absmax, mu0, jump_v=1.0, rail_v=9.5, baseline_v=0.1):
    """Per-chunk live check; returns (kind, reason) or None. In the baseline window (mu0 is None) any
    excursion is 'bad_baseline' (reset didn't hold); afterward it's 'rail' or 'jump'."""
    if mu0 is None:
        if seg_absmax > rail_v:
            return ("bad_baseline", f"railed at start (|V|max={seg_absmax:.2f}); reset did not hold")
        if abs(seg_mean) > baseline_v:
            return ("bad_baseline", f"high baseline {seg_mean:+.3f} V (>{baseline_v} V); reset did not hold")
        return None
    if seg_absmax > rail_v:
        return ("rail", f"railed (|V|max={seg_absmax:.2f} > {rail_v})")
    if abs(seg_mean - mu0) > jump_v:
        return ("jump", f"{jump_v:g} V jump (|d-mu|={abs(seg_mean - mu0):.3f} V)")
    return None


def format_temp_label(T_K):
    """Filename label: <100 mK -> nearest 1 mK; 100 mK-1 K -> nearest 10 mK; >=1 K -> nearest 0.1 K ('p' decimal)."""
    mK = T_K * 1000.0
    if T_K < 1.0:
        step = 1 if mK < 100 else 10
        return f"{int(round(mK / step)) * step}mK"
    s = f"{round(T_K, 1):.1f}".rstrip('0').rstrip('.')
    return s.replace('.', 'p') + "K"


def save_pcs102(path, v, scan_interval_s, channels=1):
    """Write a 1-D voltage array to PCS102 DAQ .txt format (tab-separated, 1-based POINT index; matches data/)."""
    n = len(v); now = datetime.datetime.now()
    header = ("PCS102\n"
              f"DATE={now:%m-%d-%Y}\n"
              f"TIME={now:%H:%M:%S}\n"
              f"CHANNELS={channels}\n"
              f"DATAPOINTS={n}\n"
              f"SCANINTVAL={scan_interval_s:.2e}\n"
              + " " * 9 + "\n" + "TEXT:  \n" + " " * 9 + "\n"
              + " " * 3 + "POINT" + " " * 9 + "CHAN_01(V)" + " " * 6 + "\n" + " " * 3 + "\n")
    with open(path, "w") as f:
        f.write(header)
        np.savetxt(f, np.column_stack([np.arange(1, n + 1), v]), fmt="%8d\t % .6f\t")


def save_temp_csv(path, samples):
    """Write the temperature log (list of (t_rel_s, T_K)) to a 2-column CSV."""
    with open(path, "w") as f:
        f.write("time_s,T_K\n")
        for t_rel, T in samples:
            f.write(f"{t_rel:.3f},{T:.6f}\n")


def read_daq_file(path, filename):
    """Read a PCS102 DAQ .txt into (header_info, df) — from data-analysis/Calculations during measurement."""
    truepath = os.path.join(path, filename)
    with open(truepath, 'r') as file:
        lines = file.readlines()
    header_info = {}
    for line in lines[:6]:
        if '=' in line:
            key, value = line.strip().split('=')
            if key in ("DATE", "TIME"):           header_info[key] = value
            elif key in ("CHANNELS", "DATAPOINTS"): header_info[key] = int(value)
            elif key == "SCANINTVAL":             header_info[key] = float(value)
            else:                                 header_info[key] = value
        else:
            header_info[line.strip()] = None
    text_index = next(i for i, line in enumerate(lines) if "TEXT:" in line)
    column_names = lines[text_index + 2].strip().split()
    df = pd.read_csv(truepath, sep=r'\s+', skiprows=text_index + 3, header=None, names=column_names)
    df.set_index("POINT", inplace=True)
    return header_info, df


LEDGER_COLS = ["timestamp", "scan_interval_us", "n_target", "n_acquired", "n_clean", "attempt",
               "outcome", "jump_index", "jump_time_s", "n_resets", "mean_V", "std_V",
               "T_start_K", "T_end_K", "filename"]


def log_experiment(path, row):
    """Append one attempt as a tab-separated row to the experiment ledger (writes header if new)."""
    new = not os.path.exists(path)
    with open(path, "a") as f:
        if new:
            f.write("\t".join(LEDGER_COLS) + "\n")
        f.write("\t".join(str(row.get(c, "")) for c in LEDGER_COLS) + "\n")


def scan_indices(outdir, base):
    """Scan {base}_{k}[_OUTCOME].txt on disk; return (n_clean, next_idx): clean-trace count and the next
    free order index (max existing index + 1, over clean AND failed) so a resume never overwrites."""
    n_clean = 0; max_idx = 0
    for p in outdir.glob(f"{base}_*.txt"):
        m = re.fullmatch(rf"{re.escape(base)}_(\d+)(_[A-Z]+)?\.txt", p.name)
        if not m:
            continue
        max_idx = max(max_idx, int(m.group(1)))
        if m.group(2) is None:
            n_clean += 1
    return n_clean, max_idx + 1
