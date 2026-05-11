"""Agent contracts (Phase 0 §4.3).

Pydantic schemas shared across agent invocations: writer / reviewer /
fact_extractor / outline_auditor / patch_writer 等. Every breaking change
must bump ``schema_version`` with at least 4 weeks of compat window.
"""

from app.services.agents.contracts.base import AgentInput, AgentOutput
from app.services.agents.contracts.consistency import (
    BlockerEntry,
    ConsistencyReportV2,
)
from app.services.agents.contracts.fact import (
    FactArbitrationDecision,
    FactRecord,
)
from app.services.agents.contracts.foreshadow import (
    ForeshadowLifecycleEntry,
    PlantPayoffMatch,
)
from app.services.agents.contracts.outline import (
    OutlineAuditReport,
    OutlineContract,
    OutlinePromiseVerdict,
)
from app.services.agents.contracts.patch import (
    EditSpan,
    PatchInstruction,
    PatchResult,
)
from app.services.agents.contracts.reader_lens import ReaderLensVerdict
from app.services.agents.contracts.spacetime import SpacetimeAnchor
from app.services.agents.contracts.voice import VoiceDriftReport, VoiceFingerprint

__all__ = [
    "AgentInput",
    "AgentOutput",
    "BlockerEntry",
    "ConsistencyReportV2",
    "EditSpan",
    "FactArbitrationDecision",
    "FactRecord",
    "ForeshadowLifecycleEntry",
    "OutlineAuditReport",
    "OutlineContract",
    "OutlinePromiseVerdict",
    "PatchInstruction",
    "PatchResult",
    "PlantPayoffMatch",
    "ReaderLensVerdict",
    "SpacetimeAnchor",
    "VoiceDriftReport",
    "VoiceFingerprint",
]
