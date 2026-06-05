"""MXC temperature: a thin wrapper over a lab-supplied reader + a background logger thread.

The package imports NO thermometer backend. Set `cfg.temp_reader` in the notebook to a callable
`fn(channel) -> T in K` (each lab supplies its own, wrapping its thermometer API),
so `import AutoSQUID` works without any instrument library and the temperature source is swappable per lab.
"""
import time
import threading


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
