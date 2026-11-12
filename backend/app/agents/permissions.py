"""Which tools each agent may call. Anything not listed is blocked and logged.

Deliberately granted to NO agent: prepare_paypal_order, capture_approved_payment.
Money moves only through the payment API after an explicit human approval (Part 4).
"""

AGENT_PERMISSIONS: dict[str, frozenset[str]] = {
    "intent": frozenset({"validate_requirements", "search_catalog"}),
    "research": frozenset({"search_catalog", "get_product_details", "search_vendors", "get_vendor_quotes", "check_availability"}),
    "evaluation": frozenset({"compare_offers", "get_vendor_quotes", "check_availability", "calculate_purchase_total", "validate_budget"}),
    "procurement": frozenset({"validate_requirements", "check_availability", "calculate_purchase_total",
                              "create_purchase_order", "get_purchase_order", "update_purchase_order"}),
    "finance": frozenset({"get_purchase_order", "validate_budget", "validate_currency", "check_approval_status",
                          "update_purchase_order", "prepare_payment_request"}),
    "reconciliation": frozenset({"get_purchase_order", "get_paypal_order", "verify_paypal_capture", "retrieve_payment_status", "update_purchase_order"}),
}

NO_AGENT_TOOLS = frozenset({"prepare_paypal_order", "capture_approved_payment"})


def tools_for(agent_name: str) -> frozenset[str]:
    return AGENT_PERMISSIONS.get(agent_name, frozenset())
