"""NI DAQ reads: finite reads, channel auto-detect, and the consecutive chunked acquisition with the
live jump/surge check. Hardware: nidaqmx. Every read is a FINITE task (start -> read N -> close); the
chunked acquisition drains one finite N-sample task gap-free (so chunking never breaks the run).
"""
import warnings

import numpy as np
import nidaqmx
from nidaqmx.constants import AcquisitionType, TerminalConfiguration
from nidaqmx.stream_readers import AnalogSingleChannelReader

from .analysis import chunk_jump


def daq_task(cfg, channel, fs, n):
    "Create + configure a FINITE single-channel AI task on `channel` at fs Hz for n samples (caller closes)."
    t = nidaqmx.Task()
    t.ai_channels.add_ai_voltage_chan(channel, min_val=-cfg.vrange, max_val=cfg.vrange,
                                      terminal_config=TerminalConfiguration.RSE)
    t.timing.cfg_samp_clk_timing(fs, sample_mode=AcquisitionType.FINITE, samps_per_chan=n)
    return t


def daq_read(cfg, channel, n, fs):
    "Finite read of n samples at fs Hz from one AI channel; returns a 1-D array (V)."
    with daq_task(cfg, channel, fs, n) as t:
        return np.asarray(t.read(number_of_samples_per_channel=n, timeout=n / fs + 5.0))


def daq_mean(cfg, channel=None, n=2000, fs=10_000):
    "Mean voltage of a short read of the locked SQUID output (V) — the auto-S-tune DC read."
    return float(np.mean(daq_read(cfg, channel or cfg.daq_ai, n, fs)))


def live_mean(cfg, channel=None, seconds=1.0, fs=10_000):
    """A short 'live view' of the locked output (what the eye watches while centering): one finite read of
    `seconds`, returned as dict(mean, std, min, max)."""
    v = daq_read(cfg, channel or cfg.daq_ai, int(round(seconds * fs)), fs)
    return {"mean": float(v.mean()), "std": float(v.std()),
            "min": float(v.min()), "max": float(v.max())}


def classify(v):
    "Label an AI-channel probe as live / dead-flat / railed."
    mx, sd, mu = np.max(np.abs(v)), v.std(), v.mean()
    if mx > 9.9 or abs(mu) > 9.5: return "RAILED"
    if sd < 5e-5:                 return "dead/flat"
    return "live"


def detect_ai_channel(cfg):
    "List NI devices, pick the PCIe-6320, probe each AI channel, and return (device_name, daq_ai). Honors cfg.force_*."
    sysl = nidaqmx.system.System.local()
    devices = list(sysl.devices)
    assert devices, "No NI-DAQmx devices found. Is the PCIe-6320 installed and is this the bench PC?"
    dev = (sysl.devices[cfg.force_device] if cfg.force_device
           else next((d for d in devices if "6320" in (d.product_type or "")), devices[0]))
    chans = [c.name for c in dev.ai_physical_chans]
    if cfg.force_ai:
        return dev, cfg.force_ai
    live = []
    for ch in chans:
        try:
            if classify(daq_read(cfg, ch, cfg.scan_n, cfg.scan_fs)) == "live":
                live.append(ch)
        except Exception:
            pass
    daq_ai = next((ch for ch in live if ch.endswith("ai0")), live[0] if live else chans[0])
    return dev, daq_ai


def acquire_finite_chunked(cfg, channel, n, fs):
    """ONE consecutive FINITE read of n samples, drained in cfg.chunk-point reads with the live chunk_jump
    check (the sample clock is gap-free, so chunking does NOT break the run). Returns
    (v[:got], got, flag|None) where flag=dict(kind,index,time_s,reason), kind in {jump,rail,bad_baseline}."""
    buf = np.empty(n, dtype=np.float64)
    got = 0; mu0 = None; base = []
    with daq_task(cfg, channel, fs, n) as t:
        reader = AnalogSingleChannelReader(t.in_stream)
        t.start()
        while got < n:
            m = min(cfg.chunk, n - got)
            reader.read_many_sample(buf[got:got + m], number_of_samples_per_channel=m, timeout=m / fs + 10.0)
            seg = buf[got:got + m]
            res = chunk_jump(float(seg.mean()), float(np.max(np.abs(seg))), mu0, cfg.jump_v, cfg.rail_v, cfg.baseline_v)
            if mu0 is None:
                base.append(float(seg.mean()))
                if len(base) >= cfg.baseline_chunks:
                    mu0 = float(np.mean(base))
            got += m
            if res:
                kind, reason = res
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", message=".*200010.*")
                    t.stop()
                return buf[:got].copy(), got, {
                    "kind": kind, 
                    "index": got, 
                    "time_s": got / fs, 
                    "reason": reason}
    return buf[:got].copy(), got, None
