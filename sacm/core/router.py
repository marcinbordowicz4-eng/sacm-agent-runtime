import os
import tempfile
import threading
from pathlib import Path

import torch

from sacm.core.agent_registry import AGENT_CLASSES
from sacm.ml.embeddings import EmbeddingService
from sacm.ml.torch_router import AgentRouter

NUM_AGENTS = len(AGENT_CLASSES)
NUM_STATES = 7
CONTEXT_DIM = 256

_LR = float(os.getenv("SACM_ROUTER_LR", "3e-4"))


def _weights_path() -> Path:
    configured = os.getenv("SACM_ROUTER_WEIGHTS")
    if configured:
        return Path(configured).expanduser()
    state_root = Path(os.getenv("SACM_STATE_ROOT", ".sacm/state")).expanduser()
    return state_root / "sacm_router_weights.pt"


class RouterService:
    def __init__(self):
        self._lock = threading.Lock()
        self.model = AgentRouter(
            context_dim=CONTEXT_DIM, num_agents=NUM_AGENTS, num_states=NUM_STATES
        )
        self.optimizer = torch.optim.Adam(self.model.parameters(), lr=_LR)
        self._load_weights_safe()
        self.model.eval()
        self.embedding_service = EmbeddingService(dim=CONTEXT_DIM)

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def route(self, context_vector: list[float], belief_state: list[float]) -> dict:
        ctx = torch.tensor([context_vector[:CONTEXT_DIM]], dtype=torch.float32)
        belief = torch.tensor([belief_state[:NUM_STATES]], dtype=torch.float32)
        with torch.no_grad():
            output = self.model(ctx, belief)
        return {
            "selected_agent_index": int(output["selected_agents"][0].item()),
            "agent_probs": output["agent_probs"][0].tolist(),
            "next_belief": output["next_belief"][0].tolist(),
        }

    # ------------------------------------------------------------------
    # Online learning
    # ------------------------------------------------------------------

    def update(
        self,
        context_vector: list[float],
        belief_state: list[float],
        selected_agent_index: int,
        advantage: float,
    ) -> float:
        """Run one REINFORCE gradient step with the exact routing inputs.

        Thread-safe: serialised behind a lock so concurrent requests do not
        corrupt the optimizer state.
        """
        ctx = torch.tensor([context_vector[:CONTEXT_DIM]], dtype=torch.float32)
        belief = torch.tensor([belief_state[:NUM_STATES]], dtype=torch.float32)
        agent_idx = torch.tensor([selected_agent_index])
        adv = torch.tensor([advantage], dtype=torch.float32)

        with self._lock:
            loss = self.model.train_step(ctx, belief, agent_idx, adv, self.optimizer)
        return loss

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_weights(self, path: str | None = None) -> None:
        """Atomically persist model + optimizer state to disk."""
        dest = Path(path).expanduser() if path else _weights_path()
        temporary_path: Path | None = None
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            with self._lock, tempfile.NamedTemporaryFile(
                dir=dest.parent,
                prefix=f".{dest.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                torch.save(
                    {
                        "model": self.model.state_dict(),
                        "optimizer": self.optimizer.state_dict(),
                    },
                    temporary,
                )
            os.replace(temporary_path, dest)
        except OSError as exc:
            raise RuntimeError(
                f"Cannot persist router weights at {dest}: {exc}"
            ) from exc
        finally:
            if temporary_path and temporary_path.exists():
                temporary_path.unlink()

    def _load_weights_safe(self, path: str | None = None) -> None:
        """Load weights if a checkpoint exists; skip gracefully on any error."""
        src = Path(path).expanduser() if path else _weights_path()
        if not src.exists():
            return
        try:
            state = torch.load(src, map_location="cpu", weights_only=True)
            self.model.load_state_dict(state["model"])
            self.optimizer.load_state_dict(state["optimizer"])
        except Exception as exc:  # corrupt / incompatible checkpoint
            print(f"[RouterService] Could not load weights from {src}: {exc}. Starting fresh.")
