"""SCC serial protocol framing for the STAR Cryoelectronics PFL-102 / PCI-1000.

Pure functions (no hardware) — VERBATIM from the OpenSQUID-0.2 GPLv3 source
(StarCryoSquidInterface.assembleCommand, PFL102._computePflRegister). Importable and unit-testable
anywhere; the actual serial writes live in serial_io.py.

assemble_command / pfl_register / dac_data are derived from OpenSQUID-0.2,
Copyright (C) 2012 Felix Jaeckel and Randy Lafler, licensed GPLv3 — so AutoSQUID
is distributed under GPLv3 (see LICENSE at the repo root).
"""
from .util import clamp


def assemble_command(address, opcode, data):
    """SCC ASCII frame: 2-hex address, 2-hex opcode (odd-parity bit in bit 7), 4-hex data, ';'."""
    n = 0
    for x in (address, opcode, data):
        while x:
            n += x & 1
            x >>= 1
    parity = (n + 1) % 2
    opcode = opcode + parity * 0x80
    return "%02X%02X%04X;" % (address, opcode, data)


def pfl_register(stage1_locked=True, stage2_locked=False, integrator=0x0000,
                 feedback=0x0000, test_input=0x0080, test_on=False):
    """PFL-102 0x50 feedback register; standard config (SQUID-locked, 100kOhm, 1.5nF, test off) -> 0x408."""
    data = test_input if test_on else 0
    if stage1_locked:    data += 0x408
    elif stage2_locked:  data += 0x400
    else:                data += 0x800
    return data + integrator + feedback


def dac_data(value, value_max, sub_opcode, code0=0x000, code_max=0xFFF):
    """0x60 DAC data word: 12-bit code in the high bits, DAC sub-opcode in the low nibble (SCC Table III).
    `value` is clamped to [0, value_max] first, so an over-limit set command is capped (assumes a zero-based range)."""
    value = clamp(value, 0.0, value_max)
    code = int(round(value * (code_max - code0) / value_max)) + code0
    code = max(0, min(0xFFF, code))
    return (code << 4) | sub_opcode
