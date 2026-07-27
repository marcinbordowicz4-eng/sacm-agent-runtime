"""Tests for the FeedbackService and the router's online learning loop."""

import copy
import os
from unittest.mock import MagicMock

import pytest
import torch

from sacm.ml.torch_router import AgentRouter
from sacm.schemas.result import AgentResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_result(
    confidence: float, next_state: str, *, verified: bool = True
) -> AgentResult:
    return AgentResult(
        agent_name="TestAgent",
        summary="test",
        confidence=confidence,
        next_state_hint=next_state,
        actions=[{"type": "TEST_RESULT", "passed": True}] if verified else [],
    )


def _uniform_belief(num_states: int = 7) -> list[float]:
    return [1.0 / num_states] * num_states


# ---------------------------------------------------------------------------
# FeedbackService.compute_reward
# ---------------------------------------------------------------------------

class TestComputeReward:
    def setup_method(self):
        from sacm.core.feedback_service import FeedbackService
        self.svc = FeedbackService(db=MagicMock(), router_service=MagicMock())

    def test_done_state_gives_max_progress(self):
        result = _make_result(confidence=1.0, next_state="done")
        reward = self.svc.compute_reward(result)
        assert abs(reward - 1.0) < 1e-6

    def test_blocked_state_gives_low_reward(self):
        result = _make_result(confidence=1.0, next_state="blocked")
        reward = self.svc.compute_reward(result)
        assert reward < 0.2

    def test_task_done_bonus_added(self):
        result = _make_result(confidence=0.7, next_state="testing")
        r_no_bonus = self.svc.compute_reward(result, task_done=False)
        r_bonus = self.svc.compute_reward(result, task_done=True)
        assert r_bonus > r_no_bonus

    def test_reward_capped_at_one(self):
        result = _make_result(confidence=1.0, next_state="done")
        reward = self.svc.compute_reward(result, task_done=True)
        assert reward <= 1.0

    def test_unknown_state_uses_default(self):
        result = _make_result(confidence=0.8, next_state="unknown_state")
        reward = self.svc.compute_reward(result)
        assert 0.0 < reward < 1.0

    def test_unverified_result_has_no_training_reward(self):
        result = _make_result(confidence=1.0, next_state="done", verified=False)
        assert self.svc.compute_reward(result, task_done=True) == 0.0

    @pytest.mark.parametrize("state,expected_gt", [
        ("done", 0.9),
        ("testing", 0.6),
        ("debugging", 0.2),
        ("blocked", 0.0),
    ])
    def test_state_ordering(self, state, expected_gt):
        result = _make_result(confidence=0.9, next_state=state)
        assert self.svc.compute_reward(result) >= expected_gt


# ---------------------------------------------------------------------------
# AgentRouter.train_step
# ---------------------------------------------------------------------------

class TestRouterTrainStep:
    def _make_router(self):
        model = AgentRouter(context_dim=32, num_agents=3, num_states=4)
        optimizer = torch.optim.Adam(model.parameters(), lr=1e-2)
        return model, optimizer

    def test_train_step_changes_weights(self):
        model, optimizer = self._make_router()
        ctx = torch.randn(1, 32)
        belief = torch.softmax(torch.randn(1, 4), dim=-1)
        agent_idx = torch.tensor([0])
        advantage = torch.tensor([0.5])

        weights_before = copy.deepcopy(model.agent_roles.data)
        model.train_step(ctx, belief, agent_idx, advantage, optimizer)
        weights_after = model.agent_roles.data

        assert not torch.allclose(weights_before, weights_after), \
            "Weights should change after a gradient step"

    def test_train_step_returns_finite_loss(self):
        model, optimizer = self._make_router()
        ctx = torch.randn(1, 32)
        belief = torch.softmax(torch.randn(1, 4), dim=-1)
        loss = model.train_step(
            ctx, belief, torch.tensor([1]), torch.tensor([0.3]), optimizer
        )
        assert isinstance(loss, float)
        assert not (loss != loss), "Loss must not be NaN"
        assert abs(loss) < 1e4, "Loss must be finite"

    def test_train_step_does_not_accumulate_gradients(self):
        """zero_grad at the start of each step prevents accumulation.

        After N steps the gradient norm should be comparable to after 1 step,
        not N× larger.  We verify by comparing the L2 norm after step 1 and
        step 2 — if gradients accumulated the second norm would be ~2× the first.
        """
        model, optimizer = self._make_router()
        ctx = torch.randn(1, 32)
        belief = torch.softmax(torch.randn(1, 4), dim=-1)

        model.train_step(ctx, belief, torch.tensor([0]), torch.tensor([0.5]), optimizer)
        norm_after_1 = sum(
            p.grad.norm().item() for p in model.parameters() if p.grad is not None
        )

        model.train_step(ctx, belief, torch.tensor([0]), torch.tensor([0.5]), optimizer)
        norm_after_2 = sum(
            p.grad.norm().item() for p in model.parameters() if p.grad is not None
        )

        # Accumulated gradients would produce norm_after_2 ≈ 2 × norm_after_1.
        # Non-accumulated gradients reset each step, so the ratio must be < 1.5.
        ratio = norm_after_2 / (norm_after_1 + 1e-8)
        assert ratio < 1.5, f"Gradients appear to be accumulating: ratio={ratio:.2f}"

    def test_model_in_eval_mode_after_train_step(self):
        model, optimizer = self._make_router()
        ctx = torch.randn(1, 32)
        belief = torch.softmax(torch.randn(1, 4), dim=-1)
        model.train_step(ctx, belief, torch.tensor([2]), torch.tensor([0.1]), optimizer)
        assert not model.training


