"""Config: every knob for the SQUID measurement-cycle / auto-S-tune stack, in one dataclass.

The notebook constructs a Config (that is where you EDIT values); the library functions all take it as
their first argument. Derived values (scan-interval list, filename tag, output dir, PFL register) are
computed properties, so there is no stale state to keep in sync.
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Union

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
    temp_channel: int = 6               # MXC mixing-chamber thermometer (read_latest_temp channel)

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
    daq_ai: str = "Dev1/ai0"            # NI analog-in carrying the SQUID output (set by detect_ai_channel)
    user: str = "Shannon"               # data folder owner
    date: str = ""                      # date subfolder ("" = the USER folder itself)

    # ---- environment ----
    ranlab_path: str = "../../"         # sys.path entry where RanLabPythonRepo lives (for the temperature backend)

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
        "Lab data root for this user (Windows path), matching the other notebooks."
        return f"F:\\Dilution Refrigerator Data\\{self.user}\\"

    @property
    def outdir(self) -> Path:
        "Folder where traces, temp CSVs, and the ledger are written/read (base_path + date)."
        return Path(self.base_path + self.date)

    @property
    def register(self) -> int:
        "The PFL feedback register to use (override, else standard SQUID-locked 0x408)."
        return self.register_override if self.register_override is not None else pfl_register(stage1_locked=True)

    def base_name(self, scan_interval_s: float) -> str:
        "Filename stem for one interval, e.g. 'DAQ_100us_14mK_10Mpts' (the order index/outcome is appended later)."
        return f"DAQ_{int(round(scan_interval_s * 1e6))}us_{self.temp_label}_{self.npts_tag}"
