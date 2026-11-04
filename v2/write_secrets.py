"""Write .streamlit/secrets.toml's [auth] section from environment variables, then exit.

st.login reads its settings only from secrets.toml, while HF Spaces passes secrets
as environment variables. The Docker entrypoint runs this before starting Streamlit.
Nothing is written unless AUTH_GOOGLE_CLIENT_ID is set.
"""

import json
import os
import sys
from pathlib import Path

GOOGLE_METADATA = "https://accounts.google.com/.well-known/openid-configuration"
REQUIRED = ["AUTH_REDIRECT_URI", "AUTH_COOKIE_SECRET", "AUTH_GOOGLE_CLIENT_ID", "AUTH_GOOGLE_CLIENT_SECRET"]


def toml_str(value: str) -> str:
    return json.dumps(value)  # a JSON string is a valid TOML basic string


def render(env: dict) -> str | None:
    if not env.get("AUTH_GOOGLE_CLIENT_ID"):
        return None
    missing = [name for name in REQUIRED if not env.get(name)]
    if missing:
        raise SystemExit(f"Google 로그인 설정에 빠진 값이 있습니다: {', '.join(missing)}")
    return "\n".join([
        "[auth]",
        f"redirect_uri = {toml_str(env['AUTH_REDIRECT_URI'])}",
        f"cookie_secret = {toml_str(env['AUTH_COOKIE_SECRET'])}",
        "",
        "[auth.google]",
        f"client_id = {toml_str(env['AUTH_GOOGLE_CLIENT_ID'])}",
        f"client_secret = {toml_str(env['AUTH_GOOGLE_CLIENT_SECRET'])}",
        f"server_metadata_url = {toml_str(GOOGLE_METADATA)}",
        "",
    ])


def main(path: str = ".streamlit/secrets.toml") -> None:
    text = render(dict(os.environ))
    if text is None:
        print("AUTH_GOOGLE_CLIENT_ID 미설정: Google 로그인 없이 시작합니다 (읽기 전용).")
        return
    target = Path(path)
    if target.exists():
        print(f"{path}이(가) 이미 있어 덮어쓰지 않습니다.")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    target.chmod(0o600)
    print(f"{path}에 Google 로그인 설정을 썼습니다.")


if __name__ == "__main__":
    main(*sys.argv[1:])
