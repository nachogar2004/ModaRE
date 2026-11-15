"""une las dos ramas de migraciones: cola del ordenador optimizador + datos del optimizador en la base

Las dos migraciones 0002 se crearon a la vez en ramas distintas a partir de 0001_inicial. Esta revisión no cambia
nada: solo las une, para que cada base aplique la que le falte (Neon ya tenía la cola; otras bases, los datos).

Revision ID: 0003_unir_cola_y_datos
Revises: 0002_cola_optimizador, 0002_datos_optimizador
Create Date: 2026-11-15 13:00:00

"""

revision = '0003_unir_cola_y_datos'
down_revision = ('0002_cola_optimizador', '0002_datos_optimizador')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
