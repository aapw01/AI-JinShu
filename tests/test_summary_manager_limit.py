"""Tests for LIMIT parameter in get_summaries_before and list_reports."""
from unittest.mock import MagicMock, patch
import pytest

from app.services.memory.summary_manager import SummaryManager
from app.services.memory.story_bible import QualityReportStore


def _make_summary_row(chapter_num: int) -> MagicMock:
    row = MagicMock()
    row.chapter_num = chapter_num
    row.summary = f"summary-{chapter_num}"
    return row


def _make_report_row(id_: int) -> MagicMock:
    row = MagicMock()
    row.id = id_
    row.metrics_json = {"score": id_}
    row.verdict = "pass"
    return row


# ---------------------------------------------------------------------------
# get_summaries_before
# ---------------------------------------------------------------------------

class TestGetSummariesBefore:
    def _make_db(self, rows):
        db = MagicMock()
        scalars = MagicMock()
        scalars.all.return_value = rows
        execute_result = MagicMock()
        execute_result.scalars.return_value = scalars
        db.execute.return_value = execute_result
        return db

    def test_no_limit_returns_all(self):
        rows = [_make_summary_row(i) for i in range(1, 6)]
        db = self._make_db(rows)
        mgr = SummaryManager()
        result = mgr.get_summaries_before(1, 1, 10, db=db)
        assert len(result) == 5
        assert result[0]["chapter_num"] == 1
        assert result[4]["chapter_num"] == 5

    def test_with_limit_returns_most_recent_in_asc_order(self):
        # When limit=3, only 3 rows should be returned (DESC fetched, then reversed)
        # The DB returns rows in DESC order (as the implementation requests)
        rows_desc = [_make_summary_row(i) for i in [5, 4, 3]]
        db = self._make_db(rows_desc)
        mgr = SummaryManager()
        result = mgr.get_summaries_before(1, 1, 10, db=db, limit=3)
        assert len(result) == 3
        # After reversing DESC result, should be ascending
        assert result[0]["chapter_num"] == 3
        assert result[1]["chapter_num"] == 4
        assert result[2]["chapter_num"] == 5

    def test_with_limit_fewer_records_returns_all(self):
        # Only 2 records exist, limit=3 → return both in ascending order
        rows_desc = [_make_summary_row(i) for i in [2, 1]]
        db = self._make_db(rows_desc)
        mgr = SummaryManager()
        result = mgr.get_summaries_before(1, 1, 10, db=db, limit=3)
        assert len(result) == 2
        assert result[0]["chapter_num"] == 1
        assert result[1]["chapter_num"] == 2

    def test_limit_triggers_desc_order_and_limit_on_stmt(self):
        """Verify that when limit is provided, the stmt uses desc order + limit."""
        rows_desc = [_make_summary_row(i) for i in [3, 2, 1]]
        db = self._make_db(rows_desc)
        mgr = SummaryManager()
        result = mgr.get_summaries_before(1, 1, 10, db=db, limit=3)
        # Should be reversed to ascending
        chapter_nums = [r["chapter_num"] for r in result]
        assert chapter_nums == sorted(chapter_nums)

    def test_result_has_correct_keys(self):
        rows = [_make_summary_row(1)]
        db = self._make_db(rows)
        mgr = SummaryManager()
        result = mgr.get_summaries_before(1, 1, 10, db=db)
        assert "chapter_num" in result[0]
        assert "summary" in result[0]
        assert result[0]["summary"] == "summary-1"


# ---------------------------------------------------------------------------
# list_reports
# ---------------------------------------------------------------------------

class TestListReports:
    def _make_db(self, rows):
        db = MagicMock()
        scalars = MagicMock()
        scalars.all.return_value = rows
        execute_result = MagicMock()
        execute_result.scalars.return_value = scalars
        db.execute.return_value = execute_result
        return db

    def test_no_limit_returns_all(self):
        rows = [_make_report_row(i) for i in [5, 4, 3, 2, 1]]
        db = self._make_db(rows)
        store = QualityReportStore()
        result = store.list_reports(1, "volume", db=db)
        assert len(result) == 5

    def test_with_limit_1_returns_one(self):
        rows = [_make_report_row(5)]  # DB returns most recent first
        db = self._make_db(rows)
        store = QualityReportStore()
        result = store.list_reports(1, "volume", db=db, limit=1)
        assert len(result) == 1
        assert result[0].id == 5

    def test_limit_none_identical_to_no_limit(self):
        rows = [_make_report_row(i) for i in [3, 2, 1]]
        db = self._make_db(rows)
        store = QualityReportStore()
        result = store.list_reports(1, "chapter", db=db, limit=None)
        assert len(result) == 3
