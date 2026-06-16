"""Pure analysis + I/O for SQUID traces: surge/jump detection, temperature labels, PCS102 read/write,
the experiment ledger + action log, and resume bookkeeping.

No instrument hardware — numpy + pandas + stdlib only (the detection/label functions need only numpy).
"""
import os
import re
import datetime
from pathlib import Path
import numpy as np
import pandas as pd


def _chunk_stats(v, win=2000, n_baseline_chunks=1):
    """Split v into ~win-point chunks; return (chunks, mu, sd, amax, mu0, sigma0): the chunk views,
    per-chunk mean/std/max|.|, and the baseline mean/std over the first n_baseline_chunks chunks.
    The single lower-level analysis shared by is_surge_spec and usable_points_from_spec so they cannot
    drift (one decides whether the WHOLE trace fails, the other WHERE the failure begins)."""
    v = np.asarray(v, dtype=float)
    nc = max(len(v) // win, 1)
    chunks = np.array_split(v, nc)
    mu = np.array([c.mean() for c in chunks])
    sd = np.array([c.std() for c in chunks])
    amax = np.array([float(np.max(np.abs(c))) for c in chunks])
    k = min(n_baseline_chunks, nc)
    mu0 = float(mu[:k].mean())
    sigma0 = max(float(sd[:k].mean()), 1e-7)
    return chunks, mu, sd, amax, mu0, sigma0


def is_surge_spec(v,
                  win=2000,
                  n_baseline_chunks=1, jump_sigma=6.0, rail_v=9.5, first_chunk_v=0.1,
                  min_std=5e-5, stuck_frac=0.1, stuck_max_frac=0.02):
    """Post-hoc surge detector (whether the WHOLE trace fails): rail catch, already-surged first-chunk
    pre-check, a stuck/flat catch (a healthy noise trace fluctuates; a frozen one's per-chunk std
    collapses), and baseline-deviation |mu_chunk - mu0|/sigma0 > jump_sigma. Returns (bad, reason)."""
    v = np.asarray(v, dtype=float)
    if len(v) == 0:
        return True, "empty/dead trace (0 points)"
    chunks, mu, sd, amax, mu0, sigma0 = _chunk_stats(v, win, n_baseline_chunks)
    nc = len(mu)
    if float(amax.max()) > rail_v:
        return True, f"railed (|V|max={float(amax.max()):.2f} > {rail_v})"
    if abs(mu[0]) > first_chunk_v:
        return True, f"first-chunk mean {mu[0]:+.3f} V > {first_chunk_v} V (already surged?)"
    live = float(np.percentile(sd, 95))                     # trace's noise level, robust to a few spuriously-noisy chunks
    if live < min_std:                                      # no chunk shows real noise -> flat/dead throughout
        return True, f"flat/dead trace (max chunk std {live:.2e} V < {min_std})"
    stuck = sd < stuck_frac * live                          # chunks whose noise collapsed vs the live level
    if stuck.mean() > stuck_max_frac:                       # frozen/stuck over more than stuck_max_frac of the run
        return True, f"stuck/frozen: {100 * stuck.mean():.0f}% of chunks have std < {stuck_frac:g}x the live level"
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


def usable_points_from_spec(v, win=2000, n_baseline_chunks=1, jump_v=0.5, rail_v=9.5, stuck_frac=0.1,
                            min_std=5e-5, gap_tol=3):
    """POST-HOC clean-prefix locator (runs after ANY acquisition that completed without a live flag —
    a slip the live check missed, or any run drained with live checking off — plus plotting; when a live
    flag fired the run stopped at that chunk with no persistence and this is NOT called). INDEPENDENT of
    is_surge_spec (they only share
    `_chunk_stats`): scans every chunk with ABSOLUTE thresholds — rail_v, |mu-mu0| > jump_v, or freeze vs
    a robust 95th-pct noise level — and cuts at the ONSET OF THE PERSISTENT failing run that reaches the
    end, walking back from the end and tolerating up to `gap_tol` isolated clean chunks. A latched
    jump/rail/freeze is cut; a transient that RECOVERS is kept (full). An empty trace, or one whose
    robust noise level is below `min_std` (flat/dead throughout), returns (0, 'dead', reason). Returns
    (usable_points, kind, reason) with kind in {None (full), 'jump','rail','frozen','dead'}."""
    v = np.asarray(v, dtype=float)
    if len(v) == 0:
        return 0, "dead", "empty/dead trace (0 points)"
    chunks, mu, sd, amax, mu0, sigma0 = _chunk_stats(v, win, n_baseline_chunks)
    nc = len(mu); base_k = min(n_baseline_chunks, nc)
    live = float(np.percentile(sd, 95))                    # robust noise level (matches is_surge_spec)
    if live < min_std:                                     # whole trace flat/dead -> no clean prefix to keep
        return 0, "dead", f"flat/dead trace (noise {live:.2e} V < {min_std:g})"
    railed = amax > rail_v
    stepped = np.abs(mu - mu0) > jump_v                    # absolute step off baseline, like chunk_jump
    frozen = sd < stuck_frac * live
    bad = railed | stepped | frozen
    bad[:base_k] = False                                  # the baseline window is the reference
    cut_chunk = nc; clean_run = 0                          # onset of the persistent failing run reaching the end
    for i in range(nc - 1, base_k - 1, -1):
        if bad[i]:
            clean_run = 0; cut_chunk = i
        else:
            clean_run += 1
            if clean_run > gap_tol:                        # more than gap_tol clean chunks -> failure (if any) recovered here
                break
    if cut_chunk == nc:                                    # no persistent trailing failure -> full trace
        return len(v), None, "ok"
    n_use = int(sum(len(c) for c in chunks[:cut_chunk]))
    j = cut_chunk
    if railed[j]:
        kind, reason = "rail", f"railed (|V|max={amax[j]:.2f} > {rail_v})"
    elif stepped[j]:
        kind, reason = "jump", f"{abs(mu[j] - mu0):.2f} V step off baseline (> {jump_v} V)"
    else:
        kind, reason = "frozen", f"frozen (chunk std {sd[j]:.2e} < {stuck_frac:g}x live)"
    return n_use, kind, reason


def truncate_to_clean(v, fs, **locator_kw):
    """Truncate a trace to its clean pre-failure prefix (located POST-HOC by usable_points_from_spec — no
    live flag, so this works on already-saved traces / live-off). Extra kwargs (jump_v, rail_v,
    n_baseline_chunks, …) pass through to the locator. Returns (clean_v, usable_points, usable_seconds,
    kind, reason); kind is None for a full clean trace."""
    n_use, kind, reason = usable_points_from_spec(v, **locator_kw)
    return v[:n_use], n_use, n_use / fs, kind, reason


def fmt_npts(n):
    """Filename point-count tag: '10Mpts' for a whole number of millions, else floored to 0.1 M with 'p'
    for the decimal (e.g. 8_454_000 -> '8p4Mpts'); a sub-0.1 M count falls back to '<n>pts'."""
    if n % 1_000_000 == 0:
        return f"{n // 1_000_000}Mpts"
    tenths = n // 100_000                                 # floor to 0.1 M
    return f"{tenths // 10}p{tenths % 10}Mpts" if tenths else f"{n}pts"


def parse_npts(tag):
    """Inverse of fmt_npts: a point-count tag -> integer points (floored value). None if unrecognized.
    '10Mpts'->10_000_000, '8p4Mpts'->8_400_000, '20000pts'->20000."""
    if tag.endswith("Mpts"):
        body = tag[:-4]
        if "p" in body:
            whole, frac = body.split("p")
            return (int(whole) * 10 + int(frac)) * 100_000
        return int(body) * 1_000_000
    if tag.endswith("pts"):
        return int(tag[:-3])
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


LEDGER_COLS = ["timestamp", "scan_interval_us", "n_target", "n_clean", "goal_progress", "attempt",
               "event", "outcome", "usable_points", "usable_seconds", "accepted",
               "goal_mode", "segment_length", "accepted_segments", "n_resets",
               "mean_V", "std_V", "T_start_K", "T_end_K", "filename"]


def _migrate_ledger(path):
    """If `path` is an existing ledger predating the 0.2 column schema, migrate it IN PLACE: back up to
    `<path>.bak`, then rewrite every row realigned to LEDGER_COLS BY NAME (removed columns dropped, new
    columns blank), deriving `accepted="1"` for pre-0.2 `outcome=="CLEAN"` rows so existing clean traces
    still count on resume. Idempotent — a current-schema (or absent) ledger is left untouched. Runs on
    both the read path (`accepted_trace_names`, before resume counts) and the write path
    (`log_experiment`, before appending), so an old folder is upgraded whichever happens first."""
    if not os.path.exists(path):
        return
    with open(path) as f:
        lines = f.read().splitlines()
    if not lines or lines[0].split("\t") == LEDGER_COLS:
        return
    old_cols = lines[0].split("\t")
    with open(f"{path}.bak", "w") as b:                        # f-string -> Path-safe (no str + Path)
        b.write("\n".join(lines) + "\n")
    with open(path, "w") as f:
        f.write("\t".join(LEDGER_COLS) + "\n")
        for line in lines[1:]:
            rec = dict(zip(old_cols, line.split("\t")))
            if not rec.get("accepted") and rec.get("outcome") == "CLEAN":
                rec["accepted"] = "1"                          # pre-0.2 clean trace -> counts on resume
            f.write("\t".join(rec.get(c, "") for c in LEDGER_COLS) + "\n")
    print(f"[AutoSQUID] migrated ledger {len(old_cols)}->{len(LEDGER_COLS)} cols "
          f"({path}); pre-0.2 rows backed up to {path}.bak")


def log_experiment(path, row):
    """Append one attempt as a tab-separated row to the experiment ledger (writes header if new). A
    pre-0.2 ledger in the folder is migrated first (see _migrate_ledger) so the appended 16-column row
    never produces a ragged file."""
    _migrate_ledger(path)
    new = not os.path.exists(path)
    with open(path, "a") as f:
        if new:
            f.write("\t".join(LEDGER_COLS) + "\n")
        f.write("\t".join(str(row.get(c, "")) for c in LEDGER_COLS) + "\n")


def log_action(path, action, detail=""):
    "Append a timestamped action line (reset / reset_fail / bad_baseline / temperature / measurement attempt / s_tune) to the action log — actions + attempted names only, NEVER measurement results."
    new = not os.path.exists(path)
    with open(path, "a") as f:
        if new:
            f.write("timestamp\taction\tdetail\n")
        f.write(f"{datetime.datetime.now().isoformat(timespec='seconds')}\t{action}\t{detail}\n")


def accepted_trace_names(outdir, id_core, ledger_name="experiment_log.txt"):
    """Filenames the EXPERIMENT LEDGER recorded as accepted (`accepted`==1) for this (interval,temp)
    `id_core` whose file still exists on disk, in index order. Exact (uses the ledger's accepted flag, set
    from the precise usable_points at write time — immune to filename rounding)."""
    outdir = Path(outdir)
    ledger = outdir / ledger_name
    if not ledger.exists():
        return []
    _migrate_ledger(ledger)                                   # upgrade a pre-0.2 folder before counting
    try:
        df = pd.read_csv(ledger, sep="\t", dtype=str)
    except Exception:                                      # noqa: BLE001
        return []
    if "accepted" not in df.columns or "filename" not in df.columns:
        return []
    pat = re.compile(rf"DAQ_(?:[^_]+_)?{re.escape(id_core)}_[0-9pM]+pts_(\d+)(?:_[A-Z]+)?\.txt")
    by_idx = {}
    for fn, acc in zip(df["filename"].fillna(""), df["accepted"].fillna("")):
        m = pat.fullmatch(str(fn))
        if str(acc) == "1" and m and (outdir / str(fn)).exists():
            by_idx[int(m.group(1))] = str(fn)              # by index -> dedup if the same file logged twice
    return [by_idx[k] for k in sorted(by_idx)]


def scan_indices(outdir, id_core, ledger_name="experiment_log.txt"):
    """Resume bookkeeping for this (interval,temp) `id_core`. Returns (n_clean, next_idx):
    n_clean = the EXACT count of accepted trials from the ledger whose file still exists (uses the ledger's
    accepted/usable_points, so a custom min_usable_frac never miscounts via filename rounding);
    next_idx = max index on the FILESYSTEM + 1 (so a resume never overwrites ANY existing trace, ledgered
    or not, on any date)."""
    outdir = Path(outdir)
    n_clean = len(accepted_trace_names(outdir, id_core, ledger_name))
    pat = re.compile(rf"DAQ_(?:[^_]+_)?{re.escape(id_core)}_[0-9pM]+pts_(\d+)(?:_[A-Z]+)?\.txt")
    max_idx = 0
    for p in outdir.glob("DAQ_*.txt"):
        m = pat.fullmatch(p.name)
        if m:
            max_idx = max(max_idx, int(m.group(1)))
    return n_clean, max_idx + 1


def _segment_rows(outdir, id_core, segment_length, ledger_name="experiment_log.txt"):
    """{trace_index: (filename, accepted_segments)} for SEGMENT-mode ledger rows of this (interval,temp)
    `id_core` at this EXACT `segment_length` whose file still exists (deduped by index). The shared
    primitive behind segment_progress / segment_trace_names. Requiring goal_mode=='segment' AND a matching
    segment_length keeps trials-mode rows and other window sizes (a later 2M goal) out of the count."""
    outdir = Path(outdir)
    ledger = outdir / ledger_name
    if not ledger.exists():
        return {}
    _migrate_ledger(ledger)
    try:
        df = pd.read_csv(ledger, sep="\t", dtype=str)
    except Exception:                                      # noqa: BLE001
        return {}
    if not {"filename", "accepted_segments", "segment_length", "goal_mode"}.issubset(df.columns):
        return {}
    pat = re.compile(rf"DAQ_(?:[^_]+_)?{re.escape(id_core)}_[0-9pM]+pts_(\d+)(?:_[A-Z]+)?\.txt")
    rows = {}
    for fn, seg, slen, gm in zip(df["filename"].fillna(""), df["accepted_segments"].fillna(""),
                                 df["segment_length"].fillna(""), df["goal_mode"].fillna("")):
        m = pat.fullmatch(str(fn))
        if not (m and str(gm) == "segment" and (outdir / str(fn)).exists()):
            continue
        try:
            if int(float(slen)) != int(segment_length):
                continue
            ns = int(float(seg)) if str(seg).strip() != "" else 0
        except (ValueError, TypeError):
            continue
        rows[int(m.group(1))] = (str(fn), ns)
    return rows


def segment_progress(outdir, id_core, segment_length, ledger_name="experiment_log.txt"):
    """Segment-mode resume count: sum of `accepted_segments` the ledger recorded for this (interval,temp)
    `id_core` in SEGMENT mode AT THIS EXACT `segment_length`, over rows whose file still exists (deduped by
    index). Exact (ledger usable_points-derived, never filenames)."""
    return sum(ns for _fn, ns in _segment_rows(outdir, id_core, segment_length, ledger_name).values())


def segment_trace_names(outdir, id_core, segment_length, ledger_name="experiment_log.txt"):
    """Filenames (index order) of SEGMENT-mode traces that contributed >=1 full `segment_length` window for
    this (interval,temp) `id_core` and still exist on disk. The segment-mode analogue of accepted_trace_names
    (segment mode sets accepted=0, so accepted_trace_names finds nothing) — for Phase-2 segment plotting."""
    rows = _segment_rows(outdir, id_core, segment_length, ledger_name)
    return [rows[k][0] for k in sorted(rows) if rows[k][1] > 0]


def backfill_ledger(outdir, cfg=None, segment_length=None, dry_run=True):
    """Reconstruct MISSING ledger VALUES after the fact — distinct from _migrate_ledger, which only makes the
    COLUMNS exist. For each row, fill ONLY currently-blank fields it can confidently infer; NEVER overwrite a
    present value, NEVER invent `timestamp`/`n_resets`/`event`, NEVER write `nan`. Physics/data fields come from
    the saved DAQ `.txt` (read only when one of them is blank); `T_start_K`/`T_end_K` only from a matching
    `TEMP_*.csv`; goal fields only from context (`cfg` and/or `segment_length`, else an existing per-row value).
    `goal_progress` (and trials-mode `n_clean`) are filled as the running cumulative over rows in ledger order.

    cfg (optional) supplies goal_mode (segment if cfg.segment_goal else trials), segment_length (cfg.segment_len),
    n_points, and min_usable_frac. `segment_length` arg overrides/provides the window size when no cfg is given.
    Returns a list of {"filename", "filled": {col: value}} for the rows that changed; with dry_run=True (default)
    the ledger is NOT written — explicit on purpose, since post-run inference can be wrong if files were edited."""
    outdir = Path(outdir)
    ledger = outdir / "experiment_log.txt"
    if not ledger.exists():
        return []
    _migrate_ledger(ledger)
    df = pd.read_csv(ledger, sep="\t", dtype=str).fillna("")
    rows = df.to_dict("records")

    ctx_mode = ""
    if cfg is not None:
        ctx_mode = "segment" if cfg.segment_goal is not None else "trials" if cfg.n_trials is not None else ""
    ctx_seg = (int(segment_length) if segment_length is not None
               else int(cfg.segment_len) if (cfg is not None and cfg.segment_len is not None) else None)
    ctx_npts = int(cfg.n_points) if cfg is not None else None
    ctx_frac = float(cfg.min_usable_frac) if cfg is not None else None

    def _blank(v):
        return v is None or str(v).strip() in ("", "nan")

    def _int(v):
        return int(float(v)) if not _blank(v) else None

    idx_pat = re.compile(r"_(\d+)(?:_[A-Z]+)?\.txt$")
    running_progress, running_clean, changes = 0, 0, []

    for row in rows:
        fn = row.get("filename", "")
        filled = {}
        on_disk = bool(fn) and (outdir / fn).exists()

        def _eff(col):
            return filled.get(col, row.get(col, ""))

        # --- goal_mode / segment_length context (row value wins; else cfg/arg) ---
        gm = row.get("goal_mode", "") or ctx_mode
        if _blank(row.get("goal_mode")) and gm:
            filled["goal_mode"] = gm
        if _blank(row.get("segment_length")) and gm == "segment" and ctx_seg is not None:
            filled["segment_length"] = str(ctx_seg)
        slen_i = _int(_eff("segment_length"))

        if _blank(row.get("n_target")) and ctx_npts is not None:
            filled["n_target"] = str(ctx_npts)

        # --- data-derived fields from the DAQ file (read it only if one of them is blank) ---
        if on_disk and any(_blank(row.get(c)) for c in
                           ("usable_points", "usable_seconds", "scan_interval_us", "mean_V", "std_V", "outcome")):
            try:
                header, dfv = read_daq_file(str(outdir), fn)
                v = dfv.iloc[:, 0].to_numpy(dtype=float)
            except Exception:                                      # noqa: BLE001
                header, v = None, None
            if header is not None and v is not None:
                up = int(header.get("DATAPOINTS", len(v))); si = float(header["SCANINTVAL"])
                if _blank(row.get("usable_points")):    filled["usable_points"] = str(up)
                if _blank(row.get("usable_seconds")):   filled["usable_seconds"] = f"{up * si:.3f}"
                if _blank(row.get("scan_interval_us")): filled["scan_interval_us"] = str(int(round(si * 1e6)))
                if len(v):
                    if _blank(row.get("mean_V")):  filled["mean_V"] = f"{v.mean():.6f}"
                    if _blank(row.get("std_V")):   filled["std_V"] = f"{v.std():.6f}"
                    if _blank(row.get("outcome")):
                        surged, _ = is_surge_spec(v)
                        filled["outcome"] = "SURGE" if surged else "CLEAN"

        usable_i = _int(_eff("usable_points")); outcome = _eff("outcome")

        if _blank(row.get("attempt")) and fn:
            m = idx_pat.search(fn)
            if m:
                filled["attempt"] = m.group(1)

        # --- accepted / accepted_segments + cumulative goal_progress / n_clean ---
        contrib = None
        if gm == "segment" and slen_i:
            if _blank(row.get("accepted_segments")) and usable_i is not None and outcome in ("CLEAN", "SURGE", "DEAD"):
                filled["accepted_segments"] = str((usable_i // slen_i) if outcome == "CLEAN" else 0)
            contrib = _int(_eff("accepted_segments"))
        elif gm == "trials":
            if _blank(row.get("accepted")) and usable_i is not None and outcome in ("CLEAN", "SURGE", "DEAD") \
                    and ctx_npts is not None and ctx_frac is not None:
                filled["accepted"] = str(1 if (outcome == "CLEAN" and usable_i >= ctx_frac * ctx_npts) else 0)
            contrib = _int(_eff("accepted"))
        if contrib is not None:
            running_progress += contrib
            if _blank(row.get("goal_progress")):
                filled["goal_progress"] = str(running_progress)
            if gm == "trials":
                running_clean += contrib
                if _blank(row.get("n_clean")):
                    filled["n_clean"] = str(running_clean)

        # --- temperature only from a matching TEMP_*.csv (never nan) ---
        if on_disk and (_blank(row.get("T_start_K")) or _blank(row.get("T_end_K"))):
            temp_csv = outdir / (fn[:-4].replace("DAQ", "TEMP", 1) + ".csv")
            if temp_csv.exists():
                try:
                    t = pd.read_csv(temp_csv)
                    if len(t):
                        Tcol = t.columns[-1]                       # (time_s, T_K) -> last column is T
                        if _blank(row.get("T_start_K")): filled["T_start_K"] = f"{float(t[Tcol].iloc[0]):.6f}"
                        if _blank(row.get("T_end_K")):   filled["T_end_K"] = f"{float(t[Tcol].iloc[-1]):.6f}"
                except Exception:                                  # noqa: BLE001
                    pass

        if filled:
            row.update(filled)
            changes.append({"filename": fn, "filled": filled})

    if not dry_run and changes:
        with open(ledger, "w") as f:
            f.write("\t".join(LEDGER_COLS) + "\n")
            for row in rows:
                f.write("\t".join(str(row.get(c, "")) for c in LEDGER_COLS) + "\n")
    return changes
