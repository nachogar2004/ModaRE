"""kg/semana de cada tienda: la demanda que reparte el optimizador (sin pedidos ni semanas)

Se rellena con la estimación por ventas en gestion/basedatos.py (las tiendas que lo tengan vacío).

Revision ID: 0003_kg_semana_tienda
Revises: 0002_datos_optimizador
Create Date: 2026-11-15 18:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = '0003_kg_semana_tienda'
down_revision = '0002_datos_optimizador'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('tiendas', schema=None) as batch_op:
        batch_op.add_column(sa.Column('kg_semana', sa.Float(), nullable=True))


def downgrade():
    with op.batch_alter_table('tiendas', schema=None) as batch_op:
        batch_op.drop_column('kg_semana')
