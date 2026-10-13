import pytest
from pydantic import ValidationError

from finance_agents.contracts.common import Money


@pytest.mark.parametrize("amount", ["1.234", "NaN", "Infinity", "-0.01"])
def test_invalid_money_rejected_without_rounding(amount):
    with pytest.raises(ValidationError):
        Money(amount=amount, currency="CNY")


def test_money_preserves_exact_cent_precision():
    money = Money(amount="123456789.01", currency="CNY")
    assert money.model_dump(mode="json")["amount"] == "123456789.01"
