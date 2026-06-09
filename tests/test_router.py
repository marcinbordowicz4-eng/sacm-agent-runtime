import torch

from sacm.ml.torch_router import AgentRouter


def test_router_forward():
    model = AgentRouter(context_dim=64, num_agents=7, num_states=7)
    ctx = torch.randn(2, 64)
    belief = torch.softmax(torch.randn(2, 7), dim=-1)
    out = model(ctx, belief)
    assert out["agent_probs"].shape == (2, 7)
    assert out["selected_agents"].shape == (2,)
    assert out["next_belief"].shape == (2, 7)
    assert torch.allclose(out["agent_probs"].sum(dim=-1), torch.ones(2), atol=1e-5)


def test_router_selects_valid_agent():
    model = AgentRouter(context_dim=64, num_agents=7, num_states=7)
    ctx = torch.randn(1, 64)
    belief = torch.full((1, 7), 1.0 / 7)
    out = model(ctx, belief)
    assert 0 <= int(out["selected_agents"][0].item()) < 7
