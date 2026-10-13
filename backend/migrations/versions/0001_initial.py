"""Схема репозитория: таблицы, роли сервисов с правами и views статистики для Grafana.

Revision ID: 0001
Revises:
"""

from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Text
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SECRETS = Path("/run/secrets")
# роль в базе → файл секрета с её паролем; нет файла — роль создаётся без входа (тесты, локальный запуск)
ROLES = {"auth": "db_auth", "writer": "db_writer", "catalog": "db_catalog", "grafana_ro": "db_grafana",
         "exporter": "db_exporter", "backup": "db_backup"}

ROLE_SQL = """
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN
    EXECUTE format('CREATE ROLE %I', '{role}');
  END IF;
  IF current_setting('app.role_password') <> '' THEN
    EXECUTE format('ALTER ROLE %I LOGIN PASSWORD %L', '{role}', current_setting('app.role_password'));
  END IF;
END $$
"""

GRANTS = [
    "GRANT USAGE ON SCHEMA public TO auth, writer, catalog",
    # auth — только users
    "GRANT SELECT, INSERT, UPDATE ON users TO auth",
    # writer (admin, import) — всё, кроме users; из users — сессия и перевод учёток при слиянии людей
    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO writer",
    "REVOKE ALL ON users FROM writer",
    "GRANT SELECT (id, name, role, is_active, token_version, person_id), UPDATE (person_id) ON users TO writer",
    # catalog — только SELECT и без users
    "GRANT SELECT ON ALL TABLES IN SCHEMA public TO catalog",
    "REVOKE ALL ON users FROM catalog",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO writer",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO catalog",
    "GRANT pg_monitor TO exporter",
    "GRANT pg_read_all_data TO backup",
]

# Бизнес-цифры для Grafana. Views принадлежат владельцу схемы: grafana_ro видит только их, а не таблицы.
STATS_VIEWS = {
    "published_by_month": """
        SELECT type, date_trunc('month', published_at) AS month, count(*) AS works
        FROM public.works WHERE status = 'published' AND published_at IS NOT NULL GROUP BY 1, 2""",
    "draft_queue": """
        SELECT type, count(*) AS drafts, min(created_at) AS oldest FROM public.works WHERE status = 'draft' GROUP BY type""",
    "imports_by_week": """
        SELECT date_trunc('week', i.created_at) AS week, i.kind, i.work_type, count(DISTINCT i.id) AS imports,
               count(it.id) AS items, count(it.id) FILTER (WHERE it.status = 'error') AS errors,
               count(it.id) FILTER (WHERE it.status = 'duplicate') AS duplicates
        FROM public.imports i LEFT JOIN public.import_items it ON it.import_id = i.id GROUP BY 1, 2, 3""",
    "unmatched_authors": """
        SELECT display_name, count(DISTINCT work_id) AS works FROM public.work_people
        WHERE person_id IS NULL AND display_name <> '' GROUP BY display_name""",
    # точность ИИ: какую долю предложенных значений модератор исправил, по полю и интервалу confidence
    "ai_accuracy": """
        SELECT r.field, floor(r.confidence * 10) / 10 AS confidence_from, count(*) AS total,
               count(*) FILTER (WHERE w.field_meta -> r.field ->> 'source' = 'manual') AS corrected
        FROM public.extraction_results r JOIN public.works w ON w.id = r.work_id GROUP BY 1, 2""",
    "works_total": """
        SELECT type, status, access_level, count(*) AS works FROM public.works GROUP BY 1, 2, 3""",
}


