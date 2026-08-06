"""Add the idempotent Backend trip-command ledger."""

from alembic import op

revision = "20260806_02"
down_revision = "20260806_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE twm_app.trip_commands (
            guest_session_id uuid NOT NULL REFERENCES twm_app.guest_sessions(id),
            trip_id uuid NOT NULL REFERENCES twm_app.trips(id) ON DELETE CASCADE,
            idempotency_key uuid NOT NULL,
            request_hash text NOT NULL CHECK (length(request_hash) = 64),
            response jsonb NOT NULL CHECK (jsonb_typeof(response) = 'object'),
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (trip_id, idempotency_key)
        )
    """)
    op.execute("CREATE INDEX trip_commands_guest_idx ON twm_app.trip_commands (guest_session_id, created_at DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS twm_app.trip_commands")
