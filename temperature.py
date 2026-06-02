"""Bluefors MXC temperature: the read backend (RanLabPythonRepo) + a background logger thread.

Runs on the bench PC where RanLabPythonRepo is importable (the notebook adds its parent to sys.path
before importing AutoSQUID). Same backend the data-analysis notebooks use.
"""
import time
import threading
from RanLabPythonRepo.python_instruments_measure.Instruments.BF_Therm_and_Heater.BF_TestFns import read_latest_temp


def read_temp(cfg):
    "MXC mixing-chamber temperature in K (read_latest_temp channel cfg.temp_channel; data-analysis convention)."
    return read_latest_temp(cfg.temp_channel)[1]


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
