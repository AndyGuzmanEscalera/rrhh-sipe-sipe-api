"""Track RAW biometric imports and preserve their source metadata."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0002_biometric_import_batches'
down_revision = '0001_rrhh'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('biometric_syncs', sa.Column('origin', sa.String(20), nullable=False, server_default='SOURCE_API'))
    op.add_column('biometric_syncs', sa.Column('source_file_name', sa.String(255)))
    op.add_column('biometric_syncs', sa.Column('error_records', sa.Integer(), nullable=False, server_default='0'))
    for column in ('requested_from', 'requested_to'):
        op.alter_column('biometric_syncs', column, nullable=True)
    op.create_check_constraint('ck_sync_origin', 'biometric_syncs', "origin IN ('SOURCE_API','SOURCE_FILE')")
    op.create_check_constraint('ck_sync_api_range', 'biometric_syncs',
                               "origin <> 'SOURCE_API' OR (requested_from IS NOT NULL AND requested_to IS NOT NULL)")
    op.create_check_constraint('ck_sync_error_records', 'biometric_syncs', 'error_records >= 0')
    op.drop_index('uq_sync_active', table_name='biometric_syncs')
    op.create_index('uq_sync_active', 'biometric_syncs', ['device_id'], unique=True,
                    postgresql_where=sa.text("origin = 'SOURCE_API' AND status IN ('queued','running')"))
    op.add_column('biometric_records', sa.Column('source_file_name', sa.String(255)))
    op.add_column('biometric_records', sa.Column('import_batch_id', sa.Uuid()))
    op.add_column('biometric_records', sa.Column('raw_payload', JSONB()))
    op.create_foreign_key('fk_records_import_batch', 'biometric_records', 'biometric_syncs',
                          ['import_batch_id'], ['id'])
    op.create_index('ix_records_import_batch', 'biometric_records', ['import_batch_id'])
    op.execute("""
        CREATE FUNCTION rrhh_guard_raw_import_metadata() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF ROW(NEW.source_file_name, NEW.import_batch_id, NEW.raw_payload)
               IS DISTINCT FROM ROW(OLD.source_file_name, OLD.import_batch_id, OLD.raw_payload) THEN
                RAISE EXCEPTION 'Biometric RAW import metadata is immutable' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER rrhh_raw_import_metadata_guard BEFORE UPDATE ON biometric_records
        FOR EACH ROW EXECUTE FUNCTION rrhh_guard_raw_import_metadata();
    """)


def downgrade():
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM biometric_syncs WHERE origin = 'SOURCE_FILE' OR error_records > 0)
               OR EXISTS (SELECT 1 FROM biometric_records
                          WHERE import_batch_id IS NOT NULL OR source_file_name IS NOT NULL OR raw_payload IS NOT NULL) THEN
                RAISE EXCEPTION 'Downgrade would erase biometric import history or RAW metadata';
            END IF;
        END;
        $$;
    """)
    op.execute('DROP TRIGGER rrhh_raw_import_metadata_guard ON biometric_records')
    op.execute('DROP FUNCTION rrhh_guard_raw_import_metadata()')
    op.drop_index('ix_records_import_batch', table_name='biometric_records')
    op.drop_constraint('fk_records_import_batch', 'biometric_records', type_='foreignkey')
    for column in ('raw_payload', 'import_batch_id', 'source_file_name'):
        op.drop_column('biometric_records', column)
    op.drop_index('uq_sync_active', table_name='biometric_syncs')
    op.create_index('uq_sync_active', 'biometric_syncs', ['device_id'], unique=True,
                    postgresql_where=sa.text("status IN ('queued','running')"))
    for name in ('ck_sync_error_records', 'ck_sync_api_range', 'ck_sync_origin'):
        op.drop_constraint(name, 'biometric_syncs', type_='check')
    for column in ('requested_from', 'requested_to'):
        op.alter_column('biometric_syncs', column, nullable=False)
    for column in ('error_records', 'source_file_name', 'origin'):
        op.drop_column('biometric_syncs', column)
