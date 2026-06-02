"""Generate the two thin notebooks (measurement_cycle, auto_s_tune) that drive the `AutoSQUID` package.

Run:  python _build_notebooks.py    (writes measurement_cycle.ipynb + auto_s_tune.ipynb in this folder)
The notebooks are thin: §0 edits a Config, the rest call AutoSQUID.* — all logic lives in the package.
"""
import ast
import nbformat as nbf
from nbformat.v4 import new_notebook, new_markdown_cell, new_code_cell

# ---- shared header: make `import AutoSQUID` resolve when the notebook sits inside the package folder ----
BOOT = r'''# add the package's PARENT to sys.path so `import AutoSQUID` works from inside the AutoSQUID/ folder,
# and the bench-PC RanLabPythonRepo (two levels up, like the other notebooks) is importable.
import sys
sys.path.insert(0, "..")          # .../automation  -> exposes the `AutoSQUID` package
sys.path.append("../../")         # .../SQUID/... root for RanLabPythonRepo (same as the other notebooks)
import AutoSQUID as sq
print("AutoSQUID loaded:", [n for n in sq.__all__[:6]], "...")'''


def build_measurement():
    "Build measurement_cycle.ipynb: §0 Config -> §1 checks -> §2 run sweep -> §3 plots."
    cells = []
    md = lambda s: cells.append(new_markdown_cell(s))
    co = lambda s: cells.append(new_code_cell(s))

    md(r"""# SQUID measurement cycle  *(thin notebook over the `AutoSQUID` package)*

Run on the **bench PC** (real `pyserial` + `nidaqmx` + `RanLabPythonRepo`). All logic lives in `AutoSQUID/`;
this notebook just builds a `Config` (§0 — the cell you edit) and calls `sq.*`.

For each scan interval it collects **`n_trials` clean traces** within **`max_attempts`** acquisitions,
order-indexing files (`DAQ_…_{i}.txt`, failed `…_{i}_{JUMP|SURGE|RAIL|BADBASE}.txt`), resuming from disk,
and stopping the whole sweep if a reset won't hold. Run cells in order; §1 is read-only, **§2 is ACTIVE**.""")

    md("## §0 — Config  *(the cell you edit)*")
    co(BOOT + "\n\n" + r'''cfg = sq.Config(
    # --- acquisition ---
    scan_interval_s = [100e-6],        # one cycle per entry; e.g. [4e-6, 20e-6, 100e-6, 500e-6]
    n_points        = 10_000_000,      # length of the consecutive run
    temp_label      = "auto",          # "auto" -> read+round MXC temp; or a literal e.g. "14mK"
    n_trials        = 2,               # target CLEAN traces per scan interval
    # --- live jump check ---
    chunk           = 100_000,         # jump-check cadence (does NOT break the consecutive run)
    jump_v          = 0.5,             # baseline-mean slip (V) flagged as a JUMP
    baseline_chunks = 1,
    # --- temperature ---
    temp_every_s    = 30.0,
    temp_channel    = 6,               # MXC mixing-chamber thermometer
    # --- control (SCC serial) ---
    port            = "COM3",          # PCS102DA may stay open on another port to hold S-lock
    # --- DAQ / output ---
    vrange          = 1.0,             # NI AI range (V); +/-1 V matches previous measurements
    max_attempts    = 4,               # max total acquisitions per interval (clean + failed)
    user            = "Shannon",
    date            = "",              # date subfolder, e.g. "Jun-01-2026"
)
cfg.outdir.mkdir(parents=True, exist_ok=True)
print(f"intervals {[f'{t*1e6:g}us' for t in cfg.scan_intervals]} · {cfg.n_trials} clean each "
      f"(<= {cfg.max_attempts} acq) · {cfg.n_points} pts ({cfg.npts_tag}) · out -> {cfg.outdir}")''')

    md("""## §1 — Checks  *(read-only)*: pick the live NI input channel + confirm the temperature backend""")
    co(r'''dev, cfg.daq_ai = sq.detect_ai_channel(cfg)     # auto-pick the PCIe-6320 live ai0 (honors cfg.force_*)
print(f"device {dev.name} ({dev.product_type}) · DAQ_AI = {cfg.daq_ai} · control PORT = {cfg.port}")
try:
    T = sq.read_temp(cfg)
    print(f"MXC temperature: {T:.4f} K  (label {sq.format_temp_label(T)})")
except Exception as e:
    print(f"temperature read FAILED ({e}) — logging running? channel {cfg.temp_channel}?")''')

    md("""## §2 — Run the sweep  *(ACTIVE — sends resets, acquires `n_points`, writes files)*

Resolves the temperature label, then runs one cycle per scan interval (stops the whole sweep on a reset
failure). Every acquisition + reset is appended to `experiment_log.txt`; traces + `_temp.csv` go to `outdir`.""")
    co(r'''import datetime
cfg.outdir.mkdir(parents=True, exist_ok=True)
print(f"temperature label for this run: {sq.resolve_temp_label(cfg)}")
print(f"measurement START : {datetime.datetime.now():%Y-%m-%d %H:%M:%S}")
for tau in cfg.scan_intervals:                       # one cycle per scan interval
    if sq.run_cycle(cfg, tau) == "reset_fail":       # reset won't hold (systemic) -> stop the whole sweep
        print("\n*** STOPPED: reset is not working — no more intervals attempted. "
              "Fix it (port/register/lock — see the protocol §G), then re-run §2: it resumes from disk. ***")
        break''')

    md("""## §3 — Plot raw data  *(reads this run's clean traces + temperature back from disk)*""")
    co(r'''sq.plot_run(cfg)
# or a manual list: sq.plot_run(cfg, ["DAQ_4us_15mK_10Mpts_1.txt"])''')

    return cells


