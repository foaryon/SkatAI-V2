import torch

from skatai.models.bidding import BiddingMLP, BiddingModelConfig, dense_features


def test_bidding_model_forward_shape_and_dense_features():
    hand_mask = torch.tensor([(1 << 10) - 1, ((1 << 20) - 1) ^ ((1 << 10) - 1)])
    actor = torch.tensor([0, 2])
    bidder = torch.tensor([1, 2])
    answerer = torch.tensor([0, 1])
    bid_index = torch.tensor([0, 5])
    role = torch.tensor([1, 0])

    x = dense_features(hand_mask, actor, bidder, answerer, bid_index, role)
    assert x.shape == (2, 44)
    assert torch.all(x[:, :32].sum(dim=1) == 10)

    model = BiddingMLP(BiddingModelConfig(hidden_dim=32, depth=2, dropout=0.0))
    logits = model(x)
    assert logits.shape == (2,)
    assert torch.isfinite(logits).all()
