"""Config derived properties + the use-site field validation."""
from pathlib import Path

import pytest

from AutoSQUID.config import Config, require_fields


class TestDerived:
    def test_scan_intervals_scalar_normalized(self):
        assert Config(scan_interval_s=100e-6).scan_intervals == [100e-6]

    def test_scan_intervals_list_passthrough(self):
        assert Config(scan_interval_s=[4e-6, 500e-6]).scan_intervals == [4e-6, 500e-6]

    def test_id_core(self):
        cfg = Config(temp_label="14mK")
        assert cfg.id_core(100e-6) == "100us_14mK"
        assert cfg.id_core(4e-6) == "4us_14mK"

    def test_base_name_uses_target_npts(self):
        name = Config(temp_label="14mK").base_name(100e-6)
        assert name.startswith("DAQ_") and name.endswith("_100us_14mK_10Mpts")

    def test_base_name_usable_npts_override(self):
        assert Config(temp_label="14mK").base_name(100e-6, 8_400_000).endswith("_100us_14mK_8p4Mpts")

    def test_outdir(self):
        assert Config(data_root="/a", user="b", date="c").outdir == Path("/a/b/c")
        assert Config(data_root="/a", user="b", date="").outdir == Path("/a/b")

    def test_register_default_squid_locked(self):
        assert Config().register == 0x408

    def test_register_override(self):
        assert Config(register_override=0x40C).register == 0x40C


class TestRequireFields:
    def test_raises_listing_missing(self):
        with pytest.raises(RuntimeError, match="data_root"):
            require_fields(Config(), ["data_root", "user"], "run_cycle")

    def test_none_counts_as_missing(self):
        with pytest.raises(RuntimeError, match="temp_reader"):
            require_fields(Config(), ["temp_reader"], "run_cycle with temp_label='auto'")

    def test_passes_when_set(self):
        cfg = Config(data_root="/a", user="b", daq_ai="Dev1/ai0")
        require_fields(cfg, ["data_root", "user", "daq_ai"], "run_cycle")   # no raise
