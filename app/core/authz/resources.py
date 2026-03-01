"""Resource loaders for authorization checks."""
from __future__ import annotations

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.authz.types import ResourceContext
from app.core.database import resolve_novel
from app.models.storyboard import StoryboardProject


def load_novel_resource(request: Request, db: Session) -> ResourceContext | None:
    novel_id = str(request.path_params.get("novel_id") or "")
    if not novel_id:
        return None
    novel = resolve_novel(db, novel_id)
    if not novel:
        return None
    return ResourceContext(
        resource_type="novel",
        resource_id=novel.uuid or str(novel.id),
        owner_uuid=novel.user_id,
        attributes={"db_id": novel.id},
    )


def load_storyboard_resource(request: Request, db: Session) -> ResourceContext | None:
    raw = request.path_params.get("project_id")
    if raw is None:
        return None
    try:
        project_id = int(str(raw))
    except ValueError:
        return None
    project = db.execute(
        select(StoryboardProject).where(StoryboardProject.id == project_id)
    ).scalar_one_or_none()
    if not project:
        return None
    return ResourceContext(
        resource_type="storyboard",
        resource_id=str(project.id),
        owner_uuid=project.owner_user_uuid,
        attributes={"novel_id": project.novel_id, "status": project.status},
    )
