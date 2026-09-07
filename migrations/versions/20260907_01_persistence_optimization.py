"""Persistence-layer optimization (TWM-191): lifecycle columns + composite PKs.

* `stage` / `status` / `active_agent` move from the `trips.trip_state` jsonb
  blob to real columns on `twm_app.trips` (indexable, filterable), backfilled
  from the blob, then stripped from the blob.
* `matcher_recommendations` and `itinerary_versions` drop their unused
  surrogate `id uuid` PK in favour of the natural `PRIMARY KEY (trip_id,
  version)` — every lookup was already `(trip_id, version)` or `trip_id`.

Pre-MVP: the backfill exists to be correct if any rows are present, but no
production data is expected. `downgrade()` restores blob-only storage and
the surrogate PKs exactly.
"""

from alembic import op

revision = "20260907_01"
down_revision = "20260902_01"
branch_labels = None
depends_on = None

# The exact TripStage Literal (schemas/scout.py). `booked` / `done` are
# documented future "committed zone" stages but are not in the Literal, so
# not in the CHECK.
_STAGES = "('new','matching','recommended','matched','planning','plan_ready','planned')"


def upgrade() -> None:
    op.execute("""
        ALTER TABLE twm_app.trips
            ADD COLUMN stage text,
            ADD COLUMN status text,
            ADD COLUMN active_agent text
    """)
    op.execute("""
        UPDATE twm_app.trips SET
            stage = COALESCE(trip_state->>'stage', 'new'),
            status = COALESCE(trip_state->>'status', 'free'),
            active_agent = trip_state->>'active_agent'
    """)
    op.execute(f"""
        ALTER TABLE twm_app.trips
            ALTER COLUMN stage SET NOT NULL,
            ALTER COLUMN stage SET DEFAULT 'new',
            ALTER COLUMN status SET NOT NULL,
            ALTER COLUMN status SET DEFAULT 'free',
            ADD CONSTRAINT trips_stage_check CHECK (stage IN {_STAGES}),
            ADD CONSTRAINT trips_status_check CHECK (status IN ('free','committed')),
            ADD CONSTRAINT trips_active_agent_check
                CHECK (active_agent IS NULL OR active_agent IN ('scout','meridian','guide'))
    """)
    op.execute("""
        UPDATE twm_app.trips
        SET trip_state = trip_state - 'stage' - 'status' - 'active_agent'
    """)

    # matcher_recommendations: natural composite PK, drop the surrogate id.
    # Keep the DESC index — get_latest_recommendation orders by version DESC.
    op.execute("ALTER TABLE twm_app.matcher_recommendations DROP CONSTRAINT IF EXISTS matcher_recommendations_pkey")
    op.execute("ALTER TABLE twm_app.matcher_recommendations DROP CONSTRAINT IF EXISTS matcher_recommendations_trip_id_version_key")
    op.execute("ALTER TABLE twm_app.matcher_recommendations DROP COLUMN id")
    op.execute("ALTER TABLE twm_app.matcher_recommendations ADD PRIMARY KEY (trip_id, version)")

    # itinerary_versions: same, and its ASC index is now redundant with the PK.
    op.execute("ALTER TABLE twm_app.itinerary_versions DROP CONSTRAINT IF EXISTS itinerary_versions_pkey")
    op.execute("ALTER TABLE twm_app.itinerary_versions DROP CONSTRAINT IF EXISTS itinerary_versions_trip_id_version_key")
    op.execute("DROP INDEX IF EXISTS twm_app.itinerary_versions_trip_version_idx")
    op.execute("ALTER TABLE twm_app.itinerary_versions DROP COLUMN id")
    op.execute("ALTER TABLE twm_app.itinerary_versions ADD PRIMARY KEY (trip_id, version)")


def downgrade() -> None:
    op.execute("ALTER TABLE twm_app.itinerary_versions DROP CONSTRAINT itinerary_versions_pkey")
    op.execute("ALTER TABLE twm_app.itinerary_versions ADD COLUMN id uuid NOT NULL DEFAULT gen_random_uuid()")
    op.execute("ALTER TABLE twm_app.itinerary_versions ADD PRIMARY KEY (id)")
    op.execute("ALTER TABLE twm_app.itinerary_versions ADD UNIQUE (trip_id, version)")
    op.execute("CREATE INDEX itinerary_versions_trip_version_idx ON twm_app.itinerary_versions (trip_id, version ASC)")

    op.execute("ALTER TABLE twm_app.matcher_recommendations DROP CONSTRAINT matcher_recommendations_pkey")
    op.execute("ALTER TABLE twm_app.matcher_recommendations ADD COLUMN id uuid NOT NULL DEFAULT gen_random_uuid()")
    op.execute("ALTER TABLE twm_app.matcher_recommendations ADD PRIMARY KEY (id)")
    op.execute("ALTER TABLE twm_app.matcher_recommendations ADD UNIQUE (trip_id, version)")

    op.execute("""
        UPDATE twm_app.trips SET trip_state =
            trip_state
            || jsonb_build_object('stage', stage)
            || jsonb_build_object('status', status)
            || jsonb_build_object('active_agent', active_agent)
    """)
    op.execute("""
        ALTER TABLE twm_app.trips
            DROP CONSTRAINT trips_stage_check,
            DROP CONSTRAINT trips_status_check,
            DROP CONSTRAINT trips_active_agent_check,
            DROP COLUMN stage,
            DROP COLUMN status,
            DROP COLUMN active_agent
    """)
