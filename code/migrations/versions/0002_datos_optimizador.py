"""datos del optimizador en la base: parámetros, tipos de vehículo, tarifas, distancias y columnas que faltaban

El optimizador deja de leer data/*.csv: todo sale de la base de datos. Los datos se cargan desde los CSV
(semilla) al arrancar, en gestion/basedatos.py, no aquí.

Revision ID: 0002_datos_optimizador
Revises: 0001_inicial
Create Date: 2026-11-15 12:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = '0002_datos_optimizador'
down_revision = '0001_inicial'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('parametros',
    sa.Column('id', sa.String(length=60), nullable=False),
    sa.Column('valor', sa.String(length=200), nullable=False),
    sa.Column('unidad', sa.String(length=30), nullable=True),
    sa.Column('descripcion', sa.Text(), nullable=True),
    sa.Column('etiqueta', sa.String(length=12), nullable=True),
    sa.Column('fuente', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('tipos_vehiculo',
    sa.Column('id', sa.String(length=10), nullable=False),
    sa.Column('descripcion', sa.String(length=120), nullable=False),
    sa.Column('mma_t', sa.Float(), nullable=True),
    sa.Column('carga_util_kg', sa.Float(), nullable=False),
    sa.Column('volumen_m3', sa.Float(), nullable=False),
    sa.Column('capacidad_palets', sa.Integer(), nullable=False),
    sa.Column('combustible', sa.String(length=20), nullable=True),
    sa.Column('kgco2e_km_vacio', sa.Float(), nullable=False),
    sa.Column('kgco2e_km_lleno', sa.Float(), nullable=False),
    sa.Column('etiqueta', sa.String(length=12), nullable=True),
    sa.Column('fuente', sa.Text(), nullable=True),
    sa.Column('fuente_emisiones', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('tarifas',
    sa.Column('id', sa.String(length=20), nullable=False),
    sa.Column('operador', sa.String(length=20), nullable=False),
    sa.Column('tipo_vehiculo', sa.String(length=10), nullable=False),
    sa.Column('coste_fijo_dia_eur', sa.Float(), nullable=True),
    sa.Column('coste_km_eur', sa.Float(), nullable=True),
    sa.Column('coste_hora_eur', sa.Float(), nullable=True),
    sa.Column('unidades_disponibles', sa.Integer(), nullable=True),
    sa.Column('etiqueta', sa.String(length=12), nullable=True),
    sa.Column('fuente', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['tipo_vehiculo'], ['tipos_vehiculo.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('distancias',
    sa.Column('origen', sa.String(length=24), nullable=False),
    sa.Column('destino', sa.String(length=24), nullable=False),
    sa.Column('distancia_km', sa.Float(), nullable=False),
    sa.Column('duracion_min', sa.Float(), nullable=True),
    sa.Column('duracion_camion_min', sa.Float(), nullable=False),
    sa.Column('metodo', sa.String(length=20), nullable=True),
    sa.Column('calculada', sa.Date(), nullable=True),
    sa.PrimaryKeyConstraint('origen', 'destino')
    )
    with op.batch_alter_table('almacenes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('tiempo_descarga_min', sa.Integer(), server_default='45', nullable=False))
        batch_op.add_column(sa.Column('coste_fijo_eur_semana', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('coste_manipulacion_eur_kg', sa.Float(), nullable=True))
    with op.batch_alter_table('tiendas', schema=None) as batch_op:
        batch_op.add_column(sa.Column('en_muestra', sa.Boolean(), server_default=sa.false(), nullable=False))
        batch_op.add_column(sa.Column('planta_actual', sa.String(length=16), nullable=True))


def downgrade():
    with op.batch_alter_table('tiendas', schema=None) as batch_op:
        batch_op.drop_column('planta_actual')
        batch_op.drop_column('en_muestra')
    with op.batch_alter_table('almacenes', schema=None) as batch_op:
        batch_op.drop_column('coste_manipulacion_eur_kg')
        batch_op.drop_column('coste_fijo_eur_semana')
        batch_op.drop_column('tiempo_descarga_min')
    op.drop_table('distancias')
    op.drop_table('tarifas')
    op.drop_table('tipos_vehiculo')
    op.drop_table('parametros')