def upgrade() -> None:
    op.create_table('classifiers',
    sa.Column('scheme', sa.Text(), nullable=False),
    sa.Column('code', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.CheckConstraint("scheme IN ('grnti', 'oecd', 'wos', 'asjc')", name=op.f('ck_classifiers_scheme')),
    sa.PrimaryKeyConstraint('scheme', 'code', name=op.f('pk_classifiers'))
    )
    op.create_table('departments',
    sa.Column('name', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('name', name=op.f('pk_departments'))
    )
    op.create_table('directions',
    sa.Column('code', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('code', name=op.f('pk_directions'))
    )
    op.create_table('imports',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('work_type', sa.Text(), nullable=False),
    sa.Column('file_name', sa.Text(), nullable=False),
    sa.Column('meta_table_name', sa.Text(), nullable=True),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.Text(), nullable=False),
    sa.CheckConstraint("kind IN ('archive', 'table')", name=op.f('ck_imports_kind')),
    sa.CheckConstraint("status IN ('uploading', 'processing', 'done', 'failed')", name=op.f('ck_imports_status')),
    sa.CheckConstraint("work_type IN ('vkr', 'coursework', 'publication', 'talk')", name=op.f('ck_imports_work_type')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_imports'))
    )
    op.create_table('journals',
    sa.Column('issn', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('lists', postgresql.ARRAY(Text()), nullable=False),
    sa.PrimaryKeyConstraint('issn', name=op.f('pk_journals'))
    )
    op.create_table('people',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('full_name', sa.Text(), nullable=False),
    sa.Column('department', sa.Text(), nullable=True),
    sa.Column('position', sa.Text(), nullable=True),
    sa.Column('academic_degree', sa.Text(), nullable=True),
    sa.Column('academic_title', sa.Text(), nullable=True),
    sa.Column('orcid', sa.Text(), nullable=True),
    sa.Column('affiliation', sa.Text(), nullable=True),
    sa.Column('photo_key', sa.Text(), nullable=True),
    sa.Column('merged_into', sa.Uuid(), nullable=True),
    sa.CheckConstraint("kind IN ('staff', 'student', 'graduate', 'external')", name=op.f('ck_people_kind')),
    sa.ForeignKeyConstraint(['department'], ['departments.name'], name=op.f('fk_people_department_departments'), onupdate='CASCADE'),
    sa.ForeignKeyConstraint(['merged_into'], ['people.id'], name=op.f('fk_people_merged_into_people')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_people'))
    )
    op.create_table('person_names',
    sa.Column('person_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('exact_key', sa.Text(), nullable=False),
    sa.Column('short_key', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['person_id'], ['people.id'], name=op.f('fk_person_names_person_id_people'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('person_id', 'name', name=op.f('pk_person_names'))
    )
    op.create_index('ix_person_names_exact_key', 'person_names', ['exact_key'], unique=False)
    op.create_index('ix_person_names_search', 'person_names', [sa.literal_column("to_tsvector('simple', replace(lower(name), 'ё', 'е'))")], unique=False, postgresql_using='gin')
    op.create_index('ix_person_names_short_key', 'person_names', ['short_key'], unique=False)
    op.create_table('users',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('login', sa.Text(), nullable=False),
    sa.Column('password_hash', sa.Text(), nullable=False),
    sa.Column('role', sa.Text(), nullable=False),
    sa.Column('person_id', sa.Uuid(), nullable=True),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('token_version', sa.Integer(), nullable=False),
    sa.CheckConstraint("role IN ('moderator', 'admin', 'author')", name=op.f('ck_users_role')),
    sa.ForeignKeyConstraint(['person_id'], ['people.id'], name=op.f('fk_users_person_id_people')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('login', name=op.f('uq_users_login'))
    )
    op.create_table('works',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('type', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('title_key', sa.Text(), nullable=False),
    sa.Column('year', sa.Integer(), nullable=True),
    sa.Column('department', sa.Text(), nullable=True),
    sa.Column('annotation', sa.Text(), nullable=True),
    sa.Column('keywords', postgresql.ARRAY(Text()), nullable=False),
    sa.Column('language', sa.Text(), nullable=False),
    sa.Column('access_level', sa.Text(), nullable=False),
    sa.Column('consent', sa.Boolean(), nullable=False),
    sa.Column('external_url', sa.Text(), nullable=True),
    sa.Column('doi', sa.Text(), nullable=True),
    sa.Column('issn', sa.Text(), nullable=True),
    sa.Column('details', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.Column('field_meta', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.Column('file_key', sa.Text(), nullable=True),
    sa.Column('original_file_name', sa.Text(), nullable=True),
    sa.Column('related_id', sa.Uuid(), nullable=True),
    sa.Column('duplicate_of', sa.Uuid(), nullable=True),
    sa.Column('import_id', sa.Uuid(), nullable=True),
    sa.Column('reject_reason', sa.Text(), nullable=True),
    sa.Column('search', postgresql.TSVECTOR(), nullable=True),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.CheckConstraint("access_level <> 'full' OR consent OR type IN ('publication', 'talk')", name=op.f('ck_works_consent')),
    sa.CheckConstraint("access_level IN ('full', 'card_only', 'hidden')", name=op.f('ck_works_access_level')),
    sa.CheckConstraint("language IN ('ru', 'en', 'other')", name=op.f('ck_works_language')),
    sa.CheckConstraint("status IN ('draft', 'published', 'rejected')", name=op.f('ck_works_status')),
    sa.CheckConstraint("type IN ('vkr', 'coursework', 'publication', 'talk')", name=op.f('ck_works_type')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_works_created_by_users')),
    sa.ForeignKeyConstraint(['department'], ['departments.name'], name=op.f('fk_works_department_departments'), onupdate='CASCADE'),
    sa.ForeignKeyConstraint(['duplicate_of'], ['works.id'], name=op.f('fk_works_duplicate_of_works'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['import_id'], ['imports.id'], name=op.f('fk_works_import_id_imports')),
    sa.ForeignKeyConstraint(['related_id'], ['works.id'], name=op.f('fk_works_related_id_works'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_works'))
    )
    op.create_index('ix_works_doi', 'works', [sa.literal_column('lower(doi)')], unique=False)
    op.create_index('ix_works_search', 'works', ['search'], unique=False, postgresql_using='gin')
    op.create_index('ix_works_status_type_year', 'works', ['status', 'type', 'year'], unique=False)
    op.create_index('ix_works_type_title_key_year', 'works', ['type', 'title_key', 'year'], unique=False)
    op.create_table('extraction_results',
    sa.Column('work_id', sa.Uuid(), nullable=False),
    sa.Column('field', sa.Text(), nullable=False),
    sa.Column('value', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.ForeignKeyConstraint(['work_id'], ['works.id'], name=op.f('fk_extraction_results_work_id_works'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('work_id', 'field', name=op.f('pk_extraction_results'))
    )
    op.create_table('import_items',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('import_id', sa.Uuid(), nullable=False),
    sa.Column('n', sa.Integer(), nullable=False),
    sa.Column('ref', sa.Text(), nullable=False),
    sa.Column('title', sa.Text(), nullable=True),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('work_id', sa.Uuid(), nullable=True),
    sa.Column('duplicate_of', sa.Uuid(), nullable=True),
    sa.Column('unmatched_authors', postgresql.ARRAY(Text()), nullable=False),
    sa.Column('file_key', sa.Text(), nullable=True),
    sa.Column('meta', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.CheckConstraint("status IN ('pending', 'extracted', 'filled', 'duplicate', 'error')", name=op.f('ck_import_items_status')),
    sa.ForeignKeyConstraint(['duplicate_of'], ['works.id'], name=op.f('fk_import_items_duplicate_of_works')),
    sa.ForeignKeyConstraint(['import_id'], ['imports.id'], name=op.f('fk_import_items_import_id_imports'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['work_id'], ['works.id'], name=op.f('fk_import_items_work_id_works')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_import_items')),
    sa.UniqueConstraint('import_id', 'n', name=op.f('uq_import_items_import_id_n'))
    )
    op.create_table('materials',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('work_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('size', sa.Integer(), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('file_key', sa.Text(), nullable=False),
    sa.Column('uploaded_by', sa.Uuid(), nullable=True),
    sa.Column('uploaded_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('decided_by', sa.Uuid(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("kind IN ('text', 'slides', 'data', 'other')", name=op.f('ck_materials_kind')),
    sa.CheckConstraint("status IN ('pending', 'approved', 'rejected')", name=op.f('ck_materials_status')),
    sa.ForeignKeyConstraint(['decided_by'], ['users.id'], name=op.f('fk_materials_decided_by_users')),
    sa.ForeignKeyConstraint(['uploaded_by'], ['users.id'], name=op.f('fk_materials_uploaded_by_users')),
    sa.ForeignKeyConstraint(['work_id'], ['works.id'], name=op.f('fk_materials_work_id_works')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_materials'))
    )
    op.create_index('ix_materials_work_id', 'materials', ['work_id'], unique=False)
    op.create_table('work_citations',
    sa.Column('work_id', sa.Uuid(), nullable=False),
    sa.Column('source', sa.Text(), nullable=False),
    sa.Column('count', sa.Integer(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['work_id'], ['works.id'], name=op.f('fk_work_citations_work_id_works'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('work_id', 'source', name=op.f('pk_work_citations'))
    )
    op.create_table('work_people',
    sa.Column('work_id', sa.Uuid(), nullable=False),
    sa.Column('role', sa.Text(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('person_id', sa.Uuid(), nullable=True),
    sa.Column('display_name', sa.Text(), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.CheckConstraint("kind IN ('staff', 'student', 'graduate', 'external')", name=op.f('ck_work_people_kind')),
    sa.CheckConstraint("role IN ('author', 'supervisor', 'presenter')", name=op.f('ck_work_people_role')),
    sa.ForeignKeyConstraint(['person_id'], ['people.id'], name=op.f('fk_work_people_person_id_people')),
    sa.ForeignKeyConstraint(['work_id'], ['works.id'], name=op.f('fk_work_people_work_id_works'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('work_id', 'role', 'position', name=op.f('pk_work_people'))
    )
    op.create_index('ix_work_people_person_id', 'work_people', ['person_id'], unique=False)
    op.create_index('ix_work_people_unmatched', 'work_people', ['display_name'], unique=False, postgresql_where=sa.text('person_id IS NULL'))
    op.create_table('work_subjects',
    sa.Column('work_id', sa.Uuid(), nullable=False),
    sa.Column('scheme', sa.Text(), nullable=False),
    sa.Column('code', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['scheme', 'code'], ['classifiers.scheme', 'classifiers.code'], name=op.f('fk_work_subjects_scheme_classifiers'), onupdate='CASCADE'),
    sa.ForeignKeyConstraint(['work_id'], ['works.id'], name=op.f('fk_work_subjects_work_id_works'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('work_id', 'scheme', 'code', name=op.f('pk_work_subjects'))
    )
    op.create_index('ix_work_subjects_scheme_code', 'work_subjects', ['scheme', 'code'], unique=False)

    connection = op.get_bind()
    for role, secret in ROLES.items():
        path = SECRETS / secret
        password = path.read_text().strip() if path.exists() else ""
        connection.execute(sa.text("SELECT set_config('app.role_password', :pw, true)"), {"pw": password})
        op.execute(ROLE_SQL.format(role=role))
    for grant in GRANTS:
        op.execute(grant)
    op.execute("CREATE SCHEMA stats")
    for name, query in STATS_VIEWS.items():
        op.execute(f"CREATE VIEW stats.{name} AS {query}")
    op.execute("GRANT USAGE ON SCHEMA stats TO grafana_ro")
    op.execute("GRANT SELECT ON ALL TABLES IN SCHEMA stats TO grafana_ro")
    op.execute("ALTER ROLE grafana_ro SET statement_timeout = '30s'")  # тяжёлая панель не повесит базу


def downgrade() -> None:
    op.execute("DROP SCHEMA stats CASCADE")
    for table in ("extraction_results", "materials", "work_citations", "work_subjects", "work_people", "import_items",
                  "works", "imports", "users", "person_names", "people", "journals", "directions", "departments", "classifiers"):
        op.drop_table(table)
