"""Tests for normalize_chapter_content covering multiple input formats."""

from app.services.generation.common import normalize_chapter_content


class TestNormalizeChapterContent:
    def test_empty_input(self):
        assert normalize_chapter_content("") == ""
        assert normalize_chapter_content(None) == ""
        assert normalize_chapter_content("   ") == ""

    def test_plain_text_single_newline_paragraphs(self):
        raw = "第一段内容。\n第二段内容。\n第三段内容。"
        result = normalize_chapter_content(raw)
        assert "\n\n" not in result or result.count("\n\n") == 0
        assert "第一段内容。" in result
        assert "第三段内容。" in result

    def test_double_newline_preserved(self):
        raw = "第一段内容。\n\n第二段内容。\n\n第三段内容。"
        result = normalize_chapter_content(raw)
        parts = result.split("\n\n")
        assert len(parts) == 3
        assert parts[0].strip() == "第一段内容。"
        assert parts[1].strip() == "第二段内容。"
        assert parts[2].strip() == "第三段内容。"

    def test_crlf_normalized(self):
        raw = "段落A。\r\n\r\n段落B。\r\n段落C。"
        result = normalize_chapter_content(raw)
        assert "\r" not in result
        assert "段落A。" in result
        assert "段落B。" in result

    def test_escaped_newlines(self):
        raw = "对话一。\\n\\n对话二。\\n对话三。"
        result = normalize_chapter_content(raw)
        assert "\\n" not in result
        assert "对话一。" in result
        assert "对话二。" in result

    def test_html_tags_converted(self):
        raw = "<p>段落一内容。</p><p>段落二内容。</p><br>段落三内容。"
        result = normalize_chapter_content(raw)
        assert "<p>" not in result
        assert "</p>" not in result
        assert "<br>" not in result
        assert "段落一内容。" in result
        assert "段落三内容。" in result

    def test_chapter_body_wrapper_stripped(self):
        raw = "<chapter_body>正文内容第一段。\n\n正文内容第二段。</chapter_body>"
        result = normalize_chapter_content(raw)
        assert "<chapter_body>" not in result
        assert "</chapter_body>" not in result
        assert "正文内容第一段。" in result

    def test_code_fence_stripped(self):
        raw = '```json\n{"chapter_body": "这是正文。"}\n```'
        result = normalize_chapter_content(raw)
        assert "```" not in result

    def test_excessive_blank_lines_compressed(self):
        raw = "段落一。\n\n\n\n\n段落二。\n\n\n\n\n\n段落三。"
        result = normalize_chapter_content(raw)
        assert "\n\n\n" not in result
        parts = result.split("\n\n")
        assert len(parts) == 3

    def test_zero_width_chars_removed(self):
        raw = "\ufeff段落一\u200b内容。\n\n段落二\u200d内容。"
        result = normalize_chapter_content(raw)
        assert "\ufeff" not in result
        assert "\u200b" not in result
        assert "\u200d" not in result
        assert "段落一内容。" in result

    def test_trailing_whitespace_stripped(self):
        raw = "段落一。   \n\n段落二。  \n\n段落三。   "
        result = normalize_chapter_content(raw)
        for line in result.split("\n"):
            assert line == line.rstrip(), f"Trailing whitespace found: {line!r}"

    def test_mixed_format_robustness(self):
        raw = (
            "<chapter_body>"
            "<p>第一段对话。</p>"
            "\\n\\n"
            "第二段描写。\r\n\r\n"
            "```\n第三段行动。\n```"
            "\n\n\n\n"
            "第四段结尾。"
            "</chapter_body>"
        )
        result = normalize_chapter_content(raw)
        assert "<chapter_body>" not in result
        assert "<p>" not in result
        assert "\\n" not in result
        assert "\r" not in result
        assert "```" not in result
        assert "\n\n\n" not in result
        assert "第一段对话。" in result
        assert "第四段结尾。" in result

    def test_idempotent(self):
        raw = "段落一。\n\n段落二。\n\n段落三。"
        first = normalize_chapter_content(raw)
        second = normalize_chapter_content(first)
        assert first == second
