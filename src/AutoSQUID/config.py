"""Config: every knob for the SQUID measurement-cycle / auto-S-tune stack, in one dataclass.

The notebook constructs a Config (that is where you EDIT values); the library functions all take it as
their first argument. Derived values (scan-interval list, filename tag, output dir, PFL register) are
computed properties, so there is no stale state to keep in sync.
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Union
from datetime import datetime
import os

from .scc import pfl_register


@dataclass
class Config:
    # ---- acquisition ----
    scan_interval_s: Union[float, List[float]] = field(default_factory=lambda: [100e-6])  # one cycle per entry
    n_points: int = 10_000_000          # length of the consecutive run
    temp_label: str = "auto"            # "auto" -> read+round the current MXC temperature; or a literal e.g. "14mK"
    n_trials: int = 2                   # target number of CLEAN traces per scan interval

    # ---- live jump check ----
    chunk: int = 100_000                # points per read = jump-check cadence (does NOT break the consecutive run)
    jump_v: float = 0.5                 # flag a baseline-mean slip >= this (V)
    rail_v: float = 9.5                 # true-rail catch (V); range-agnostic
    baseline_chunks: int = 1            # first chunk(s) set the baseline mean mu0

    # ---- temperature logging ----
    temp_every_s: float = 30.0          # MXC sample period (background thread)
    temp_channel: int = 6               # thermometer channel passed to temp_reader
    temp_reader: Optional[Callable[[int], float]] = None   # LAB-SPECIFIC: set in the notebook to fn(channel)->T in K
                                        # (e.g. cfg.temp_reader = lambda ch: read_latest_temp(ch)[1]); NOT imported by the package

    # ---- control (SCC serial) ----
    port: str = "COM3"                  # control COM port (PCS102DA may stay open on another port to hold S-lock)
    channel: int = 1                    # SCC address = locked PFL channel
    reset_opcode: int = 0x50
    baud: int = 9600
    register_override: Optional[int] = None   # None -> standard SQUID-locked register (0x408)

    # ---- verify / DAQ / output ----
    verify_n: int = 1000                # post-reset verify read
    verify_fs: int = 10_000
    scan_n: int = 4000                  # per-channel liveliness probe
    scan_fs: int = 50_000
    baseline_v: float = 0.10            # |mean| below this = cleared / locked baseline
    vrange: float = 1.0                 # NI AI range (V); +/-1 V matches the previous measurements
    max_attempts: int = 4               # max total acquisitions per interval (clean + failed) before moving on
    force_device: Optional[str] = None  # e.g. 'Dev1' to skip device auto-pick
    force_ai: Optional[str] = None      # e.g. 'Dev1/ai0' to skip channel auto-pick
    daq_ai: str = ""            # NI analog-in carrying the SQUID output (set by detect_ai_channel)
    data_root: str = ""   # data root; outdir = data_root / user / date (set per install)
    user: str = ""               # data folder owner
    date: str = ""                      # date subfolder ("" = the USER folder itself)

    # ---- derived ----
    @property
    def scan_intervals(self) -> List[float]:
        "Scan interval(s) normalized to a list (accepts a scalar)."
        s = self.scan_interval_s
        return [s] if isinstance(s, (int, float)) else list(s)

    @property
    def npts_tag(self) -> str:
        "Filename point-count tag, e.g. 10_000_000 -> '10Mpts'."
        n = self.n_points
        return f"{n // 1_000_000}Mpts" if n % 1_000_000 == 0 else f"{n}pts"

    @property
    def base_path(self) -> str:
        "Per-user data folder: data_root / user."
        return os.path.join(self.data_root, self.user)

    @property
    def outdir(self) -> Path:
        "Folder where traces, temp CSVs, and the ledger are written/read (base_path + date)."
        return Path(os.path.join(self.base_path, self.date))

    @property
    def register(self) -> int:
        "The PFL feedback register to use (override, else standard SQUID-locked 0x408)."
        return self.register_override if self.register_override is not None else pfl_register(stage1_locked=True)

    def core_name(self, scan_interval_s: float) -> str:
        "Date-agnostic identifying core, e.g. '100us_14mK_10Mpts' — matches this (interval, temp, npts) trace on ANY date."
        return f"{int(round(scan_interval_s * 1e6))}us_{self.temp_label}_{self.npts_tag}"

    def base_name(self, scan_interval_s: float) -> str:
        "WRITE stem for a new trace, e.g. 'DAQ_Jun01_100us_14mK_10Mpts' (date prefix + core; order index/outcome appended later). Existing traces are MATCHED by core_name, which ignores the date."
        return f"DAQ_{datetime.now().strftime('%b%d')}_{self.core_name(scan_interval_s)}"


def require_fields(cfg, names, context):
    "Raise if any of `names` is unset (None or '') on cfg — call at the top of a function that needs them."
    missing = [n for n in names if getattr(cfg, n) is None or getattr(cfg, n) == ""]
    if missing:
        raise RuntimeError(f"{context}: set cfg.{', cfg.'.join(missing)} before running")
