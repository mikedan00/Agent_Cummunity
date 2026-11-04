import pytest

import db
from users import UserStore


def test_login_creates_once_and_refreshes_email():
    users = UserStore()
    a = users.login("https://accounts.google.com", "sub-1", "kim@x.com")
    b = users.login("https://accounts.google.com", "sub-1", "kim@new.com")
    assert a.id == b.id and b.email == "kim@new.com" and b.nickname is None
    assert users.login("https://accounts.google.com", "sub-2", "lee@x.com").id != a.id


def test_admin_emails_get_admin_role():
    users = UserStore(admin_emails={" Boss@X.com "})
    assert users.login("g", "1", "boss@x.com").is_admin
    assert not users.login("g", "2", "kim@x.com").is_admin


def test_nickname_rules():
    users = UserStore()
    kim, lee = users.login("g", "1", "kim@x"), users.login("g", "2", "lee@x")
    for bad in ["a", "공백 있음", "x" * 21, "AI", "admin", "semi;colon"]:
        with pytest.raises(ValueError):
            users.set_nickname(kim.id, bad)
    with pytest.raises(ValueError, match="사용할 수 없는"):
        users.set_nickname(kim.id, "critic", taken={"Critic"})  # clashes with an agent name
    assert users.set_nickname(kim.id, "철수_1").nickname == "철수_1"
    with pytest.raises(ValueError, match="한 번만"):
        users.set_nickname(kim.id, "다른이름")
    with pytest.raises(ValueError, match="이미 쓰고"):
        users.set_nickname(lee.id, "철수_1")
    with pytest.raises(ValueError, match="이미 쓰고"):
        users.set_nickname(lee.id, "철수_1".upper())  # case-insensitive


def test_ban():
    users = UserStore()
    kim = users.login("g", "1", "kim@x")
    users.set_banned(kim.id, True)
    assert users.get(kim.id).banned
    users.set_banned(kim.id, False)
    assert not users.get(kim.id).banned


def test_messages_and_posts_record_their_author_account():
    engine = db.make_engine("sqlite://")
    from chat_store import ChatStore
    from forum_store import ForumStore
    kim = UserStore(engine).login("g", "1", "kim@x")
    assert ChatStore(engine=engine).add_message("lobby", "kim", "hi", user_id=kim.id).user_id == kim.id
    assert ForumStore(engine=engine).create_post("T", "B", "kim", user_id=kim.id).user_id == kim.id
