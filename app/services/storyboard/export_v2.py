"""Storyboard V2 export generation and signed-download helpers."""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.storyboard import StoryboardCharacterCard, StoryboardShot, StoryboardVersion
from app.services.storyboard.exporter import export_shots_to_csv


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def artifact_root() -> Path:
    root = Path("tmp/storyboard_exports")
    root.mkdir(parents=True, exist_ok=True)
    return root


def make_download_signature(export_id: str, expires_ts: int) -> str:
    secret = (get_settings().auth_jwt_secret or "").encode("utf-8")
    payload = f"{export_id}:{expires_ts}".encode("utf-8")
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def verify_download_signature(export_id: str, expires_ts: int, signature: str) -> bool:
    if int(expires_ts) < int(_utc_now().timestamp()):
        return False
    expected = make_download_signature(export_id, expires_ts)
    return hmac.compare_digest(expected, str(signature or ""))


def build_export_download_url(*, project_id: int, export_id: str, expires_seconds: int = 600) -> str:
    expires_ts = int(_utc_now().timestamp()) + max(60, int(expires_seconds))
    sig = make_download_signature(export_id, expires_ts)
    return f"/api/storyboards/{project_id}/exports/{export_id}/download?expires={expires_ts}&sig={sig}"


def _minimal_pdf_bytes(lines: list[str]) -> bytes:
    # Minimal PDF fallback (Latin-1 only) used when reportlab is unavailable.
    safe = [str(x).encode("latin-1", errors="replace").decode("latin-1") for x in lines]
    text = "\\n".join([f"({line[:120].replace('(', '[').replace(')', ']')}) Tj" for line in safe[:120]])
    stream = f"BT /F1 10 Tf 40 780 Td 12 TL {text} ET"
    pdf = (
        "%PDF-1.4\n"
        "1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n"
        "2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n"
        "3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>endobj\n"
        "4 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n"
        f"5 0 obj<< /Length {len(stream)} >>stream\n{stream}\nendstream endobj\n"
        "xref\n0 6\n0000000000 65535 f \n"
        "0000000010 00000 n \n"
        "0000000060 00000 n \n"
        "0000000117 00000 n \n"
        "0000000244 00000 n \n"
        "0000000314 00000 n \n"
        "trailer<< /Root 1 0 R /Size 6 >>\nstartxref\n430\n%%EOF\n"
    )
    return pdf.encode("latin-1", errors="replace")


def _render_pdf_bytes(payload: dict[str, Any]) -> bytes:
    lines = [
        "Storyboard Version Report",
        f"Project #{payload.get('storyboard_project_id')}",
        f"Version #{payload.get('version_no')} ({payload.get('lane')})",
        f"Shots: {len(payload.get('shots') or [])}",
        f"Characters: {len(payload.get('character_cards') or [])}",
        "",
    ]
    for row in (payload.get("shots") or [])[:120]:
        lines.append(
            f"E{row.get('episode_no')}-S{row.get('scene_no')}-#{row.get('shot_no')} "
            f"{str(row.get('action') or '')[:80]}"
        )
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfbase import cidfonts
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfgen import canvas
        from io import BytesIO

        buf = BytesIO()
        c = canvas.Canvas(buf, pagesize=A4)
        pdfmetrics.registerFont(cidfonts.UnicodeCIDFont("STSong-Light"))
        c.setFont("STSong-Light", 10)
        x = 36
        y = 812
        for line in lines:
            c.drawString(x, y, line[:120])
            y -= 14
            if y < 40:
                c.showPage()
                c.setFont("STSong-Light", 10)
                y = 812
        c.save()
        return buf.getvalue()
    except Exception:
        return _minimal_pdf_bytes(lines)


def _export_json_payload(version: StoryboardVersion, shots: list[StoryboardShot], cards: list[StoryboardCharacterCard]) -> dict[str, Any]:
    return {
        "storyboard_project_id": int(version.storyboard_project_id),
        "storyboard_version_id": int(version.id),
        "version_no": int(version.version_no),
        "lane": str(version.lane),
        "source_novel_version_id": int(version.source_novel_version_id) if version.source_novel_version_id else None,
        "quality_report_json": version.quality_report_json if isinstance(version.quality_report_json, dict) else {},
        "shots": [
            {
                "episode_no": int(s.episode_no),
                "scene_no": int(s.scene_no),
                "shot_no": int(s.shot_no),
                "location": s.location,
                "time_of_day": s.time_of_day,
                "shot_size": s.shot_size,
                "camera_angle": s.camera_angle,
                "camera_move": s.camera_move,
                "duration_sec": int(s.duration_sec or 0),
                "characters_json": s.characters_json or [],
                "action": s.action,
                "dialogue": s.dialogue,
                "emotion_beat": s.emotion_beat,
                "transition": s.transition,
                "sound_hint": s.sound_hint,
                "production_note": s.production_note,
                "blocking": s.blocking,
                "motivation": s.motivation,
                "performance_note": s.performance_note,
                "continuity_anchor": s.continuity_anchor,
            }
            for s in shots
        ],
        "character_cards": [
            {
                "character_key": row.character_key,
                "display_name": row.display_name,
                "skin_tone": row.skin_tone,
                "ethnicity": row.ethnicity,
                "master_prompt_text": row.master_prompt_text,
                "negative_prompt_text": row.negative_prompt_text,
                "style_tags_json": row.style_tags_json or [],
                "consistency_anchors_json": row.consistency_anchors_json or [],
                "quality_score": row.quality_score,
            }
            for row in cards
        ],
    }


def render_export_blob(
    db: Session,
    *,
    version_id: int,
    export_format: str,
) -> tuple[bytes, str, str]:
    version = db.execute(select(StoryboardVersion).where(StoryboardVersion.id == version_id)).scalar_one_or_none()
    if not version:
        raise ValueError("storyboard_version_not_found")
    shots = db.execute(
        select(StoryboardShot)
        .where(StoryboardShot.storyboard_version_id == version_id)
        .order_by(StoryboardShot.episode_no.asc(), StoryboardShot.scene_no.asc(), StoryboardShot.shot_no.asc())
    ).scalars().all()
    cards = db.execute(
        select(StoryboardCharacterCard)
        .where(StoryboardCharacterCard.storyboard_version_id == version_id)
        .order_by(StoryboardCharacterCard.display_name.asc())
    ).scalars().all()
    if export_format == "csv":
        content = export_shots_to_csv(shots).encode("utf-8")
        return content, "text/csv; charset=utf-8", "csv"
    payload = _export_json_payload(version, shots, cards)
    if export_format == "json":
        content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        return content, "application/json; charset=utf-8", "json"
    if export_format == "pdf":
        content = _render_pdf_bytes(payload)
        return content, "application/pdf", "pdf"
    raise ValueError("storyboard_export_format_invalid")


def save_export_blob(*, export_public_id: str, extension: str, content: bytes) -> tuple[str, int]:
    root = artifact_root()
    folder = root / export_public_id[:2]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{export_public_id}.{extension}"
    path.write_bytes(content)
    return str(path), len(content)


def open_export_blob(storage_path: str) -> bytes:
    return Path(storage_path).read_bytes()
