"""Human-friendly text shared by error messages (and later notifications)."""

from decimal import Decimal

from app.domain.enums import QuantityUnit

# kg is never pluralised; the other units take an "s".
_SINGULAR_ONLY = {QuantityUnit.KG}


def format_quantity(amount: Decimal | float, unit: QuantityUnit) -> str:
    """Decimal("15") + package -> "15 packages"; 1 package -> "1 package"; 12.5 kg -> "12.5 kg"."""
    number = Decimal(str(amount)).normalize()
    text = f"{number:f}"
    if unit in _SINGULAR_ONLY or number == 1:
        return f"{text} {unit.value}"
    return f"{text} {unit.value}s"
