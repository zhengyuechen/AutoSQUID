"""SCC framing: known-good frames, the odd-parity invariant, the PFL register map, DAC data words."""
import pytest

from AutoSQUID.scc import assemble_command, pfl_register, dac_data


class TestAssembleCommand:
    def test_reset_release_frame(self):
        # channel 1, opcode 0x50, SQUID-locked register: popcount(0x01)+popcount(0x50)+popcount(0x408)=5 (odd) -> parity bit clear
        assert assemble_command(0x01, 0x50, 0x408) == "01500408;"

    def test_reset_assert_frame(self):
        # data 0x409 (reset bit set): popcount=6 (even) -> parity bit set in bit 7 of the opcode (0x50 -> 0xD0)
        assert assemble_command(0x01, 0x50, 0x409) == "01D00409;"

    def test_frame_shape(self):
        fr = assemble_command(0xFF, 0x60, 0x1234)
        assert len(fr) == 9 and fr.endswith(";") and fr == fr.upper()

    @pytest.mark.parametrize("addr,op,data", [
        (0x01, 0x50, 0x408), (0x01, 0x50, 0x409), (0xFF, 0x60, 0x0000),
        (0x01, 0x60, 0xFFF0), (0x02, 0x50, 0x0800), (0x01, 0x60, 0x8001),
    ])
    def test_odd_parity_invariant(self, addr, op, data):
        # total set bits over (address, opcode-with-parity, data) must always be odd,
        # and the frame must carry address/data verbatim with the parity only in opcode bit 7
        fr = assemble_command(addr, op, data)
        a, o, d = int(fr[0:2], 16), int(fr[2:4], 16), int(fr[4:8], 16)
        assert (bin(a).count("1") + bin(o).count("1") + bin(d).count("1")) % 2 == 1
        assert a == addr and d == data and (o & 0x7F) == op


class TestPflRegister:
    def test_standard_squid_locked(self):
        assert pfl_register(stage1_locked=True) == 0x408

    def test_array_locked(self):
        assert pfl_register(stage1_locked=False, stage2_locked=True) == 0x400

    def test_both_tuning(self):
        assert pfl_register(stage1_locked=False, stage2_locked=False) == 0x800

    def test_integrator_and_feedback_added(self):
        assert pfl_register(stage1_locked=True, integrator=0x100, feedback=0x004) == 0x50C

    def test_test_input_only_when_on(self):
        assert pfl_register(stage1_locked=True, test_input=0x040, test_on=False) == 0x408
        assert pfl_register(stage1_locked=True, test_input=0x040, test_on=True) == 0x448


class TestDacData:
    def test_full_scale(self):
        assert dac_data(100.0, 100.0, 0x0) == 0xFFF0   # code 0xFFF in the high bits, sub-opcode in the low nibble

    def test_zero(self):
        assert dac_data(0.0, 100.0, 0x3) == 0x0003

    def test_quarter_scale(self):
        # 25 of 100 -> code round(25*4095/100) = 1024
        assert dac_data(25.0, 100.0, 0x1) == (1024 << 4) | 0x1

    def test_over_limit_clamped(self):
        assert dac_data(150.0, 100.0, 0x2) == dac_data(100.0, 100.0, 0x2)

    def test_negative_clamped_to_zero(self):
        assert dac_data(-5.0, 100.0, 0x0) == 0x0000
