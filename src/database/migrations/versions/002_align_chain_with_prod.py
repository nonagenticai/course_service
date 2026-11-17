"""Align chain output with prod schema (big drift).

Revision ID: 002_align_chain_with_prod
Revises: 001_initial_tables
Create Date: 2026-05-26 23:45:00.000000

Round-9 chain reconciliation — the biggest of the fleet. Querying
information_schema on the live prod DB reveals that prod uses STRING
primary keys directly (`course_id`, `module_id`, `lesson_id`,
`log_id`), no integer `id` column, and `text` for the title columns.
But `alembic upgrade head` from empty creates a different schema:
integer `id` autoincrement PKs everywhere, the string IDs as
`UNIQUE NOT NULL`, and `String(500)` for titles. Prod was bootstrapped
by `Base.metadata.create_all` from an older models.py with the
string-PK design; the chain was written later with the integer-PK
design that was never deployed.

This revision converts chain-output (integer-PK shape) to prod-shape
(string-PK shape) using `DO $$ ... END $$` blocks gated on the
existence of the integer `id` column. On prod (no `id`): every block
short-circuits, alembic_version advances. On fresh CI / test-namespace
DB (just had 001 applied): the blocks execute the full PK swap.

The text-vs-varchar drift on title columns is handled by unconditional
`ALTER COLUMN ... TYPE TEXT`, which is idempotent in Postgres (same-type
alters are no-ops, and varchar→text widens without rewriting rows
because PG stores varchar internally as text+constraint).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "002_align_chain_with_prod"
down_revision: str | None = "001_initial_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_SWAP_PK_TO_STRING = """
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='public' AND table_name='courses' AND column_name='id'
    ) THEN
        -- Drop FKs that reference courses.course_id so we can rebuild courses' PK.
        ALTER TABLE lessons DROP CONSTRAINT IF EXISTS lessons_course_id_fkey;
        ALTER TABLE modules DROP CONSTRAINT IF EXISTS modules_course_id_fkey;
        ALTER TABLE course_build_logs DROP CONSTRAINT IF EXISTS course_build_logs_course_id_fkey;

        -- Drop FKs that reference modules.module_id so we can rebuild modules' PK.
        ALTER TABLE lessons DROP CONSTRAINT IF EXISTS lessons_module_id_fkey;

        -- Drop the unique indexes 001 made on string IDs — once a column
        -- becomes a PK below, the implicit pkey index is sufficient. Doing
        -- this BEFORE the PK swap matters: FKs were depending on these
        -- indexes (Postgres picked them as the FK target's unique enforcer),
        -- so they had to be dropped first.
        DROP INDEX IF EXISTS ix_courses_course_id;
        DROP INDEX IF EXISTS ix_modules_module_id;
        DROP INDEX IF EXISTS ix_lessons_lesson_id;
        DROP INDEX IF EXISTS ix_course_build_logs_log_id;

        -- courses: drop int PK + unique on course_id, drop id col, add string PK.
        ALTER TABLE courses DROP CONSTRAINT IF EXISTS courses_pkey;
        ALTER TABLE courses DROP CONSTRAINT IF EXISTS courses_course_id_key;
        ALTER TABLE courses DROP COLUMN IF EXISTS id;
        ALTER TABLE courses ADD CONSTRAINT courses_pkey PRIMARY KEY (course_id);

        -- modules: same shape.
        ALTER TABLE modules DROP CONSTRAINT IF EXISTS modules_pkey;
        ALTER TABLE modules DROP CONSTRAINT IF EXISTS modules_module_id_key;
        ALTER TABLE modules DROP COLUMN IF EXISTS id;
        ALTER TABLE modules ADD CONSTRAINT modules_pkey PRIMARY KEY (module_id);

        -- lessons: same shape.
        ALTER TABLE lessons DROP CONSTRAINT IF EXISTS lessons_pkey;
        ALTER TABLE lessons DROP CONSTRAINT IF EXISTS lessons_lesson_id_key;
        ALTER TABLE lessons DROP COLUMN IF EXISTS id;
        ALTER TABLE lessons ADD CONSTRAINT lessons_pkey PRIMARY KEY (lesson_id);

        -- course_build_logs: same shape (PK on log_id, drop id col).
        ALTER TABLE course_build_logs DROP CONSTRAINT IF EXISTS course_build_logs_pkey;
        ALTER TABLE course_build_logs DROP CONSTRAINT IF EXISTS course_build_logs_log_id_key;
        ALTER TABLE course_build_logs DROP COLUMN IF EXISTS id;
        ALTER TABLE course_build_logs ADD CONSTRAINT course_build_logs_pkey PRIMARY KEY (log_id);

        -- Re-add FKs (now pointing at PKs).
        ALTER TABLE modules ADD CONSTRAINT modules_course_id_fkey
            FOREIGN KEY (course_id) REFERENCES courses(course_id) ON DELETE CASCADE;
        ALTER TABLE lessons ADD CONSTRAINT lessons_course_id_fkey
            FOREIGN KEY (course_id) REFERENCES courses(course_id) ON DELETE CASCADE;
        ALTER TABLE lessons ADD CONSTRAINT lessons_module_id_fkey
            FOREIGN KEY (module_id) REFERENCES modules(module_id) ON DELETE CASCADE;
        ALTER TABLE course_build_logs ADD CONSTRAINT course_build_logs_course_id_fkey
            FOREIGN KEY (course_id) REFERENCES courses(course_id) ON DELETE CASCADE;
    END IF;
END $$;
"""


def upgrade() -> None:
    op.execute(_SWAP_PK_TO_STRING)

    # Title columns: VARCHAR(500) → TEXT. Idempotent — varchar→text is a no-op
    # in Postgres beyond removing the length cap (no row rewrite).
    op.execute("ALTER TABLE courses ALTER COLUMN title TYPE TEXT")
    op.execute("ALTER TABLE modules ALTER COLUMN title TYPE TEXT")
    op.execute("ALTER TABLE lessons ALTER COLUMN title TYPE TEXT")

    # URL columns: VARCHAR(2048) → TEXT (prod has them as text).
    op.execute("ALTER TABLE courses ALTER COLUMN bundle_url TYPE TEXT")
    op.execute("ALTER TABLE lessons ALTER COLUMN video_url TYPE TEXT")
    op.execute("ALTER TABLE modules ALTER COLUMN intro_video_url TYPE TEXT")
    op.execute("ALTER TABLE modules ALTER COLUMN slide_deck_path TYPE TEXT")

    # Create the two extra non-unique indexes that prod has but chain doesn't.
    op.execute("CREATE INDEX IF NOT EXISTS idx_course_status ON courses (status)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_lesson_pipeline_status ON lessons (pipeline_status)")


def downgrade() -> None:
    # Forward-only — the chain's integer-PK shape is not a state we want to
    # return to. Pre-prod is forward-only per ~/.claude/CLAUDE.md, and prod
    # has never had the integer-PK shape, so a downgrade only matters for a
    # hypothetical fresh CI DB. Even there, we'd be undoing a corrective
    # alignment; if that's needed, write a new revision rather than relying
    # on this downgrade.
    raise NotImplementedError(
        "002_align_chain_with_prod is forward-only — prod never had the "
        "integer-PK shape, so reverting is not a supported operation."
    )
