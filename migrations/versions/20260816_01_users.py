"""Create user accounts table for email/password auth (TWM-178)."""

from alembic import op

revision = "20260816_01"
down_revision = "20260814_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE twm_app.users (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            email text NOT NULL UNIQUE CHECK (length(trim(email)) > 0),
            password_hash text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS twm_app.users")
