from app.api.routes.generation import _format_eta, _smoothed_chapter_seconds


def test_format_eta():
    assert _format_eta(45) == "约45秒"
    assert _format_eta(360) == "约6分钟"
    assert _format_eta(3660) == "约1小时1分钟"


def test_smoothed_chapter_seconds_prefers_recent():
    v1 = _smoothed_chapter_seconds([100, 100, 100])
    v2 = _smoothed_chapter_seconds([100, 100, 300])
    assert v1 > 0
    assert v2 > v1

