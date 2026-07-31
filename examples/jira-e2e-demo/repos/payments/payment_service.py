from dataclasses import dataclass


@dataclass
class Payment:
    order_id: str
    amount_cents: int


def authorize(payment: Payment) -> str:
    return f"authorization:{payment.order_id}"
