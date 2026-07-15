from warcrawler.config import LoginConfig
from warcrawler.login import extract_token

PAGE = """<html><body>
<form method=post action=/login>
  <input type="hidden" name="csrf_token" value="TOK-12345">
  <input name="username"><input name="password" type="password">
</form></body></html>"""


def test_extract_token_by_field_name():
    assert extract_token(PAGE, LoginConfig(csrf_field="csrf_token")) == "TOK-12345"


def test_extract_token_by_regex():
    cfg = LoginConfig(csrf_regex=r'name="csrf_token"\s+value="([^"]+)"')
    assert extract_token(PAGE, cfg) == "TOK-12345"


def test_extract_token_absent_returns_none():
    assert extract_token("<html><body>no token</body></html>",
                         LoginConfig(csrf_field="csrf_token")) is None
