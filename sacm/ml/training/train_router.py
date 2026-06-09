import torch

from sacm.ml.torch_router import AgentRouter


def train_step(model: AgentRouter, batch_size: int = 4, context_dim: int = 256) -> float:
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    contexts = torch.randn(batch_size, context_dim)
    beliefs = torch.softmax(torch.randn(batch_size, 7), dim=-1)
    targets = torch.randint(0, 7, (batch_size,))
    output = model(contexts, beliefs)
    loss = torch.nn.functional.cross_entropy(output["agent_probs"], targets)
    optimizer.zero_grad()
    loss.backward()
    optimizer.step()
    return float(loss.item())
