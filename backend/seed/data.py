"""Sign-in accounts. Vendors, products and offers are never loaded automatically: people add them in
Vendor Intelligence, or import the starter catalogue (seed/catalog.py) from there."""

USERS = [
    # email, full_name, role
    ("requester@procura.local", "Riley Requester", "requester"),
    ("approver@procura.local", "Avery Approver", "approver"),
    ("admin@procura.local", "Ade Admin", "admin"),
    ("requester2@procura.local", "Robin Requester", "requester"),
]
DEMO_USER_EMAIL = "requester@procura.local"
# Development password for every seeded account. Shown on the login page only when
# DEMO_ACCOUNTS_ENABLED=true and APP_ENV is not production. Never reuse it for real accounts.
DEMO_PASSWORD = "Procura-Demo-2026"
