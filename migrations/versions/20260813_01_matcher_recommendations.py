"""Move matcher recommendation rounds out of trip_state into their own table (TWM-153)."""

from alembic import op

revision = "20260813_01"
down_revision = "20260806_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE twm_app.matcher_recommendations (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            trip_id uuid NOT NULL REFERENCES twm_app.trips(id) ON DELETE CASCADE,
            version integer NOT NULL CHECK (version >= 1),
            status text NOT NULL,
            message text NOT NULL,
            trip_type text,
            options jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(options) = 'array'),
            traveler_criteria jsonb CHECK (traveler_criteria IS NULL OR jsonb_typeof(traveler_criteria) = 'array'),
            constraint_adjustment_suggestions jsonb CHECK (constraint_adjustment_suggestions IS NULL OR jsonb_typeof(constraint_adjustment_suggestions) = 'array'),
            agent_meta jsonb NOT NULL CHECK (jsonb_typeof(agent_meta) = 'object'),
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (trip_id, version)
        )
    """)
    op.execute("CREATE INDEX matcher_recommendations_trip_version_idx ON twm_app.matcher_recommendations (trip_id, version DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS twm_app.matcher_recommendations")
