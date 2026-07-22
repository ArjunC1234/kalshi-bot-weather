"""PyTorch model for Edgecaster v2."""

from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import nn


@dataclass(frozen=True)
class EdgecasterV2Config:
    hidden_size: int = 48
    dropout: float = 0.20
    epochs: int = 200
    patience: int = 20
    learning_rate: float = 0.001
    weight_decay: float = 0.03
    rank_loss_weight: float = 0.50
    reward_loss_weight: float = 0.50
    trade_loss_weight: float = 0.20
    min_predicted_reward: float = 0.03
    min_trade_probability: float = 0.50
    min_raw_edge: float = 0.0
    max_spread: float = 0.15
    min_entry_price: float = 0.02
    max_entry_price: float = 0.80
    daily_budget: float = 40.0
    max_order_cost: float = 3.0
    budget_fraction: float = 0.10
    max_contracts_per_order: int = 20
    max_no_contracts_per_order: int = 10
    max_positions_per_event: int = 1
    seed: int = 29


class EdgecasterV2Net(nn.Module):
    """Candidate MLP plus DeepSets-style snapshot context."""

    def __init__(
        self,
        numeric_dim: int,
        categorical_cardinalities: list[int],
        hidden_size: int,
        dropout: float,
    ) -> None:
        super().__init__()
        embedding_dim = max(4, min(12, hidden_size // 4))
        self.embeddings = nn.ModuleList(
            nn.Embedding(cardinality, embedding_dim) for cardinality in categorical_cardinalities
        )
        input_dim = numeric_dim + embedding_dim * len(categorical_cardinalities)
        self.candidate_encoder = nn.Sequential(
            nn.Linear(input_dim, hidden_size),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_size, hidden_size),
            nn.ReLU(),
        )
        self.context_head = nn.Sequential(
            nn.Linear(hidden_size * 3, hidden_size),
            nn.ReLU(),
            nn.Dropout(dropout),
        )
        self.win_head = nn.Linear(hidden_size, 1)
        self.reward_head = nn.Linear(hidden_size, 1)
        self.rank_head = nn.Linear(hidden_size, 1)
        self.trade_head = nn.Linear(hidden_size, 1)

    def forward(
        self,
        numeric: torch.Tensor,
        categorical: torch.Tensor,
        group_ids: torch.Tensor | None = None,
    ) -> dict[str, torch.Tensor]:
        embedded = [layer(categorical[:, index]) for index, layer in enumerate(self.embeddings)]
        encoded_input = torch.cat([numeric, *embedded], dim=1) if embedded else numeric
        candidate = self.candidate_encoder(encoded_input)
        if group_ids is None:
            mean_context = candidate.mean(dim=0, keepdim=True).expand_as(candidate)
            max_context = candidate.max(dim=0, keepdim=True).values.expand_as(candidate)
        else:
            mean_context = torch.zeros_like(candidate)
            max_context = torch.zeros_like(candidate)
            for group_id in torch.unique(group_ids):
                mask = group_ids == group_id
                group = candidate[mask]
                mean_context[mask] = group.mean(dim=0, keepdim=True)
                max_context[mask] = group.max(dim=0, keepdim=True).values
        contextual = self.context_head(torch.cat([candidate, mean_context, max_context], dim=1))
        return {
            "win_logit": self.win_head(contextual).squeeze(1),
            "reward": self.reward_head(contextual).squeeze(1),
            "rank": self.rank_head(contextual).squeeze(1),
            "trade_logit": self.trade_head(contextual).squeeze(1),
        }
