"""MXC temperature: a thin wrapper over a lab-supplied reader + a background logger thread.

The package imports NO thermometer backend. Set `cfg.temp_reader` in the notebook to a callable
`fn(channel) -> T in K` (each lab supplies its own, wrapping its thermometer API),
so `import AutoSQUID` works without any instrument library and the temperature source is swappable per lab.
"""
import time
import threading
from contextlib import contextmanager

from .analysis import save_temp_csv


def read_temp(cfg):
    "MXC temperature in K via the lab-supplied cfg.temp_reader(cfg.temp_channel); errors if it is not set."
    if cfg.temp_reader is None:
        raise RuntimeError("cfg.temp_reader is not set — assign a fn(channel)->T(K) in the notebook, "
                           "e.g. cfg.temp_reader = lambda ch: read_latest_temp(ch)[1]")
    return cfg.temp_reader(cfg.temp_channel)


class TempLogger(threading.Thread):
    "Background thread: sample read_temp(cfg) every cfg.temp_every_s into (t_rel_s, T_K) until stop()."
    def __init__(self, cfg):
        "Set the sampling period from cfg and prepare the stop flag + samples list."
        super().__init__(daemon=True)
        self.cfg = cfg
        self.every_s = cfg.temp_every_s
        self._stop_event = threading.Event()
        self.samples = []

    def run(self):
        "Append (t_rel_s, T_K) every every_s s until stopped; record nan on a read error."
        t0 = time.time()
        while not self._stop_event.is_set():
            try:
                T = float(read_temp(self.cfg))
            except Exception:
                T = float("nan")
            self.samples.append((time.time() - t0, T))
            self._stop_event.wait(self.every_s)

    def stop(self):
        "Signal the sampling loop to stop."
        self._stop_event.set()


@contextmanager
def temp_logging(cfg):
    """Run a background MXC TempLogger for the enclosed block IF cfg.temp_logger, else a no-op.

    Yields the TempLogger (its `.samples` is [] when logging is off), so run_cycle can wrap the
    acquisition in `with temp_logging(cfg) as tl:` instead of repeating the `if cfg.temp_logger:`
    start/stop dance inline.
    """
    tl = TempLogger(cfg)
    if cfg.temp_logger:
        tl.start()
    try:
        yield tl
    finally:
        if cfg.temp_logger:
            tl.stop()
            tl.join(timeout=cfg.temp_every_s + 5)


def log_temp_now(cfg, log_action):
    "Append a one-shot MXC TEMP line via log_action(action, detail); no-op when cfg.temp_logger is off."
    if not cfg.temp_logger:
        return
    try:
        T = read_temp(cfg)
        log_action("TEMP", f"{T:.4f} K" if T is not None else "read failed (None)")
    except Exception as e:
        log_action("TEMP", f"read failed ({e})")


def save_temp_log(cfg, path, samples):
    "Write the temperature CSV to `path`; no-op when cfg.temp_logger is off."
    if cfg.temp_logger:
        save_temp_csv(path, samples)
