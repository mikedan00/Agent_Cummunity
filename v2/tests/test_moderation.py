import db
from chat_store import ChatStore
from forum_store import ForumStore
from moderation import Moderation
from users import UserStore


def setup():
    engine = db.make_engine("sqlite://")
    users = UserStore(engine)
    chat, forum = ChatStore(engine=engine), ForumStore(engine=engine)
    kim = users.set_nickname(users.login("g", "1", "kim@x").id, "kim")
    lee = users.set_nickname(users.login("g", "2", "lee@x").id, "lee")
    return Moderation(engine, chat, forum, users), chat, forum, users, kim, lee


def test_reports_are_grouped_and_deduplicated():
    mod, chat, forum, users, kim, lee = setup()
    spam = chat.add_message("lobby", "lee", "광고", user_id=lee.id)
    assert mod.report(kim.id, "message", spam.id)
    assert not mod.report(kim.id, "message", spam.id)  # same user, still open
    boss = users.login("g", "3", "boss@x")
    assert mod.report(boss.id, "message", spam.id)
    [entry] = mod.open_reports()
    assert (entry["text"], entry["author"], entry["author_id"], entry["count"]) == ("광고", "lee", lee.id, 2)


def test_hide_removes_content_and_closes_reports():
    mod, chat, forum, users, kim, lee = setup()
    post = forum.create_post("T", "나쁜 글", "lee", user_id=lee.id)
    comment = forum.add_comment(post.id, "lee", "나쁜 댓글", user_id=lee.id)
    mod.report(kim.id, "comment", comment.id)
    mod.report(kim.id, "post", post.id)
    assert {e["target"] for e in mod.open_reports()} == {"post", "comment"}
    mod.hide("comment", comment.id)
    assert forum.comments(post.id) == [] and [e["target"] for e in mod.open_reports()] == ["post"]
    mod.resolve("post", post.id)  # dismiss: content stays
    assert mod.open_reports() == [] and forum.post(post.id)


def test_ban_author_bans_hides_and_skips_ai_content():
    mod, chat, forum, users, kim, lee = setup()
    msg = chat.add_message("lobby", "lee", "욕설", user_id=lee.id)
    assert mod.ban_author("message", msg.id)
    assert users.get(lee.id).banned and chat.messages("lobby") == []
    ai = chat.add_message("lobby", "AI", "답변", is_ai=True)
    assert not mod.ban_author("message", ai.id)
