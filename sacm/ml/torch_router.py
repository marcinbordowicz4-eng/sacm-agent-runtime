from dataclasses import dataclass

import torch
import torch.nn as nn
import torch.nn.functional as F


@dataclass
class RouterOutput:
    agent_probs: torch.Tensor
    selected_agents: torch.Tensor
    next_belief: torch.Tensor
    transition_matrix: torch.Tensor


class AgentRouter(nn.Module):
    def __init__(self, context_dim: int = 256, num_agents: int = 7, num_states: int = 7):
        super().__init__()
        self.context_projection = nn.Linear(context_dim, context_dim)
        self.agent_roles = nn.Parameter(torch.randn(num_agents, context_dim))
        self.state_head = nn.Linear(context_dim, num_states)
        self.transition_logits = nn.Parameter(torch.randn(num_states, num_states))

    def forward(
        self,
        context_vectors: torch.Tensor,
        belief_states: torch.Tensor,
    ) -> dict[str, torch.Tensor]:
        z = torch.tanh(self.context_projection(context_vectors))
        agent_logits = z @ self.agent_roles.T
        agent_probs = F.softmax(agent_logits, dim=-1)
        observed_state_probs = F.softmax(self.state_head(z), dim=-1)
        transition_matrix = F.softmax(self.transition_logits, dim=-1)
        next_belief = belief_states @ transition_matrix
        next_belief = 0.7 * next_belief + 0.3 * observed_state_probs
        selected_agents = torch.argmax(agent_probs, dim=-1)
        return {
            "agent_probs": agent_probs,
            "selected_agents": selected_agents,
            "next_belief": next_belief,
            "transition_matrix": transition_matrix,
        }
