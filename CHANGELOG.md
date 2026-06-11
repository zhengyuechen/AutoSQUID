# Changelog

All notable changes to **AutoSQUID** are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project uses
[semantic versioning](https://semver.org/) (`MAJOR.MINOR.PATCH`).

## 0.2.0 — 2026-06-11

### Changed

* **Jumps are truncated at the source, then re-judged — no more `JUMP`/`RAIL` outcomes; detection owns the boundary.** When a live `chunk_jump` flag fires, `acquire_finite_chunked` drops the offending chunk and returns the strict **clean pre-jump prefix** `buf[:chunk_start]`; that boundary is final and the locator is *not* called. When an acquisition completes **without** a live flag, the **post-hoc locator** (`usable_points_from_spec`) runs regardless of whether `live_jump_check` was on — so a slip the live check missed, or any run drained with live checking off, is still located. The prefix gets a **post-run surge check** (`is_surge_spec`) and is labelled **`CLEAN`** / **`SURGE`** / **`DEAD`** — a `DEAD` (0-point) prefix is **ledgered but never written to disk**. Filename suffixes are now only nothing, `_SURGE`, or `_BADBASE` (bad-baseline unchanged — systemic, stops the sweep).
* **The post-hoc locator is INDEPENDENT of `is_surge_spec`** (they only share `_chunk_stats`). `usable_points_from_spec` scans every chunk with **absolute** thresholds (`rail_v`, `|mu-mu0| > jump_v`, freeze vs a robust 95th-pct noise level) and cuts at the **onset of the persistent failing run that reaches the end** — walking back from the end and permitting up to `gap_tol` **consecutive** clean chunks (internal default `3`); recovery requires **more than** `gap_tol` consecutive clean chunks, so a *latched* jump/rail/freeze is cut but a *transient that recovers* is kept. It catches a slip the live check missed, and no longer over-truncates a transient. This persistence applies wherever the locator runs (any acquisition that finished without a live flag); when a live flag fired, its boundary is taken as-is with no persistence.
* **Provenance separate from the filename:** the ledger records `event` (the trigger — `JUMP`/`RAIL`/`FROZEN`/`DEAD`/`BAD_BASELINE`/`NONE`, from the live flag or, with live off, the locator's `kind`), `outcome` (integrity of the saved prefix — `SURGE` is reserved for this), and `accepted` (`outcome=CLEAN` **and** `usable_points ≥ min_usable_frac × n_points`, so a clean prefix shorter than that fraction is still ledgered but does **not** count toward `n_trials`). A truncated-but-clean trace is `CLEAN` with e.g. `event=JUMP`, so a slip is never silently lost — including on the no-live-flag path (live-on-but-missed or live-off), where the locator now returns `(usable_points, kind, reason)`.
* **Resume counting is EXACT, from the ledger** (`scan_indices` / `accepted_trace_names`): it counts the trials the ledger marked `accepted` (set from the precise `usable_points` at write time) whose file still **exists**, and takes the next index from the **filesystem** (never overwrites any existing trace). Immune to filename rounding (e.g. a `min_usable_frac=0.905` trace tagged `9p0Mpts` still counts). `clean_trace_names` uses the same accepted list, so plotting-selection matches counting.
* **Filename point-count tag = the trace's USABLE points** (`fmt_npts`, one decimal with `p`, e.g. `8p4Mpts`; whole millions stay `10Mpts`). Generic in `cfg.n_points` (10 M, 20 M, …).
* **`acquire_finite_chunked` returns `buf[:chunk_start]`** on a jump/rail (exact even when the final chunk is short), with `flag = {kind, index, time_s, reason}`.
* **Robustness:** `is_surge_spec` (and the locator) use a **95th-percentile** noise level instead of `max(std)` (a single noisy chunk no longer false-flags "stuck"); both **guard empty input**; and the **exit reset** (when the final accepted prefix left the lock slipped) now runs through `_reset` — counting `n_resets`, logging, and returning `reset_fail` if it doesn't clear.
* **Experiment ledger columns:** added `event`, `outcome`, `usable_points`, `usable_seconds`, and `accepted` — `event` is the trigger/provenance, `outcome` the integrity of the saved prefix, `accepted` whether it counts toward `n_trials`. These replace the old `jump_index`/`jump_time_s`; `n_acquired` removed.

### Added

* **`analysis._chunk_stats`** — the single per-chunk analysis (means/stds/rails/baseline) shared by `is_surge_spec` (whole-trace pass/fail) and `usable_points_from_spec` (where the failure begins).
* **`analysis.usable_points_from_spec(v, …)`** → `(usable_points, kind, reason)` (the clean-prefix locator, run after any acquisition that finished without a live flag); **`analysis.truncate_to_clean(v, fs, **locator_kw)`** → `(clean_v, usable_points, usable_seconds, kind, reason)`.
* **`analysis.accepted_trace_names(outdir, id_core)`** — the ledger-accepted, on-disk trace filenames for a (interval, temp), in index order.
* **`analysis.fmt_npts` / `parse_npts`**; **`Config.min_usable_frac`** (default `0.90`).
* **`plotting.plot_usable(...)`**: plots only the usable (clean pre-failure) part of a trace; **skips `_SURGE` files**. Newly-acquired truncated files already contain *only* their usable prefix, so the discarded-tail overlay is meaningful only for full-length legacy / externally-supplied traces that still hold the post-slip tail.

### Removed

* `Config.report_usable` and the old flag-based `truncate_to_clean(v, fs, chunk, flag)`.

### Fixed

* **`usable_points_from_spec` `gap_tol` off-by-one:** the backward-scan break is now `clean_run > gap_tol` (was `>=`), so `gap_tol` is the number of **consecutive** clean chunks actually tolerated inside a trailing failing run — with `gap_tol=1`, one clean chunk is now tolerated (previously it took `gap_tol=2`).
* **Flat/dead traces now carry DEAD provenance from the locator.** When the locator's robust noise level is below `min_std` (new `min_std=5e-5` parameter, matching `is_surge_spec`), `usable_points_from_spec` returns `(0, "dead", reason)` instead of `(full_length, None, "ok")`. A fully flat trace is therefore labelled `event=DEAD` / `outcome=DEAD` (0-point prefix → not saved), instead of being saved as a `_SURGE` file by the downstream integrity gate.

## 0.1.4 — 2026-06-07

### Added

* **`Config.terminal_config`** (`"RSE"` | `"NRSE"` | `"DIFF"`, default `"DIFF"`): NI analog-input terminal configuration, now applied in `daq.daq_task` (was hard-coded RSE). `DIFF` reads the SQUID output differentially (ai0/ai8) and rejects common-mode pickup; `NRSE` references AI SENSE. Lets the read mode match the wiring without editing library source.
* **`Config.temp_logger`** (bool, default `True`): toggles the background MXC `TempLogger` thread during acquisition. When `False`, `run_cycle` starts no temperature thread and writes no per-trace `TEMP_*.csv`, and a literal `temp_label` then needs no `temp_reader` — for quick acquisition-only runs.
* **`Config.live_jump_check`** (bool, default `True`): toggles the mid-run `chunk_jump`/rail abort inside `daq.acquire_finite_chunked`. When `False`, the single gap-free run drains to completion without the per-chunk check; the post-hoc `is_surge_spec` integrity gate on the saved trace is unchanged.
* **`example_measurement_cycle.ipynb`**: the §0 `Config` cell now exposes `temp_logger` and `live_jump_check`.

### Changed

* **Temperature-logger gating extracted into `temperature.py`** (`temp_logging` context manager + `log_temp_now` / `save_temp_log`): `run_cycle` wraps the acquisition in `with temp_logging(cfg) as tl:` and calls the two helpers — which auto-detect `cfg.temp_logger` — instead of repeated inline `if cfg.temp_logger:` blocks. No behavior change.
* **`run_cycle` slimmed to scannable control flow** (`measurement.py`): the per-attempt body (banner → acquire → classify → save → log → result line) moved into an inner `_attempt(idx)` closure, the end-of-interval status into `_summary()`, and the outcome mapping into a new module-level pure helper `_classify_outcome(cfg, v, flag) -> (outcome, tag, reason)`. The interval loop is now just resume → reset → `_attempt`/`_reset` → `_summary`. No behavior or console-output change.
* **`auto_s_tune` probes 10% *down*, then 10% *up* on no response** (`tuning.auto_s_tune`): the initial secant probe is 10% *below* `start_sflux` (`dx = -0.1·start_sflux`); if that probe comes back flat (e.g. the output is pinned/saturated below the start point), it now retries once 10% *above* `start_sflux` from the same start before returning `no_response`, instead of giving up on the first flat probe. The secant update still self-finds the sign, so convergence on a healthy lock is unchanged.
* **`auto_s_tune` now requires `data_root` and `user`** (`config.require_fields`): the up-front check is `daq_ai`/`port`/`data_root`/`user` (was `daq_ai`/`port`), so the tune always has a valid `action_log.txt` destination and fails fast instead of running without recording where its S-flux result went.

### Fixed

* **`run_cycle` action log** (`measurement._reset` / `reset_and_verify`): the `RESET` detail now records **how many reset tries it took to bring the baseline below `cfg.baseline_v`** (e.g. `2 tries`; `RESET_FAIL` logs `not cleared`), instead of the upcoming trace index — which read like a reset counter but wasn't (a single reset on a resume logged a misleading `attempt 2`). `reset_and_verify` now returns that clearing-try count (`0` if it never cleared within `tries`) rather than a bare bool; truthiness is unchanged (`0` is falsy), so `if reset_and_verify(...)` callers still work. The ledger's `attempt` field (on-disk trace index, consistent with the measurement rows) is unchanged.

### Docs

* Updated the README and operating protocol for differential input configuration,
  optional temperature logging and live checks, reset-try action logging, and the
  revised auto-S-tune requirements/probe sequence.

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
