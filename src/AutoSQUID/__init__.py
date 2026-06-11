"""AutoSQUID — shared library for the SQUID measurement-cycle + auto-S-tune notebooks.

Edit values in the notebook (build a Config); call these functions with that cfg. Modules:
  util        generic helpers (clamp)
  config      Config dataclass (all knobs + derived paths/register)
  scc         pure SCC framing (assemble_command, pfl_register, dac_data)
  analysis    pure detection + I/O (surge/jump, temp label, PCS102 read/write, ledger, resume)
  serial_io   SCC writes (reset, s_lock/s_tune, set_* DACs)        [pyserial]
  daq         NI reads + channel detect + chunked acquisition       [nidaqmx]
  temperature MXC read via the injected `cfg.temp_reader` + background logger   [no instrument import]
  measurement run_cycle state machine (the interval loop lives in the notebook)
  tuning      auto_s_tune (live-center the locked output near 0 by stepping S-flux; secant search)
  plotting    plot_run / plot_psd (raw trace + temperature; one-sided Welch PSD, read from disk) [matplotlib/pandas]

The DAQ + serial backends (`nidaqmx`, `pyserial`) are imported at module top (the package assumes the
STAR Cryo + NI rig), so `import AutoSQUID` needs those two. The MXC thermometer backend is **not** imported —
set `cfg.temp_reader` (a `fn(channel)->T in K`) in the notebook, so the temperature source is lab-swappable.
The pure modules (`scc`, `analysis`, `config`) have no hardware deps and import anywhere for unit tests.
"""
__version__ = "0.2.0"

from .config import Config
from .util import clamp
from .scc import assemble_command, pfl_register, dac_data
from .analysis import (is_surge_spec, chunk_jump, truncate_to_clean, usable_points_from_spec,
                       fmt_npts, parse_npts, format_temp_label,
                       save_pcs102, save_temp_csv, read_daq_file,
                       log_experiment, log_action, scan_indices, accepted_trace_names, LEDGER_COLS)
from .serial_io import (reset, fire_reset, s_lock, s_tune,
                        set_squid_flux, set_array_flux, set_squid_bias, set_array_bias)
from .daq import daq_read, daq_mean, live_mean, classify, detect_ai_channel, acquire_finite_chunked
from .temperature import read_temp, TempLogger, temp_logging, log_temp_now, save_temp_log
from .measurement import run_cycle, reset_and_verify, resolve_temp_label
from .tuning import auto_s_tune
from .plotting import plot_run, clean_trace_names, plot_overlay, plot_psd, plot_usable

__all__ = [
    "__version__",
    "Config",
    "assemble_command", "pfl_register", "dac_data", "clamp",
    "is_surge_spec", "chunk_jump", "truncate_to_clean", "usable_points_from_spec", "fmt_npts", "parse_npts", "format_temp_label",
    "save_pcs102", "save_temp_csv", "read_daq_file", "log_experiment", "log_action", "scan_indices", "accepted_trace_names", "LEDGER_COLS",
    "reset", "fire_reset", "s_lock", "s_tune", "set_squid_flux", "set_array_flux", "set_squid_bias", "set_array_bias",
    "daq_read", "daq_mean", "live_mean", "classify", "detect_ai_channel", "acquire_finite_chunked",
    "read_temp", "TempLogger", "temp_logging", "log_temp_now", "save_temp_log",
    "run_cycle", "reset_and_verify", "resolve_temp_label",
    "auto_s_tune", "plot_run", "clean_trace_names", "plot_overlay", "plot_psd", "plot_usable",
]
