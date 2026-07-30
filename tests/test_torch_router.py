import torch

from sacm.ml.torch_router import AgentRouter


def test_router_masks_disallowed_state_transitions():
    router = AgentRouter(context_dim=4, num_agents=2, num_states=3)
    router.transition_logits.data.fill_(0.0)
    router._fsm_mask.data.copy_(
        torch.tensor(
            [
                [True, False, False],
                [False, True, True],
                [True, False, True],
            ]
        )
    )

    transition_matrix = router._masked_transition_matrix()

    assert transition_matrix[0, 1].item() == 0.0
    assert transition_matrix[0, 2].item() == 0.0
    assert torch.allclose(transition_matrix.sum(dim=-1), torch.ones(3))
