"""Summary manager - chapter summaries for context."""
import re
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.novel import ChapterSummary

# Volume-brief 关键句抽取的触发词。这些词覆盖玄幻 / 都市 / 言情常见的"事件级"
# 转折点，命中后该句优先放进卷摘要，避免被简单字符截断切掉。
# 维度：身份变化 / 重大伤害 / 关系变化 / 关键道具 / 情节反转 / 死亡/失踪 / 决定。
_VOLUME_BRIEF_KEYWORDS: tuple[str, ...] = (
    "决定", "决意", "决战", "反转", "揭示", "揭穿", "揭开", "暴露", "突破", "晋升",
    "登基", "继承", "拜师", "结盟", "联手", "背叛", "出走", "投靠", "投降", "归顺",
    "重伤", "断臂", "断腿", "失明", "中毒", "中伏", "陷入昏迷", "苏醒",
    "死亡", "身亡", "战死", "被杀", "失踪", "走失", "复活", "重生",
    "出现", "现身", "出场", "登场", "退场", "离开", "归来", "返回",
    "得到", "拿到", "失去", "夺回", "夺走", "捡到", "争夺",
    "签订", "达成", "立下", "约定", "缔结",
    "怀孕", "成婚", "退婚", "和离", "结识", "相认",
)
_VOLUME_BRIEF_SENTENCE_SPLITTER = re.compile(r"(?<=[。！？!?；;])")


def _split_into_sentences(text: str) -> list[str]:
    """按中文/英文句末标点切分，保留标点附在前一句末尾。"""
    if not text:
        return []
    parts = [seg.strip() for seg in _VOLUME_BRIEF_SENTENCE_SPLITTER.split(text)]
    return [seg for seg in parts if seg]


def _is_key_sentence(sentence: str) -> bool:
    return any(keyword in sentence for keyword in _VOLUME_BRIEF_KEYWORDS)


def _build_volume_brief_text(items: list[tuple[int, str]], chars_per_volume: int) -> str:
    """关键句优先 + 章首句兜底 + 普通句补齐，再按 chars_per_volume 截断。

    与旧版"chars_per_volume 之前 join 全部摘要再截前 N 字"相比，这里能保证
    每章至少留下首句作为锚点，并优先把"反转 / 死亡 / 决定 / 关键道具变更"等
    事件级句子保留下来，避免长篇小说卷头摘要里关键事件被无声切掉。
    """
    if not items or chars_per_volume <= 0:
        return ""

    # 按章节切分句子并打标
    chapter_sentences: list[tuple[int, list[tuple[str, str]]]] = []
    for chapter_num, summary in items:
        sentences = _split_into_sentences(summary or "")
        if not sentences:
            continue
        tagged: list[tuple[str, str]] = []
        for index, sentence in enumerate(sentences):
            if index == 0:
                tag = "anchor"
            elif _is_key_sentence(sentence):
                tag = "key"
            else:
                tag = "filler"
            tagged.append((tag, sentence))
        chapter_sentences.append((chapter_num, tagged))

    if not chapter_sentences:
        return ""

    # 选择策略：先把所有 anchor + key 全收，再用 filler 补到预算上限。
    selected: list[tuple[int, str]] = []
    leftover: list[tuple[int, str]] = []
    for chapter_num, tagged in chapter_sentences:
        for tag, sentence in tagged:
            if tag in ("anchor", "key"):
                selected.append((chapter_num, sentence))
            else:
                leftover.append((chapter_num, sentence))

    def _format(parts: list[tuple[int, str]]) -> str:
        return " ".join(f"ch{chapter_num}:{sentence}" for chapter_num, sentence in parts)

    text = _format(selected)
    if len(text) > chars_per_volume:
        return text[:chars_per_volume] + "..."

    for chapter_num, sentence in leftover:
        candidate = _format(selected + [(chapter_num, sentence)])
        if len(candidate) > chars_per_volume:
            text = text[:chars_per_volume] + "..." if len(text) > chars_per_volume else text
            break
        selected.append((chapter_num, sentence))
        text = candidate

    return text


