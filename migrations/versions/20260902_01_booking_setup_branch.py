"""Add booking_setup branch table; drop logistics_state and itinerary_proposed_revisions (TWM-216).

`booking_setup` is the new deterministic, UI-owned scheduling branch (trip
calendar anchor, structured party, per-entity search-date preferences) —
same `trip_id` / `state jsonb` / `updated_at` shape as the other split
branch tables.

`logistics_state` held the never-wired confirmed-anchor capability, and
`itinerary_proposed_revisions` backed the itinerary revision-review flow
whose only trigger (`confirm_logistics`) is removed in the same change.
Pre-MVP: no production data, so both tables are dropped outright.
"""

from alembic import op

revision = "20260902_01"
down_revision = "20260817_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE twm_app.booking_setup (
            trip_id uuid PRIMARY KEY REFERENCES twm_app.trips(id) ON DELETE CASCADE,
            state jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(state) = 'object'),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("DROP TABLE IF EXISTS twm_app.itinerary_proposed_revisions")
    op.execute("DROP TABLE IF EXISTS twm_app.logistics_state")


def downgrade() -> None:
    op.execute("""
        CREATE TABLE twm_app.logistics_state (
            trip_id uuid PRIMARY KEY REFERENCES twm_app.trips(id) ON DELETE CASCADE,
            state jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(state) = 'object'),
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
    op.execute("DROP TABLE IF EXISTS twm_app.booking_setup")
