"""Lightweight FSM helpers for the PyTorch router layer.

Provides the transition mask that constrains AgentRouter.transition_logits
so learned weights can only flow along valid FSM edges.

States (integer indices)
------------------------
0  planning
1  coding
2  testing
3  debugging
4  reviewing
5  done
6  blocked
"""

import torch

NUM_STATES = 7

# Human-readable labels — used only for logging/debugging
STATE_NAMES = ["planning", "coding", "testing", "debugging",
               "reviewing", "done", "blocked"]

# Allowed (from, to) transitions.  Self-loops are always allowed.
_ALLOWED: list[tuple[int, int]] = [
    (0, 0), (0, 1),              # planning → planning / coding
    (1, 1), (1, 2), (1, 4),      # coding   → coding / testing / reviewing
    (2, 2), (2, 4), (2, 3),      # testing  → testing / reviewing / debugging
    (3, 3), (3, 1), (3, 2),      # debugging→ debugging / coding / testing
    (4, 4), (4, 5), (4, 1),      # reviewing→ reviewing / done / coding
    (5, 5),                      # done     → done  (terminal)
    (6, 6), (6, 1),              # blocked  → blocked / coding (recovery)
]


def get_transition_mask(num_states: int = NUM_STATES) -> torch.Tensor:
    """Return a (num_states, num_states) boolean mask of allowed transitions.

    If num_states != NUM_STATES (e.g. in unit tests) the full matrix is
    allowed so no valid gradient paths are blocked.
    """
    if num_states != NUM_STATES:
        return torch.ones(num_states, num_states, dtype=torch.bool)

    mask = torch.zeros(num_states, num_states, dtype=torch.bool)
    for i, j in _ALLOWED:
        mask[i, j] = True
    return mask