class SummaryManager:
    """Manage chapter summaries."""

    def get_summaries_before(
        self,
        novel_id: int,
        novel_version_id: int,
        before_chapter: int,
        db: Optional[Session] = None,
        limit: int | None = None,
    ) -> list[dict]:
        """Get summaries of chapters before given number.

        When *limit* is provided, only the *limit* most recent chapters are
        returned (fetched DESC then reversed to preserve chronological order).
        """
        should_close = db is None
        db = db or SessionLocal()
        try:
            stmt = select(ChapterSummary).where(
                ChapterSummary.novel_id == novel_id,
                ChapterSummary.novel_version_id == novel_version_id,
                ChapterSummary.chapter_num < before_chapter,
            )
            if limit is not None:
                stmt = stmt.order_by(ChapterSummary.chapter_num.desc()).limit(limit)
                rows = list(reversed(db.execute(stmt).scalars().all()))
            else:
                stmt = stmt.order_by(ChapterSummary.chapter_num)
                rows = db.execute(stmt).scalars().all()
            return [{"chapter_num": r.chapter_num, "summary": r.summary} for r in rows]
        finally:
            if should_close:
                db.close()

    def get_volume_brief(
        self,
        novel_id: int,
        novel_version_id: int,
        chapter_num: int,
        volume_size: int = 30,
        chars_per_volume: int = 400,
        db: Optional[Session] = None,
    ) -> str:
        """Compress older chapter summaries into volume-level briefs.

        For chapters older than (chapter_num - 5), group them into volumes of volume_size
        and return a compressed summary string.
        """
        cutoff = max(1, chapter_num - 5)
        should_close = db is None
        db = db or SessionLocal()
        try:
            stmt = (
                select(ChapterSummary)
                .where(
                    ChapterSummary.novel_id == novel_id,
                    ChapterSummary.novel_version_id == novel_version_id,
                    ChapterSummary.chapter_num < cutoff,
                )
                .order_by(ChapterSummary.chapter_num)
            )
            rows = db.execute(stmt).scalars().all()
            if not rows:
                return ""

            by_volume: dict[int, list[tuple[int, str]]] = {}
            for r in rows:
                vol_idx = (r.chapter_num - 1) // volume_size
                if vol_idx not in by_volume:
                    by_volume[vol_idx] = []
                by_volume[vol_idx].append((r.chapter_num, r.summary or ""))

            parts = []
            for vol_idx in sorted(by_volume.keys()):
                items = by_volume[vol_idx]
                start_ch, end_ch = items[0][0], items[-1][0]
                brief = _build_volume_brief_text(items, chars_per_volume)
                if not brief:
                    # Fallback: 当所有摘要都是空字符串等极端情况时，保留旧行为，
                    # 避免某些卷条目从 brief 里整卷消失。
                    combined = " ".join(s for _, s in items)
                    brief = combined[:chars_per_volume] + ("..." if len(combined) > chars_per_volume else "")
                parts.append(f"【卷{vol_idx + 1} ({start_ch}-{end_ch})】{brief}")
            return " ".join(parts)
        finally:
            if should_close:
                db.close()

    def add_summary(
        self, novel_id: int, novel_version_id: int, chapter_num: int, summary: str, db: Optional[Session] = None
    ):
        """Add or update chapter summary."""
        should_close = db is None
        db = db or SessionLocal()
        try:
            stmt = select(ChapterSummary).where(
                ChapterSummary.novel_id == novel_id,
                ChapterSummary.novel_version_id == novel_version_id,
                ChapterSummary.chapter_num == chapter_num,
            )
            existing = db.execute(stmt).scalar_one_or_none()
            if existing:
                existing.summary = summary
            else:
                try:
                    with db.begin_nested():
                        db.add(
                            ChapterSummary(
                                novel_id=novel_id,
                                novel_version_id=novel_version_id,
                                chapter_num=chapter_num,
                                summary=summary,
                            )
                        )
                        db.flush()
                except IntegrityError:
                    existing = db.execute(stmt).scalar_one_or_none()
                    if existing:
                        existing.summary = summary
                    else:
                        raise
            if should_close:
                db.commit()
            else:
                db.flush()
        except Exception:
            if should_close:
                db.rollback()
            raise
        finally:
            if should_close:
                db.close()
