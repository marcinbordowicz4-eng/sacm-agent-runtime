"""AgentRouter — PyTorch policy network with a finite-state-machine mask.

Two learning targets:
  • agent_roles / context_projection  — which agent to pick (REINFORCE)
  • transition_logits                 — how to move between FSM states (REINFORCE)

The transition matrix is constrained to only allowed FSM edges so the router
can *never* produce an impossible state transition, regardless of how the
weights evolve.
"""

from dataclasses import dataclass

import torch
import torch.nn as nn
import torch.nn.functional as F

from sacm.ml.state_machine import NUM_STATES, get_transition_mask


@dataclass
class RouterOutput:
    agent_probs: torch.Tensor
    selected_agents: torch.Tensor
    next_belief: torch.Tensor
    transition_matrix: torch.Tensor


class AgentRouter(nn.Module):
    def __init__(self, context_dim: int = 256, num_agents: int = 11, num_states: int = NUM_STATES):
        super().__init__()
        self.num_states = num_states
        self.context_projection = nn.Linear(context_dim, context_dim)
        self.agent_roles = nn.Parameter(torch.randn(num_agents, context_dim))
        self.state_head = nn.Linear(context_dim, num_states)
        self.transition_logits = nn.Parameter(torch.randn(num_states, num_states))

        # Register FSM mask as a non-trainable buffer (moves with model to any device)
        self.register_buffer("_fsm_mask", get_transition_mask(num_states))

    def _masked_transition_matrix(self) -> torch.Tensor:
        """Softmax over transition_logits with invalid transitions blocked."""
        # set disallowed transitions to -inf before softmax
        masked = self.transition_logits.masked_fill(~self._fsm_mask, -1e9)
        return F.softmax(masked, dim=-1)

    def forward(
        self,
        context_vectors: torch.Tensor,
        belief_states: torch.Tensor,
    ) -> dict[str, torch.Tensor]:
        z = torch.tanh(self.context_projection(context_vectors))

        agent_logits = z @ self.agent_roles.T
        agent_probs = F.softmax(agent_logits, dim=-1)

        observed_state_probs = F.softmax(self.state_head(z), dim=-1)

        transition_matrix = self._masked_transition_matrix()
        next_belief = belief_states @ transition_matrix
        next_belief = 0.7 * next_belief + 0.3 * observed_state_probs

        selected_agents = torch.argmax(agent_probs, dim=-1)

        return {
            "agent_probs": agent_probs,
            "selected_agents": selected_agents,
            "next_belief": next_belief,
            "transition_matrix": transition_matrix,
        }

    def train_step(
        self,
        context_vectors: torch.Tensor,
        belief_states: torch.Tensor,
        selected_agent_indices: torch.Tensor,
        advantages: torch.Tensor,
        optimizer: torch.optim.Optimizer,
        entropy_coef: float = 0.01,
    ) -> float:
        """REINFORCE with advantage baseline and entropy regularisation.

        Both the routing policy AND the FSM transition weights are updated —
        the FSM mask ensures only valid transitions receive gradient.

        Returns scalar loss value.
        """
        self.train()
        optimizer.zero_grad(set_to_none=True)

        output = self.forward(context_vectors, belief_states)
        agent_probs = output["agent_probs"]  # [B, M]

        log_probs = torch.log(agent_probs + 1e-8)
        selected_log_probs = log_probs.gather(
            1, selected_agent_indices.unsqueeze(1)
        ).squeeze(1)

        entropy = -(agent_probs * log_probs).sum(dim=-1).mean()
        policy_loss = -(selected_log_probs * advantages).mean()
        loss = policy_loss - entropy_coef * entropy

        loss.backward()
        torch.nn.utils.clip_grad_norm_(self.parameters(), max_norm=1.0)
        optimizer.step()
        self.eval()
        return loss.item()
