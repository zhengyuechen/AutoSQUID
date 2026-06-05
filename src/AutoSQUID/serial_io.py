"""SCC serial writes to the PFL-102 (reset, lock/tune, DAC sets). Hardware: pyserial.

Each call opens the control port, writes the ASCII SCC frame(s), and closes — except fire_reset, which
writes on a port the caller already holds open (used inside reset_and_verify's verify loop).
"""
import time
import serial

from .scc import assemble_command, pfl_register, dac_data


def _send(cfg, *frames, gap_s=0.002):
    "Open the control port, write each SCC frame (ascii), close."
    with serial.Serial(cfg.port, cfg.baud, bytesize=8, parity="N", stopbits=1, timeout=1) as ser:
        for fr in frames:
            ser.write(fr.encode("ascii")); time.sleep(gap_s)


def fire_reset(ser, cfg):
    "Write the reset frame pair on an OPEN serial port: assert bit 0, then release (caller holds the port)."
    reg = cfg.register
    ser.write(assemble_command(cfg.channel, cfg.reset_opcode, reg | 0x01).encode("ascii")); time.sleep(0.002)
    ser.write(assemble_command(cfg.channel, cfg.reset_opcode, reg).encode("ascii"))


def reset(cfg):
    "Reset the FLL (open port, assert bit 0, release, close)."
    reg = cfg.register
    _send(cfg, assemble_command(cfg.channel, cfg.reset_opcode, reg | 0x01),
               assemble_command(cfg.channel, cfg.reset_opcode, reg))


def s_lock(cfg):
    "S-LOCK: lock the input SQUID (stage 1) — write the SQUID-locked feedback register (0x408)."
    _send(cfg, assemble_command(cfg.channel, 0x50, pfl_register(stage1_locked=True)))


def s_tune(cfg):
    "S-TUNE: open the input-SQUID feedback loop — write the both-tuning register (0x800)."
    _send(cfg, assemble_command(cfg.channel, 0x50, pfl_register(stage1_locked=False, stage2_locked=False)))


def set_squid_flux(cfg, uA):
    "Set the SQUID flux (stage-1 offset current), 0-100 uA  (DAC 0x60, sub 0x0)."
    _send(cfg, assemble_command(cfg.channel, 0x60, dac_data(uA, 100.0, 0x00)))


def set_array_flux(cfg, uA):
    "Set the array flux (stage-2 offset current), 0-100 uA  (DAC 0x60, sub 0x1)."
    _send(cfg, assemble_command(cfg.channel, 0x60, dac_data(uA, 100.0, 0x01)))


def set_squid_bias(cfg, mA):
    "Set the SQUID bias current, 0-2 mA  (DAC 0x60, sub 0x2)."
    _send(cfg, assemble_command(cfg.channel, 0x60, dac_data(mA, 2.0, 0x02)))


def set_array_bias(cfg, uA):
    "Set the array bias current, 0-100 uA  (DAC 0x60, sub 0x3)."
    _send(cfg, assemble_command(cfg.channel, 0x60, dac_data(uA, 100.0, 0x03)))