def build_auto_s_tune():
    "Build auto_s_tune.ipynb: §0 Config -> §1 lock + null the output to 0."
    cells = []
    md = lambda s: cells.append(new_markdown_cell(s))
    co = lambda s: cells.append(new_code_cell(s))

    md(r"""# Auto S-tune  *(thin notebook over the `AutoSQUID` package)*

**Live-center** the locked input-SQUID output near a target (default 0 mV) by stepping the SQUID-flux DAC —
automating the manual "nudge S-flux until the trace looks centered" procedure (`sq.auto_s_tune`, a secant
search on short live-mean reads). Run on the **bench PC**. No reset between steps (flux shifts the locked
baseline directly).

**Lock the input SQUID first** (`sq.s_lock(cfg)` below, or in PCS102DA) and make sure the trace is roughly
centered (within ±1 V), then run §1.""")

    md("## §0 — Config  *(the cell you edit)*")
    co(BOOT + "\n\n" + r'''cfg = sq.Config(
    port    = "COM3",     # SCC control port
    channel = 1,          # locked PFL channel
    daq_ai  = "Dev1/ai0", # NI analog-in carrying the locked output
    vrange  = 1.0,        # +/-1 V AI range (start must be on-scale)
)
print(f"control PORT={cfg.port} ch={cfg.channel} · DAQ_AI={cfg.daq_ai} · vrange=+/-{cfg.vrange} V")''')

    md("""## §1 — Lock + auto S-tune  *(ACTIVE — sends to the hardware)*

Close PCS102DA on this port first. `auto_s_tune` returns a dict: `status` ∈
{converged, no_response, max_iter}, plus `flux_sflux` / `mean_V` / `std_V` / `n_iter`.
Tunables: `tol_V` (centered band, default 3 mV), `read_s` (live-view length), `start_sflux`, `max_step_uA`.""")
    co(r'''sq.s_lock(cfg)                                   # lock the input SQUID (or do it in PCS102DA)
res = sq.auto_s_tune(cfg, start_sflux=50.0, target_V=0.0, tol_V=0.003)
print(res)
if res["status"] != "converged":
    print("NOT centered — nudge S-flux manually closer / check lock, then re-run.")''')

    return cells


def write(cells, path):
    "AST-check every code cell, then write the notebook."
    for c in cells:
        if c.cell_type == "code":
            ast.parse(c.source)
    nb = new_notebook(cells=cells, metadata={"kernelspec": {"name": "python3", "display_name": "Python 3"},
                                             "language_info": {"name": "python"}})
    with open(path, "w") as f:
        nbf.write(nb, f)
    print("wrote", path, "·", len(cells), "cells")


if __name__ == "__main__":
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    write(build_measurement(), os.path.join(here, "measurement_cycle.ipynb"))
    write(build_auto_s_tune(), os.path.join(here, "auto_s_tune.ipynb"))
