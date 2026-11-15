"""lotes: los pedidos se acumulan y se planifican cuando el admin cierra un lote (no por semanas)

- tabla lotes
- pedidos.semana_entrega pasa a admitir NULL (= pendiente de planificar) y guarda el código del lote
- datos: cada semana ya usada se convierte en un lote con el mismo código; los pedidos pendientes vuelven a la bolsa

Revision ID: 0004_lotes
Revises: 0003_unir_cola_y_datos
Create Date: 2026-11-15 14:00:00

"""
import datetime as dt
import re

from alembic import op
import sqlalchemy as sa

revision = '0004_lotes'
down_revision = '0003_unir_cola_y_datos'
branch_labels = None
depends_on = None


def _lunes(codigo: str) -> dt.date:
    if re.fullmatch(r"\d{4}-W\d{2}", codigo):
        return dt.date.fromisoformat(f"{codigo}-1")
    return dt.date.today()


def upgrade():
    op.create_table('lotes',
        sa.Column('codigo', sa.String(length=8), nullable=False),
        sa.Column('creado', sa.DateTime(), nullable=False),
        sa.Column('inicio', sa.Date(), nullable=False),
        sa.Column('creado_por_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['creado_por_id'], ['usuarios.id'], ),
        sa.PrimaryKeyConstraint('codigo'),
    )
    with op.batch_alter_table('pedidos', schema=None) as batch_op:
        batch_op.alter_column('semana_entrega', existing_type=sa.String(length=8), nullable=True)

    con = op.get_bind()
    # pedidos todavía pendientes: vuelven a la bolsa de «pendientes de planificar»
    con.execute(sa.text("UPDATE pedidos SET semana_entrega = NULL WHERE estado = 'pendiente'"))
    # cada semana que ya tenía pedidos confirmados, rutas o reposiciones pasa a ser un lote con ese código
    codigos = set()
    for tabla, col in (("pedidos", "semana_entrega"), ("viajes", "semana"), ("reposiciones", "semana")):
        codigos |= {r[0] for r in con.execute(sa.text(f"SELECT DISTINCT {col} FROM {tabla} WHERE {col} IS NOT NULL"))}
    lotes = sa.table('lotes', sa.column('codigo', sa.String), sa.column('creado', sa.DateTime), sa.column('inicio', sa.Date))
    if codigos:
        ahora = dt.datetime.now().replace(microsecond=0)
        op.bulk_insert(lotes, [{"codigo": c, "creado": ahora, "inicio": _lunes(c)} for c in sorted(codigos)])


def downgrade():
    con = op.get_bind()
    con.execute(sa.text("UPDATE pedidos SET semana_entrega = 'SINLOTE' WHERE semana_entrega IS NULL"))
    with op.batch_alter_table('pedidos', schema=None) as batch_op:
        batch_op.alter_column('semana_entrega', existing_type=sa.String(length=8), nullable=False)
    op.drop_table('lotes')
