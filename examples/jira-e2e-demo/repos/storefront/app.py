from contracts import CheckoutRequest


def checkout(request: CheckoutRequest) -> dict[str, str]:
    return {"order_id": request.order_id, "status": "accepted"}
