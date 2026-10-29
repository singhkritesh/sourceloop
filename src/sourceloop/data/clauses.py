DEFAULT_CLAUSES: dict[str, dict] = {
    "payment_terms": {"title": "Payment terms", "negotiable": True, "locked": False},
    "delivery": {"title": "Delivery and incoterms", "negotiable": True, "locked": False},
    "warranty": {"title": "Warranty", "negotiable": True, "locked": False},
    "liability": {"title": "Limitation of liability", "negotiable": False, "locked": True},
    "termination": {"title": "Termination", "negotiable": False, "locked": True},
    "ip": {"title": "Intellectual property", "negotiable": False, "locked": True},
    "confidentiality": {"title": "Confidentiality", "negotiable": True, "locked": False},
    "quality_requirements": {"title": "Quality requirements", "negotiable": True, "locked": False},
}
