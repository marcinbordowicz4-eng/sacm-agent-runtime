from dataclasses import dataclass


@dataclass
class CheckoutRequest:
    order_id: str
    payment_token: str
