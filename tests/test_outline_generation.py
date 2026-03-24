import pytest
from app.services.generation.common import _looks_like_meta_text, is_effective_title, is_outline_content_valid


class TestLooksLikeMetaText:
    def test_catches_auto_generated(self):
        assert _looks_like_meta_text("auto-generated outline") is True

    def test_catches_auto_generated_out(self):
        assert _looks_like_meta_text("auto-generated out") is True

    def test_catches_placeholder(self):
        assert _looks_like_meta_text("placeholder") is True

    def test_catches_todo(self):
        assert _looks_like_meta_text("TODO: fill in") is True

    def test_catches_tbd(self):
        assert _looks_like_meta_text("TBD") is True

    def test_allows_real_title(self):
        assert _looks_like_meta_text("铁幕之下") is False

    def test_allows_english_real_title(self):
        assert _looks_like_meta_text("The Storm Breaks") is False

    def test_catches_fill_in(self):
        assert _looks_like_meta_text("fill in the content") is True

    def test_catches_insert_here(self):
        assert _looks_like_meta_text("insert here") is True

    def test_empty_string(self):
        assert _looks_like_meta_text("") is False


class TestIsEffectiveTitle:
    def test_rejects_auto_generated_out(self):
        assert is_effective_title("第1章：auto-generated out", 1) is False

    def test_rejects_bare_chapter_num(self):
        assert is_effective_title("第5章", 5) is False

    def test_accepts_real_title(self):
        assert is_effective_title("第1章：铁幕之下", 1) is True

    def test_rejects_chapter_with_placeholder_suffix(self):
        assert is_effective_title("第1章：auto-generated", 1) is False


class TestOutlineContentValid:
    def test_empty_outline_is_invalid(self):
        item = {"chapter_num": 1, "title": "第1章", "outline": ""}
        assert is_outline_content_valid(item) is False

    def test_auto_generated_outline_is_invalid(self):
        item = {"chapter_num": 1, "title": "第1章", "outline": "auto-generated outline"}
        assert is_outline_content_valid(item) is False

    def test_valid_outline(self):
        item = {"chapter_num": 1, "title": "第1章：铁幕之下", "outline": "主角在审讯室中发现线索"}
        assert is_outline_content_valid(item) is True

    def test_outline_too_short_is_invalid(self):
        item = {"chapter_num": 1, "title": "第1章", "outline": "推进"}
        assert is_outline_content_valid(item) is False

    def test_none_outline_is_invalid(self):
        item = {"chapter_num": 1, "title": "第1章", "outline": None}
        assert is_outline_content_valid(item) is False
