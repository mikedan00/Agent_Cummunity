"""With TEST_DATABASE_URL set (a throwaway PostgreSQL database), every store in a test
shares that database, emptied before each test. Otherwise stores use in-memory SQLite."""

import os

import pytest

import db

PG_URL = os.environ.get("TEST_DATABASE_URL")

if PG_URL:
    _make_engine = db.make_engine

    @pytest.fixture(autouse=True)
    def postgres_db(monkeypatch):
        engine = _make_engine(PG_URL)
        db.metadata.drop_all(engine)
        db.metadata.create_all(engine)
        monkeypatch.setattr(db, "make_engine", lambda url=None: engine)
        yield engine
        engine.dispose()
