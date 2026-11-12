"""Sign-in accounts. Vendors, products and offers are never loaded automatically: people add them in
Vendor Intelligence, or import the starter catalogue (seed/catalog.py) from there."""

USERS = [
    # email, full_name, role
    ("requester@keicy.local", "Riley Requester", "requester"),
    ("approver@keicy.local", "Avery Approver", "approver"),
    ("admin@keicy.local", "Ade Admin", "admin"),
    ("requester2@keicy.local", "Robin Requester", "requester"),
]
DEMO_USER_EMAIL = "requester@keicy.local"
# Development password for every seeded account. Shown on the login page only when
# DEMO_ACCOUNTS_ENABLED=true and APP_ENV is not production. Never reuse it for real accounts.
DEMO_PASSWORD = "Keicy-Demo-2026"
