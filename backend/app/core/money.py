"""Ghana cedi formatting. Amounts are stored and exchanged as numbers; the ISO code stays "GHS" for
payment providers, while people see the cedi sign: GH₵1,250.00."""
from decimal import Decimal

CURRENCY_CODE = "GHS"
SYMBOL = "GH₵"


def cedis(amount: Decimal | float | int | str, decimals: int = 2) -> str:
    return f"{SYMBOL}{Decimal(str(amount)):,.{decimals}f}"


def cedis_compact(amount: Decimal | float | int) -> str:
    """GH₵40 for whole amounts, GH₵40.50 otherwise (used in short chips like 'Budget: up to GH₵40')."""
    a = Decimal(str(amount))
    return cedis(a, 0) if a == a.to_integral_value() else cedis(a, 2)
