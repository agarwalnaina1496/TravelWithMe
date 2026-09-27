"""Add first-message trip-creation idempotency (TWM-233).

`trip_commands` (the existing idempotency ledger) is keyed by `trip_id`,
which doesn't exist yet at the moment a fresh live entry's very first
message is what creates the trip -- so a client-side retry after a dropped
response (the backend already committed the trip, the client just never
saw it) had no way to be recognized as the same attempt, and would create a
second, orphaned trip.

`trips.idempotency_key` records the key the *first* command used to create
the row (NULL for every other trip-mutating path, which already has a real
trip_id to key `trip_commands` off instead). The partial unique index scopes
it to `guest_session_id` -- every request already carries one turnkey, and
an authenticated request already resolves the same trip via `user_id`
taking precedence (`_resolve_owner`), so this doesn't need `user_id` in the
key too.
"""

from alembic import op

revision = "20260925_01"
down_revision = "20260907_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE twm_app.trips ADD COLUMN idempotency_key uuid")
    op.execute("""
        CREATE UNIQUE INDEX trips_guest_idempotency_idx
        ON twm_app.trips (guest_session_id, idempotency_key)
        WHERE idempotency_key IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS twm_app.trips_guest_idempotency_idx")
    op.execute("ALTER TABLE twm_app.trips DROP COLUMN IF EXISTS idempotency_key")
