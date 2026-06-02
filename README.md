# `AutoSQUID` — SQUID measurement-cycle + auto-S-tune

Small, focused Python package driven by two thin notebooks. **All settings live in the notebook** as one
`Config` object; all logic lives in the modules here. Runs on the **bench PC** (`nidaqmx` + `pyserial` +
`RanLabPythonRepo` installed).

> **Full reference:** `measurement_cycle_protocol.pdf` (this folder) — the configuration table, the
> PCIe-6320 DAQ and PFL-102/SCC details, the reset protocol, the run loop, the operating procedure, and the
> auto-S-tune procedure. This README is the orientation + quick-start; the protocol is the bench reference.

## What it does

For one fixed temperature, and for each scan interval, collect a target number of clean, gap-free
time-series runs at the chosen sample rate. It watches for a flux-lock jump while acquiring, resets the FLL
and re-acquires on a failure, logs the MXC temperature throughout, and saves each trace in the standard
`PCS102` text format with an append-only run ledger. A companion notebook centers the locked output near
0 mV before the run (auto-S-tune).

## Layout

| module | role | deps |
|---|---|---|
| `util.py` | generic helpers (`clamp`) | — |
| `config.py` | `Config` dataclass — every knob + derived paths / filename stem / PFL register | — |
| `scc.py` | SCC framing: `assemble_command`, `pfl_register`, `dac_data` (OpenSQUID-derived) | — |
| `analysis.py` | detection + I/O: `is_surge_spec`, `chunk_jump`, `format_temp_label`, PCS102 read/write, ledger, `scan_indices` | numpy, pandas |
| `serial_io.py` | SCC writes: `reset`, `fire_reset`, `s_lock`/`s_tune`, `set_squid_flux`/`_bias`, `set_array_bias` | pyserial |
| `daq.py` | NI reads: `daq_read`, `daq_mean`, `live_mean`, `classify`, `detect_ai_channel`, `acquire_finite_chunked` | nidaqmx |
| `temperature.py` | MXC `read_temp` + background `TempLogger` | RanLabPythonRepo |
| `measurement.py` | state machine: `run_cycle` (one interval), `reset_and_verify`, `resolve_temp_label`; the interval loop lives in the notebook | — |
| `tuning.py` | `auto_s_tune` — live-center the locked output near 0 by stepping S-flux (secant search) | — |
| `plotting.py` | `plot_run` — raw trace + temperature, read back from disk | matplotlib, pandas |

`util`/`config`/`scc`/`analysis` are hardware-free; the rest import the instrument backends at module top
(so `import AutoSQUID` runs on the bench PC). Dependency order: `util` → `scc`/`config` → `analysis` →
`serial_io`/`daq`/`temperature` → `measurement`/`tuning`/`plotting`.

## Notebooks (in this folder)

- **`measurement_cycle.ipynb`** — §0 build `Config` → §1 read-only checks (`detect_ai_channel` + a
  temperature read) → §2 the interval loop (`sq.run_cycle(cfg, tau)` per interval) → §3 `sq.plot_run(cfg)`.
- **`auto_s_tune.ipynb`** — §0 build `Config` → §1 `sq.s_lock(cfg)` then `sq.auto_s_tune(cfg)`.

Each prepends `..` to `sys.path` so `import AutoSQUID` resolves from inside the folder, and `../../` for
`RanLabPythonRepo`. Regenerate both notebooks from source: `python _build_notebooks.py`.

## Quick start (bench PC)

```python
import AutoSQUID as sq
cfg = sq.Config(scan_interval_s=[100e-6], n_trials=2, port="COM3", user="Shannon", date="Jun-01-2026")
dev, cfg.daq_ai = sq.detect_ai_channel(cfg)          # §1 pick the live ai0
print(sq.read_temp(cfg))                             # confirm the MXC backend
for tau in cfg.scan_intervals:                       # §2 acquire (lock the FLL first!)
    if sq.run_cycle(cfg, tau) == "reset_fail":
        break
sq.plot_run(cfg)                                     # §3 plot from disk
```

First run on the hardware: do the one-time bring-up in the protocol (§ Operating procedure) as a supervised
dry run — do not "Run All" blind.

## Behavior (per scan interval)

Collect `n_trials` CLEAN traces within `max_attempts` real acquisitions. Each acquisition is order-indexed
`i`: clean → `DAQ_{tau}us_{label}_{npts}_{i}.txt` (+ `_{i}_temp.csv`), failed →
`…_{i}_{JUMP|SURGE|RAIL|BADBASE}.txt`. `i` continues past existing files, so re-running **resumes / tops up
and never overwrites**. A reset fires only after a failed acquisition, never between clean traces; a
non-clearing reset — or a bad baseline at the start (the reset didn't hold) — is systemic and **stops the
whole sweep**. Every acquisition + reset is appended to `experiment_log.txt`.

`vrange = ±1 V` (matches previous measurements): finer ADC resolution, but the ADC clips ~1 V, so `jump_v`
(default 0.5 V) catches an in-range slip and `rail_v` (9.5 V) is dormant until the card returns to ±10 V.

## Auto-S-tune

`auto_s_tune` automates the manual "nudge S-flux until the trace is centered at 0 mV": with the SQUID
**locked**, it steps the SQUID-flux DAC and reads a short finite live-mean each step, using a secant update
(`dx = (target − mean)/slope`) so the sign and gain are measured, not assumed — no reset between steps.
Stops when the live mean is within `tol_V` (default 3 mV) of target, or at one DAC LSB. Returns
`dict(status ∈ {converged, no_response, max_iter}, flux_sflux, mean_V, std_V, n_iter)`. At ±1 V the start must
already be on-scale, or the clipped read returns `no_response` — center roughly by hand first, then run it.
See the protocol's auto-S-tune section for the full procedure.
