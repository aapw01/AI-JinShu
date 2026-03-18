from app.services.generation.length_control import (
    build_chapter_length_prompt_kwargs,
    count_content_words,
    maybe_compact_chapter_length,
)


def test_build_chapter_length_prompt_kwargs_defaults():
    kwargs = build_chapter_length_prompt_kwargs()
    assert kwargs["word_count"] == 2600
    assert kwargs["min_word_count"] == 2000
    assert kwargs["target_word_count"] == 2600
    assert kwargs["soft_max_word_count"] == 3000
    assert kwargs["hard_ceiling_word_count"] == 3500
    assert kwargs["ideal_min_word_count"] == 2200
    assert kwargs["ideal_max_word_count"] == 2800


def test_maybe_compact_chapter_length_skips_when_within_soft_max():
    content = "甲" * 2800

    out, diagnostics = maybe_compact_chapter_length(
        content=content,
        compact_fn=lambda *_args: "不应调用",
        normalize_fn=lambda text: text,
    )

    assert out == content
    assert diagnostics["length_compaction_attempted"] is False
    assert diagnostics["word_count_before_compaction"] == 2800
    assert diagnostics["word_count_after_compaction"] == 2800


def test_maybe_compact_chapter_length_applies_shorter_result():
    content = "甲" * 3600

    out, diagnostics = maybe_compact_chapter_length(
        content=content,
        compact_fn=lambda _draft, _feedback: "乙" * 2900,
        normalize_fn=lambda text: text,
    )

    assert count_content_words(out) == 2900
    assert diagnostics["length_compaction_attempted"] is True
    assert diagnostics["length_compaction_applied"] is True
    assert diagnostics["length_compaction_reason"] == "hard_ceiling_exceeded"
    assert diagnostics["word_count_before_compaction"] == 3600
    assert diagnostics["word_count_after_compaction"] == 2900


def test_maybe_compact_chapter_length_falls_back_when_under_min():
    content = "甲" * 3400

    out, diagnostics = maybe_compact_chapter_length(
        content=content,
        compact_fn=lambda _draft, _feedback: "乙" * 1800,
        normalize_fn=lambda text: text,
    )

    assert out == content
    assert diagnostics["length_compaction_attempted"] is True
    assert diagnostics["length_compaction_applied"] is False
    assert diagnostics["length_compaction_reason"] == "compaction_under_min"
    assert diagnostics["word_count_before_compaction"] == 3400
    assert diagnostics["word_count_after_compaction"] == 1800
