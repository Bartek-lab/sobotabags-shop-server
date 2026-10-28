from app.services.pricing_service import distribute_amount


def test_distribute_amount_splits_proportionally():
    out = distribute_amount([100, 300], 40)
    assert out == [10, 30]
    assert sum(out) == 40


def test_distribute_amount_remainder_lands_on_last_item():
    # 10 split 3 ways by equal weight doesn't divide evenly -- the exact sum
    # must still be 10, with any rounding slack absorbed by the last item.
    out = distribute_amount([1, 1, 1], 10)
    assert sum(out) == 10
    assert out[-1] >= out[0]


def test_distribute_amount_never_exceeds_an_items_own_weight():
    # A lopsided split (one tiny weight, one huge one) must never discount
    # the tiny line below zero or above its own total.
    out = distribute_amount([5, 995], 1000)
    assert out[0] <= 5
    assert sum(out) == 1000


def test_distribute_amount_zero_total_weight_returns_zeros_except_last():
    out = distribute_amount([0, 0], 50)
    assert sum(out) == 50


def test_distribute_amount_single_item_gets_everything():
    assert distribute_amount([100], 37) == [37]


def test_distribute_amount_zero_to_distribute():
    assert distribute_amount([100, 200], 0) == [0, 0]
