"""Create files, verification and audit tables; seed document requirements

Generated with `alembic revision --autogenerate`, reviewed, then extended by hand:
- seed rows for verification_requirements (which documents each role uploads)
- a trigger that makes audit_logs append-only (no UPDATE, no DELETE)

Tables: files, verification_requirements, verification_requests (with a partial
unique index: at most one PENDING request per user), verification_documents,
audit_logs.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-14 16:00:00 UTC
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (role, receiver category code or None, document_type, name, description, is_required)
# Kept to the minimum needed (Technical Plan issue M7: collect as little personal data as possible).
REQUIREMENTS = [
    ("provider", None, "business_proof", "Proof of business",
     "Food authority licence, NTN certificate, or shop/trade registration", True),
    ("provider", None, "storefront_photo", "Photo of the shop front",
     "Optional: helps the administrator confirm the business location", False),
    ("receiver", "ngo", "registration_certificate", "Registration certificate",
     "NGO, trust or welfare organization registration certificate", True),
    ("receiver", "community_kitchen", "organization_proof", "Proof of the community kitchen",
     "Registration, or a letter from a recognised local body confirming the kitchen", True),
    ("receiver", "community_group", "organization_proof", "Proof of the community group",
     "Group registration, or a letter from a recognised local body", True),
    ("receiver", "individual", "identity_document", "Identity document",
     "CNIC (front side). Seen only by FreshLink administrators", True),
    ("receiver", "small_business", "business_proof", "Proof of business",
     "Shop or trade registration, or NTN certificate", True),
    ("volunteer", None, "identity_document", "Identity document",
     "CNIC (front side). Seen only by FreshLink administrators", True),
]

APPEND_ONLY_FUNCTION = """
CREATE FUNCTION audit_logs_block_changes() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_logs is append-only: % is not allowed', TG_OP;
END;
$$ LANGUAGE plpgsql
"""
APPEND_ONLY_TRIGGER = """
CREATE TRIGGER audit_logs_append_only
BEFORE UPDATE OR DELETE ON audit_logs
FOR EACH ROW EXECUTE FUNCTION audit_logs_block_changes()
"""


def _seed() -> None:
    conn = op.get_bind()
    for role, category_code, doc_type, name, description, required in REQUIREMENTS:
        conn.execute(
            sa.text(
                "INSERT INTO verification_requirements "
                "(role, receiver_category_id, document_type, name, description, is_required) "
                "VALUES (:role, (SELECT id FROM receiver_categories WHERE code = :code), "
                ":doc_type, :name, :description, :required)"
            ),
            {"role": role, "code": category_code, "doc_type": doc_type, "name": name,
             "description": description, "required": required},
        )


def upgrade() -> None:
    op.create_table('audit_logs',
    sa.Column('actor_id', sa.Uuid(), nullable=True),
    sa.Column('actor_email', sa.String(length=254), nullable=True),
    sa.Column('actor_role', sa.String(length=20), nullable=True),
    sa.Column('action', sa.String(length=60), nullable=False),
    sa.Column('entity_type', sa.String(length=40), nullable=False),
    sa.Column('entity_id', sa.Uuid(), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('before', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('after', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('ip', sa.String(length=45), nullable=True),
    sa.Column('user_agent', sa.String(length=300), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_logs'))
    )
    op.create_index(op.f('ix_audit_logs_action'), 'audit_logs', ['action'], unique=False)
    op.create_index(op.f('ix_audit_logs_actor_id'), 'audit_logs', ['actor_id'], unique=False)
    op.create_index(op.f('ix_audit_logs_created_at'), 'audit_logs', ['created_at'], unique=False)
    op.create_index(op.f('ix_audit_logs_entity_id'), 'audit_logs', ['entity_id'], unique=False)
    op.create_table('files',
    sa.Column('owner_user_id', sa.Uuid(), nullable=False),
    sa.Column('purpose', sa.Enum('verification_doc', 'listing_image', name='file_purpose', native_enum=False, create_constraint=True, length=20), nullable=False),
    sa.Column('storage_key', sa.String(length=100), nullable=False),
    sa.Column('original_name', sa.String(length=255), nullable=False),
    sa.Column('mime_type', sa.String(length=100), nullable=False),
    sa.Column('size_bytes', sa.BigInteger(), nullable=False),
    sa.Column('is_private', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], name=op.f('fk_files_owner_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_files')),
    sa.UniqueConstraint('storage_key', name=op.f('uq_files_storage_key'))
    )
    op.create_index(op.f('ix_files_owner_user_id'), 'files', ['owner_user_id'], unique=False)
    op.create_table('verification_requests',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('status', sa.Enum('pending', 'approved', 'rejected', 'info_requested', name='verification_request_status', native_enum=False, create_constraint=True, length=20), server_default='pending', nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('submitted_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('reviewed_by', sa.Uuid(), nullable=True),
    sa.Column('review_notes', sa.Text(), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], name=op.f('fk_verification_requests_reviewed_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_verification_requests_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_verification_requests'))
    )
    op.create_index(op.f('ix_verification_requests_user_id'), 'verification_requests', ['user_id'], unique=False)
    op.create_index('uq_verification_requests_one_pending_per_user', 'verification_requests', ['user_id'], unique=True, postgresql_where=sa.text("status = 'pending'"))
    op.create_table('verification_requirements',
    sa.Column('role', sa.Enum('provider', 'receiver', 'volunteer', 'admin', name='requirement_role', native_enum=False, create_constraint=True, length=20), nullable=False),
    sa.Column('receiver_category_id', sa.Uuid(), nullable=True),
    sa.Column('document_type', sa.String(length=50), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('is_required', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['receiver_category_id'], ['receiver_categories.id'], name=op.f('fk_verification_requirements_receiver_category_id_receiver_categories'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_verification_requirements'))
    )
    op.create_table('verification_documents',
    sa.Column('verification_request_id', sa.Uuid(), nullable=False),
    sa.Column('file_id', sa.Uuid(), nullable=False),
    sa.Column('document_type', sa.String(length=50), nullable=False),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['file_id'], ['files.id'], name=op.f('fk_verification_documents_file_id_files'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['verification_request_id'], ['verification_requests.id'], name=op.f('fk_verification_documents_verification_request_id_verification_requests'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_verification_documents'))
    )
    op.create_index(op.f('ix_verification_documents_verification_request_id'), 'verification_documents', ['verification_request_id'], unique=False)

    _seed()
    op.execute(APPEND_ONLY_FUNCTION)
    op.execute(APPEND_ONLY_TRIGGER)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_logs_append_only ON audit_logs")
    op.execute("DROP FUNCTION IF EXISTS audit_logs_block_changes()")
    op.drop_index(op.f('ix_verification_documents_verification_request_id'), table_name='verification_documents')
    op.drop_table('verification_documents')
    op.drop_table('verification_requirements')
    op.drop_index('uq_verification_requests_one_pending_per_user', table_name='verification_requests', postgresql_where=sa.text("status = 'pending'"))
    op.drop_index(op.f('ix_verification_requests_user_id'), table_name='verification_requests')
    op.drop_table('verification_requests')
    op.drop_index(op.f('ix_files_owner_user_id'), table_name='files')
    op.drop_table('files')
    op.drop_index(op.f('ix_audit_logs_entity_id'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_created_at'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_actor_id'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_action'), table_name='audit_logs')
    op.drop_table('audit_logs')
