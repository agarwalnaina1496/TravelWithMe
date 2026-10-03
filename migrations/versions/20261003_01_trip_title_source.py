"""Record who set a trip's title (TWM-234).

Meridian/Guide generate a title and the traveler can rename it; both land in
`trips.title`, so nothing could tell a generated title from one the traveler
chose. `title_source` is that marker: `placeholder` (still the default),
`generated` (promoted from an agent), or `user` (set by the traveler).
"""

from alembic import op

revision = "20261003_01"
down_revision = "20260925_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE twm_app.trips
        ADD COLUMN title_source text NOT NULL DEFAULT 'placeholder'
        CHECK (title_source IN ('placeholder', 'generated', 'user'))
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE twm_app.trips DROP COLUMN IF EXISTS title_source")
