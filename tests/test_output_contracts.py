import pytest

from app.services.generation.contracts import OutputContractError, validate_chapter_body


def test_validate_chapter_body_accepts_clean_content():
    body = "热电厂B4控制室里，灯管闪了两下。林晚把指尖按在回车键上。"
    out = validate_chapter_body(
        body,
        stage="writer",
        chapter_num=1,
        provider="openai",
        model="m",
        min_chars=10,
    )
    assert out == body


def test_validate_chapter_body_rejects_empty():
    with pytest.raises(OutputContractError) as exc_info:
        validate_chapter_body(
            "",
            stage="writer",
            chapter_num=1,
            provider="openai",
            model="m",
            min_chars=10,
        )
    assert exc_info.value.code == "MODEL_OUTPUT_SCHEMA_INVALID"


def test_validate_chapter_body_rejects_contamination():
    raw = "以下是根据反馈全面打磨后的第9章修订版。\n重点解决如下问题：\n正文第一句。"
    with pytest.raises(OutputContractError) as exc_info:
        validate_chapter_body(
            raw,
            stage="finalizer",
            chapter_num=9,
            provider="openai",
            model="m",
            min_chars=10,
        )
    assert exc_info.value.code == "MODEL_OUTPUT_POLICY_VIOLATION"


def test_validate_chapter_body_rejects_too_short():
    with pytest.raises(OutputContractError) as exc_info:
        validate_chapter_body(
            "很短",
            stage="rewrite",
            chapter_num=3,
            provider="openai",
            model="m",
            min_chars=20,
        )
    assert exc_info.value.code == "MODEL_OUTPUT_POLICY_VIOLATION"
