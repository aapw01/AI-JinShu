from app.services.generation.evaluation_metrics import (
    compute_abrupt_ending_risk,
    compute_closure_action_metrics,
)


def test_compute_closure_action_metrics_oscillation():
    out = compute_closure_action_metrics(["continue", "bridge_chapter", "continue", "rewrite_tail"])
    assert out["samples"] == 4
    assert out["switches"] == 3
    assert out["oscillation_rate"] == 1.0
    assert out["distribution"]["continue"] == 2


def test_compute_abrupt_ending_risk_high():
    out = compute_abrupt_ending_risk(
        {
            "action": "force_finalize",
            "unresolved_count": 2,
            "must_close_coverage": 0.6,
            "closure_threshold": 0.95,
        },
        ["短尾章。"],
    )
    assert out["score"] >= 0.5
    assert out["is_abrupt"] is True
    assert "force_finalize" in out["reasons"]


def test_compute_abrupt_ending_risk_low():
    out = compute_abrupt_ending_risk(
        {
            "action": "finalize",
            "unresolved_count": 0,
            "must_close_coverage": 0.98,
            "closure_threshold": 0.95,
        },
        ["终章：故事圆满收束，众人踏上新旅程。", "尾声：风止云开，全书完。"],
    )
    assert out["score"] < 0.5
    assert out["is_abrupt"] is False
