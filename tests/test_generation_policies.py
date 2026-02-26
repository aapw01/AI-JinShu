from app.services.generation.policies import (
    ClosurePolicyEngine,
    ClosurePolicyInput,
    PacingController,
    PacingInput,
)


def test_closure_policy_finalize_when_threshold_passed():
    out = ClosurePolicyEngine.decide(
        ClosurePolicyInput(
            generated_chapters=48,
            target_chapters=50,
            min_total_chapters=48,
            max_total_chapters=52,
            remaining_chapters=2,
            remaining_ratio=0.04,
            phase_mode="finale",
            unresolved_count=0,
            must_close_coverage=0.98,
            closure_threshold=0.95,
            tail_rewrite_attempts=0,
            bridge_attempts=0,
        )
    )
    assert out.action == "finalize"
    assert "coverage_pass" in out.reason_codes


def test_closure_policy_bridge_when_unresolved_and_budget_left():
    out = ClosurePolicyEngine.decide(
        ClosurePolicyInput(
            generated_chapters=50,
            target_chapters=50,
            min_total_chapters=48,
            max_total_chapters=52,
            remaining_chapters=1,
            remaining_ratio=0.02,
            phase_mode="finale",
            unresolved_count=3,
            must_close_coverage=0.8,
            closure_threshold=0.95,
            tail_rewrite_attempts=2,
            bridge_attempts=0,
        )
    )
    assert out.action == "bridge_chapter"
    assert out.next_limits["bridge_budget_left"] >= 1


def test_closure_policy_force_finalize_when_budget_exhausted():
    out = ClosurePolicyEngine.decide(
        ClosurePolicyInput(
            generated_chapters=52,
            target_chapters=50,
            min_total_chapters=48,
            max_total_chapters=52,
            remaining_chapters=0,
            remaining_ratio=0.0,
            phase_mode="finale",
            unresolved_count=2,
            must_close_coverage=0.7,
            closure_threshold=0.95,
            tail_rewrite_attempts=2,
            bridge_attempts=2,
        )
    )
    assert out.action == "force_finalize"


def test_pacing_controller_accelerates_after_streak():
    out = PacingController.decide(
        PacingInput(
            phase_mode="converge",
            low_progress_streak=1,
            progress_signal=0.3,
        )
    )
    assert out.mode == "accelerated"
    assert out.low_progress_streak == 2


def test_pacing_controller_closing_accelerated_in_finale():
    out = PacingController.decide(
        PacingInput(
            phase_mode="finale",
            low_progress_streak=3,
            progress_signal=0.2,
        )
    )
    assert out.mode == "closing_accelerated"
    assert "closing_window" in out.reason_codes
