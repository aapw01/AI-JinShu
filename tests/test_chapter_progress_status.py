import json

from app.api.routes.chapters import _get_generating_chapter_from_redis


class _DummyRedis:
    def __init__(self, payload):
        self.payload = payload

    def get(self, key: str):
        if self.payload is None:
            return None
        return json.dumps(self.payload).encode("utf-8")


def test_get_generating_chapter_from_redis_running(monkeypatch):
    monkeypatch.setattr(
        "app.api.routes.chapters.redis.from_url",
        lambda *_: _DummyRedis({"status": "running", "current_chapter": 12}),
    )
    assert _get_generating_chapter_from_redis(1) == 12


def test_get_generating_chapter_from_redis_non_running(monkeypatch):
    monkeypatch.setattr(
        "app.api.routes.chapters.redis.from_url",
        lambda *_: _DummyRedis({"status": "completed", "current_chapter": 12}),
    )
    assert _get_generating_chapter_from_redis(1) is None

