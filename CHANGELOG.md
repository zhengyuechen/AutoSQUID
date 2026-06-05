# Changelog

All notable changes to **AutoSQUID** are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project uses
[semantic versioning](https://semver.org/) (`MAJOR.MINOR.PATCH`).

## 0.1.3 — 2026-06-05

### Added

* Added adjusting array flux (`set_array_flux`).
* Changed naming of the temperature file to `TEMP_{MonDD}_<core>_{i}.csv`.
* Logged change in s-flux; changed default `tol_V` in `auto_s_tune` to 0.020 (20 mV).
* **Packaging — installable src layout.** Added `pyproject.toml` (src layout, `src/AutoSQUID/`) so the package is `pip install -e .`-able; moved notebooks → `examples/`, the protocol PDF + wiring diagram → `docs/`; added a **GPLv3** `LICENSE` (the `scc.py` SCC framing is ported from OpenSQUID, GPLv3).

## [0.1.2][0.1.2] — 2026-06-03

### Added

- **Action log** (`analysis.log_action` + `run_cycle`): a results-free `action_log.txt` written alongside `experiment_log.txt`, recording the operational event stream — `RESET`/`RESET_FAIL`, the MXC `TEMP` read taken just before each measurement, the `MEASURE` attempt (name + index, never the trace data), and `BAD_BASELINE`. Keeps the numeric ledger separate from the activity record.
- **Use-site config validation** (`config.require_fields`): the functions that need a field check it right before use and raise early, rather than validating in the constructor — `run_cycle` requires `data_root`/`user`/`daq_ai` (plus `temp_reader` when `temp_label="auto"`), `auto_s_tune` requires `daq_ai`/`port`, and `plot_run` requires `data_root`/`user`. `Config()` itself stays passive.
- **`example_measurement_cycle.ipynb`**: a generic measurement-cycle showcase notebook (blank `temp_reader` for you to plug in your lab's reader) is the committed acquisition notebook; the lab-specific `measurement_cycle.ipynb` is now local / gitignored.

### Changed

- **Filenames now carry the acquisition date, but matching ignores it.** `base_name` is `DAQ_<MonDD>_<core>` (e.g. `DAQ_Jun01_100us_14mK_10Mpts`); the new `core_name` gives the date-agnostic `<interval>_<temp>_<npts>` core. `scan_indices` and `clean_trace_names` now match by that core regardless of the date token, so traces taken on **any day** within a folder all count toward the clean total and the next order index. `scan_indices` signature changed `(outdir, base)` → `(outdir, core)`.
- **The order index / logged `attempt` continues across manual stops.** `attempt` is now the on-disk order index `i` (largest existing index for that core + 1), so stopping a run partway and resuming continues the numbering instead of restarting a per-run counter.
- **Portability — lab-specific config separated from the assumed rig.** The MXC temperature backend is no longer imported by the package: `temperature.py` reads through an injectable `cfg.temp_reader` (`fn(channel) -> T in K`, set in the notebook), so `import AutoSQUID` needs `nidaqmx` + `pyserial` (plus `numpy`/`pandas`/`matplotlib`), not the lab thermometer library. New `Config` fields `data_root` (→ `base_path = data_root/user`) and `temp_reader`; lab-specific defaults (`data_root`, `user`, `daq_ai`) are empty and set per install. The STAR Cryo + NI + Bluefors rig is still assumed (SCC framing, PFL register/DAC ranges, PCS102 format unchanged).
- **Clean-trace integrity gate widened** (`analysis.is_surge_spec`): besides rail and >6σ baseline jumps, it now flags flat/dead traces and stuck/frozen segments via a per-chunk standard-deviation collapse (a chunk whose std falls below the live noise level), so a `CLEAN` label excludes frozen runs.
- **`daq.detect_ai_channel` now raises** when no live analog-input channel is found, instead of silently falling back to the first channel.
- **Auto-S-tune reports `dac_limited` separately from `converged`** (`tuning.auto_s_tune`): when the next S-flux step is below one DAC LSB while the mean is still outside `tol_V`, the status is `dac_limited` (as centered as the DAC allows) rather than a false `converged`.
- **`plotting.plot_psd` drops the DC (`f=0`) bin** on the log-log axis (invalid on a log scale); the `clean_only=True` integrity gating is unchanged.

### Fixed

- **`run_cycle` setup**: creates `cfg.outdir` before any reset/write, so the initial reset or a save can't fail on a missing folder; the `BUDGET_EXHAUSTED` ledger row no longer records a misleading next-attempt index (`attempt=""`).
- **NI early-stop warning**: the `nidaqmx` `200010` warning is suppressed only around the expected early `task.stop()` after a failed live check (not globally), so genuine warnings still surface.

### Docs

- **`daq.acquire_finite_chunked`**: comment + NI references explaining why draining the finite acquisition in chunked `read_many_sample` calls doesn't interrupt the single gap-free run (the on-board FIFO holds only a few thousand samples) — NI buffer KB article + PCIe-6320 spec.
- **Protocol PDF**: renamed to `AutoSQUID_protocol.pdf`, added the SQUID/PFL-102/PCI-1000/DAC-2568/PCIe-6320 wiring diagram, and rewrote the run-order section (§5.3) as a bench-facing checklist; the configuration table now matches `config.py`. README and protocol references point to `example_measurement_cycle.ipynb` and `AutoSQUID_protocol.pdf`.

## [0.1.1][0.1.1] — 2026-06-02

### Added

- **`plotting.plot_psd(path, filename, conversion=1, P=…)`**: one-sided Welch PSD (Φ₀²/Hz) of one trace —
  splits it into `P` mean-removed, windowed segments and averages them, overlaying each `P` on one log-log
  axis (normalization verified against `scipy.signal.welch` to ~1e-16). A `clean_only` toggle runs the
  `is_surge_spec` integrity gate and skips the trace on failure; `conversion` is the per-cooldown Φ₀/V factor.
- **`plotting.plot_overlay(t, v, temp_t, temp_T)`**: overlay a voltage trace and a continuous MXC-temperature
  line on one shared time axis (twin y-axes), interpolating the 30 s temperature samples onto the trace grid.
- **Notebook**: `data_analysis.ipynb` — off-line raw-trace / temperature-overlay / PSD analysis over the package.

### Changed

- **`auto_s_tune`**: renamed the S-flux tunables/return key for clarity — `start_uA`→`start_sflux` and the
  result key `flux_uA`→`flux_sflux`; default `start_sflux` lowered 50.0 → 10.0 µA.

## [0.1.0][0.1.0] — 2026-06-01

Initial release: the SQUID measurement-cycle + auto-S-tune package, extracted from the earlier
single-notebook scripts into a small library (`Config` dataclass + focused modules) driven by two thin
notebooks, with the operating-protocol PDF.

### Added

- **Package** (`AutoSQUID/`): `config` (the `Config` dataclass — all knobs + derived paths/register),
  `util` (`clamp`), `scc` (SCC framing), `analysis` (surge/jump detection, temperature label, PCS102
  read/write, ledger, resume), `serial_io` (SCC writes), `daq` (NI reads + channel detect + chunked
  acquisition), `temperature` (MXC read + background logger), `measurement` (`run_cycle` state machine),
  `tuning` (`auto_s_tune`), `plotting` (`plot_run`).
- **Notebooks**: `measurement_cycle.ipynb` (acquisition) and `auto_s_tune.ipynb` (centering).
- **Measurement cycle**: collects `n_trials` clean, gap-free `n_points` traces per scan interval within
  `max_attempts` acquisitions; order-indexed filenames with `_JUMP`/`_SURGE`/`_RAIL`/`_BADBASE` suffixes;
  resume/top-up from disk without overwriting; per-acquisition `experiment_log.txt` ledger; MXC temperature
  logged every 30 s; stops the whole sweep on a non-clearing reset or bad-baseline start.
- **Auto-S-tune**: live-centers the locked output near 0 mV by stepping the SQUID-flux DAC (secant search
  on short finite reads; no reset between steps), returning a status dict.
- **Docs**: `README.md` and the compiled `measurement_cycle_protocol.pdf` (configuration, the PCIe-6320 DAQ
  and PFL-102/SCC reference, the reset protocol, the run loop, and the operating + auto-S-tune procedures).

### Notes

- Runs on the bench PC (`nidaqmx`, `pyserial`, + your thermometer backend). `vrange = ±1 V` (matches previous
  measurements). Defaults are bench-specific (`port="COM3"`, `user=""`, the `F:\…` data root) — set
  them per run.

[0.1.2]: https://github.com/zhengyuechen/AutoSQUID/releases/tag/v0.1.2
[0.1.1]: https://github.com/zhengyuechen/AutoSQUID/releases/tag/v0.1.1
[0.1.0]: https://github.com/zhengyuechen/AutoSQUID/releases/tag/v0.1.0
