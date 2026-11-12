"""tool call statuses

Revision ID: 256e4f3ef3ab
Revises: 0c3bb8c66650
Create Date: 2026-11-12 11:35:35.183959

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '256e4f3ef3ab'
down_revision: Union[str, Sequence[str], None] = '0c3bb8c66650'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


NAME = "ck_agent_tool_logs_status_valid"
NEW = "status IN ('SUCCEEDED', 'FAILED', 'TIMED_OUT', 'BLOCKED', 'DUPLICATE')"
OLD = "status IN ('RUNNING', 'SUCCEEDED', 'FAILED', 'TIMED_OUT')"


def upgrade() -> None:
    """Tool calls get their own status set (adds BLOCKED and DUPLICATE, drops RUNNING)."""
    op.drop_constraint(op.f(NAME), "agent_tool_logs", type_="check")
    op.create_check_constraint(op.f(NAME), "agent_tool_logs", NEW)


def downgrade() -> None:
    op.execute("DELETE FROM agent_tool_logs WHERE status IN ('BLOCKED', 'DUPLICATE')")
    op.drop_constraint(op.f(NAME), "agent_tool_logs", type_="check")
    op.create_check_constraint(op.f(NAME), "agent_tool_logs", OLD)
