import tomllib

import pytest

import write_secrets

ENV = {"AUTH_REDIRECT_URI": "https://me-space.hf.space/oauth2callback", "AUTH_COOKIE_SECRET": 'a"b\\c',
       "AUTH_GOOGLE_CLIENT_ID": "id.apps.googleusercontent.com", "AUTH_GOOGLE_CLIENT_SECRET": "sec"}


def test_renders_valid_toml_for_st_login():
    data = tomllib.loads(write_secrets.render(ENV))
    assert data["auth"]["redirect_uri"] == ENV["AUTH_REDIRECT_URI"]
    assert data["auth"]["cookie_secret"] == 'a"b\\c'  # quotes and backslashes survive
    assert data["auth"]["google"]["client_id"] == ENV["AUTH_GOOGLE_CLIENT_ID"]
    assert data["auth"]["google"]["server_metadata_url"].startswith("https://accounts.google.com/")


def test_nothing_without_client_id_and_error_on_partial_config():
    assert write_secrets.render({}) is None
    with pytest.raises(SystemExit, match="AUTH_COOKIE_SECRET"):
        write_secrets.render({k: v for k, v in ENV.items() if k != "AUTH_COOKIE_SECRET"})


def test_main_writes_file_once(tmp_path, monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    target = tmp_path / ".streamlit" / "secrets.toml"
    write_secrets.main(str(target))
    assert tomllib.loads(target.read_text())["auth"]["google"]["client_secret"] == "sec"
    target.write_text("# edited")
    write_secrets.main(str(target))
    assert target.read_text() == "# edited"  # an existing file is kept