# ---------------------------------------------------------------------------
# RouterService.save_weights / _load_weights_safe
# ---------------------------------------------------------------------------

class TestRouterServicePersistence:
    def test_save_and_load_roundtrip(self, tmp_path):
        from sacm.core.router import RouterService

        svc = RouterService()
        weights_path = str(tmp_path / "weights.pt")

        # snapshot original agent_roles
        original = svc.model.agent_roles.data.clone()
        svc.save_weights(weights_path)

        # mutate in-place
        svc.model.agent_roles.data.fill_(0.0)

        # reload
        svc._load_weights_safe(weights_path)
        assert torch.allclose(svc.model.agent_roles.data, original), \
            "Loaded weights must match saved weights"

    def test_load_missing_file_is_noop(self, tmp_path):
        from sacm.core.router import RouterService
        svc = RouterService()
        svc._load_weights_safe(str(tmp_path / "nonexistent.pt"))  # must not raise

    def test_load_corrupt_file_is_noop(self, tmp_path):
        from sacm.core.router import RouterService
        bad = tmp_path / "bad.pt"
        bad.write_bytes(b"not a valid torch checkpoint")
        svc = RouterService()
        svc._load_weights_safe(str(bad))  # must not raise

    def test_save_is_atomic(self, tmp_path):
        """No partial writes: only the final file should exist after save."""
        from sacm.core.router import RouterService
        svc = RouterService()
        path = str(tmp_path / "weights.pt")
        svc.save_weights(path)
        assert os.path.exists(path)
        assert not os.path.exists(path + ".tmp"), "Temp file must be cleaned up"


# ---------------------------------------------------------------------------
# FeedbackService.record — reward baseline + quality EMA
# ---------------------------------------------------------------------------

class TestFeedbackServiceRecord:
    def _make_svc(self, db=None):
        from sacm.core.feedback_service import FeedbackService
        from sacm.core.router import RouterService

        router = RouterService()
        svc = FeedbackService(db=db or MagicMock(), router_service=router)
        return svc

    def test_reward_baseline_moves_toward_recent_reward(self):
        svc = self._make_svc()
        initial_baseline = svc._reward_baseline
        ctx = [0.1] * 256
        belief = _uniform_belief()
        result = _make_result(confidence=1.0, next_state="done")

        for _ in range(5):
            svc.record(ctx, belief, 0, "ClaudeReasoner", result)

        assert svc._reward_baseline > initial_baseline, \
            "Baseline should drift toward high rewards"

    def test_record_returns_positive_reward(self):
        svc = self._make_svc()
        result = _make_result(confidence=0.8, next_state="coding")
        reward = svc.record([0.0] * 256, _uniform_belief(), 1, "CodexCoder", result)
        assert reward > 0.0

    def test_update_count_increments(self):
        svc = self._make_svc()
        result = _make_result(confidence=0.5, next_state="planning")
        for _i in range(3):
            svc.record([0.0] * 256, _uniform_belief(), 0, "Architect", result)
        assert svc._update_count == 3

    def test_quality_score_ema_update(self, db):
        """quality_score should move toward the reward in the DB."""
        import datetime
        import uuid

        from sacm.core.feedback_service import EMA_ALPHA, FeedbackService
        from sacm.core.router import RouterService
        from sacm.infrastructure.db.models import Agent

        # seed an agent row in the test DB
        agent = Agent(
            id=str(uuid.uuid4()),
            name="TestEMAAgent",
            role="test",
            provider="mock",
            model_name="mock",
            quality_score=0.5,
            cost_weight=1.0,
            latency_score=0.5,
            created_at=datetime.datetime.utcnow(),
            updated_at=datetime.datetime.utcnow(),
        )
        db.add(agent)
        db.commit()

        router = RouterService()
        svc = FeedbackService(db=db, router_service=router)

        result = _make_result(confidence=1.0, next_state="done")
        svc.record([0.0] * 256, _uniform_belief(), 0, "TestEMAAgent", result)

        db.refresh(agent)
        expected = EMA_ALPHA * 1.0 + (1 - EMA_ALPHA) * 0.5
        assert abs(agent.quality_score - expected) < 0.01
