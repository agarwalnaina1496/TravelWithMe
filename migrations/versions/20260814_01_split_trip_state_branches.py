"""Split matcher/planner/itinerary/logistics branches out of trips.trip_state into dedicated tables (TWM-158).

trips.trip_state keeps only the non-touchable core fields (status, stage,
active_agent, trip_context, advisor_state) going forward; the four
touchable branches move to their own tables so a command only ever writes
the branch(es) it actually changed. itinerary_state is pointer-only
(status, current_version) — the full result for any version lives in
itinerary_versions, which is now written unconditionally for the active
version too, not only the superseded one.

The trips.trip_state column itself is left untouched by this migration —
it is not dropped. No backfill is needed: the product has not launched, so
there is no production trip_state data to migrate; every write goes
through the new split-table path from here on.
"""

from alembic import op

revision = "20260814_01"
down_revision = "20260813_02"
branch_labels = None
depends_on = None

_BLOB_TABLES = ("matcher_state", "planner_state", "logistics_state")


def upgrade() -> None:
    for table in _BLOB_TABLES:
        op.execute(f"""
            CREATE TABLE twm_app.{table} (
                trip_id uuid PRIMARY KEY REFERENCES twm_app.trips(id) ON DELETE CASCADE,
                state jsonb NOT NULL DEFAULT '{{}}'::jsonb CHECK (jsonb_typeof(state) = 'object'),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
        """)
    op.execute("""
        CREATE TABLE twm_app.itinerary_state (
            trip_id uuid PRIMARY KEY REFERENCES twm_app.trips(id) ON DELETE CASCADE,
            status text,
            current_version integer CHECK (current_version IS NULL OR current_version >= 1),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE TABLE twm_app.itinerary_proposed_revisions (
            trip_id uuid PRIMARY KEY REFERENCES twm_app.trips(id) ON DELETE CASCADE,
            base_version integer NOT NULL CHECK (base_version >= 1),
            result jsonb NOT NULL CHECK (jsonb_typeof(result) = 'object'),
            affected_days jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(affected_days) = 'array'),
            changes jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(changes) = 'array'),
            triggered_by jsonb NOT NULL CHECK (jsonb_typeof(triggered_by) = 'object'),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS twm_app.itinerary_proposed_revisions")
    op.execute("DROP TABLE IF EXISTS twm_app.itinerary_state")
    for table in _BLOB_TABLES:
        op.execute(f"DROP TABLE IF EXISTS twm_app.{table}")
