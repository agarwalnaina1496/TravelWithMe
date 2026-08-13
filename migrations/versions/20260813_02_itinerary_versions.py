"""Move accepted itinerary revision history out of trip_state into its own table (TWM-155)."""

from alembic import op

revision = "20260813_02"
down_revision = "20260813_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE twm_app.itinerary_versions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            trip_id uuid NOT NULL REFERENCES twm_app.trips(id) ON DELETE CASCADE,
            version integer NOT NULL CHECK (version >= 1),
            source_guide_revision integer NOT NULL CHECK (source_guide_revision >= 1),
            result jsonb NOT NULL CHECK (jsonb_typeof(result) = 'object'),
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (trip_id, version)
        )
    """)
    op.execute("CREATE INDEX itinerary_versions_trip_version_idx ON twm_app.itinerary_versions (trip_id, version ASC)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS twm_app.itinerary_versions")
