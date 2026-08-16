"""Add nullable trips.user_id for guest-trip claim-on-login (TWM-179).

Additive, nullable column — no backfill needed, no existing trips are
affected. Once claimed, a trip is resolved by user_id going forward; the
originating guest_session_id is kept (not deleted) but superseded as an
access path. trip_commands also gets a parallel nullable user_id so
idempotency-key lookups keep working for authenticated requests.
"""

from alembic import op

revision = "20260817_01"
down_revision = "20260816_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE twm_app.trips ADD COLUMN user_id uuid REFERENCES twm_app.users(id)")
    op.execute("CREATE INDEX trips_user_updated_idx ON twm_app.trips (user_id, updated_at DESC) WHERE user_id IS NOT NULL")
    op.execute("ALTER TABLE twm_app.trip_commands ADD COLUMN user_id uuid REFERENCES twm_app.users(id)")


def downgrade() -> None:
    op.execute("ALTER TABLE twm_app.trip_commands DROP COLUMN IF EXISTS user_id")
    op.execute("DROP INDEX IF EXISTS twm_app.trips_user_updated_idx")
    op.execute("ALTER TABLE twm_app.trips DROP COLUMN IF EXISTS user_id")
