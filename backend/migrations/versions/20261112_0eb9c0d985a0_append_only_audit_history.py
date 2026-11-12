"""append only audit history

Revision ID: 0eb9c0d985a0
Revises: a4ce40aeac5e
Create Date: 2026-11-12 12:40:32.279052

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0eb9c0d985a0'
down_revision: Union[str, Sequence[str], None] = 'a4ce40aeac5e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLES = ("audit_logs", "workflow_state_history")

FUNCTION = """
CREATE OR REPLACE FUNCTION procura_append_only() RETURNS trigger AS $$
BEGIN
    -- History is append-only. The single allowed change is the FK's ON DELETE SET NULL when a user
    -- is removed: actor_user_id becomes NULL and every other column stays identical.
    IF TG_OP = 'UPDATE'
       AND NEW.actor_user_id IS NULL
       AND (to_jsonb(NEW) - 'actor_user_id') = (to_jsonb(OLD) - 'actor_user_id') THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION '% is append-only (% not allowed)', TG_TABLE_NAME, TG_OP USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    """Make audit and workflow history append-only at the database level (no UPDATE/DELETE)."""
    op.execute(FUNCTION)
    for t in TABLES:
        op.execute(f"CREATE TRIGGER {t}_append_only BEFORE UPDATE OR DELETE ON {t} FOR EACH ROW EXECUTE FUNCTION procura_append_only()")


def downgrade() -> None:
    for t in TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {t}_append_only ON {t}")
    op.execute("DROP FUNCTION IF EXISTS procura_append_only()")
