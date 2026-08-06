"""Create guest-owned trip persistence tables."""

from alembic import op

revision = "20260806_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE twm_app.guest_sessions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            token_hash text NOT NULL UNIQUE CHECK (length(token_hash) = 64),
            created_at timestamptz NOT NULL DEFAULT now(),
            last_seen_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz NOT NULL
        )
    """)
    op.execute("CREATE INDEX guest_sessions_expires_at_idx ON twm_app.guest_sessions (expires_at)")
    op.execute("""
        CREATE TABLE twm_app.trips (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            guest_session_id uuid NOT NULL REFERENCES twm_app.guest_sessions(id),
            title varchar(120) NOT NULL CHECK (length(trim(title)) > 0),
            product_mode text NOT NULL DEFAULT 'self_led' CHECK (product_mode IN ('self_led', 'twm_led')),
            trip_state jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(trip_state) = 'object'),
            ui_state jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(ui_state) = 'object'),
            version integer NOT NULL DEFAULT 1 CHECK (version >= 1),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX trips_guest_updated_idx ON twm_app.trips (guest_session_id, updated_at DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS twm_app.trips")
    op.execute("DROP TABLE IF EXISTS twm_app.guest_sessions")
