from warcrawler.config import TorConfig
from warcrawler.tor_manager import TorManager


def test_torrc_quotes_datadirectory_and_uses_forward_slashes(tmp_path):
    data_dir = tmp_path / "space dir"      # path with a space (the Windows failure case)
    data_dir.mkdir()
    tm = TorManager(TorConfig(socks_port=9050, control_port=9051),
                    base_dir=tmp_path, data_dir=data_dir)
    text = tm._torrc().read_text(encoding="utf-8")
    assert 'DataDirectory "' in text                 # value is quoted
    assert "space dir/tor" in text                   # spaced path, forward slashes
    assert "\\" not in text.split("DataDirectory", 1)[1].splitlines()[0]  # no backslashes
    assert "SocksPort 9050" in text and "ControlPort 9051" in text
