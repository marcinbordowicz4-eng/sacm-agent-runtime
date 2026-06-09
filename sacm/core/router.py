import torch

from sacm.ml.embeddings import EmbeddingService
from sacm.ml.torch_router import AgentRouter

NUM_AGENTS = 10
NUM_STATES = 7
CONTEXT_DIM = 256


class RouterService:
    def __init__(self):
        self.model = AgentRouter(
            context_dim=CONTEXT_DIM, num_agents=NUM_AGENTS, num_states=NUM_STATES
        )
        self.model.eval()
        self.embedding_service = EmbeddingService(dim=CONTEXT_DIM)

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
