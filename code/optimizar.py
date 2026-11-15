"""CLI: genera el plan de rutas de una semana.

    python optimizar.py                       # semana siguiente, pedidos + estimación
    python optimizar.py --semana 2026-W47 --segundos 60
    python optimizar.py --fuente estimacion --muestra --segundos 10   # prueba rápida
    python optimizar.py --asignacion actual   # compara con la asignación tienda-planta de hoy
"""
import argparse
import sys

from modare import config as C
from modare.planificador import planificar


def main():
    ap = argparse.ArgumentParser(description="Optimizador de rutas Moda re-")
    ap.add_argument("--semana", help="ISO, p.ej. 2026-W47 (por defecto: la siguiente)")
    ap.add_argument("--fuente", choices=["mixto", "pedidos", "estimacion"], default="mixto")
    ap.add_argument("--asignacion", choices=["optimo", "actual"], default="optimo")
    ap.add_argument("--plantas-futuras", action="store_true", help="Incluye Asturias y Santiago")
    ap.add_argument("--segundos", type=int, default=30, help="Tiempo de búsqueda por planta")
    ap.add_argument("--precio-co2", type=float, default=C.PRECIO_CO2_EUR_T, help="€/t CO2 en el objetivo")
    ap.add_argument("--muestra", action="store_true", help="Solo tiendas con en_muestra=1")
    ap.add_argument("--secuencial", action="store_true", help="Sin procesos en paralelo")
    ap.add_argument("--almacenes", choices=["todos", "optimo"], default="todos",
                    help="todos: un almacén por provincia, todos abiertos (por defecto); optimo: el modelo decide cuáles abrir")
    a = ap.parse_args()

    plan = planificar(a.semana, fuente=a.fuente, asignacion=a.asignacion, plantas_futuras=a.plantas_futuras,
                      segundos=a.segundos, precio_co2=a.precio_co2, solo_muestra=a.muestra,
                      paralelo=not a.secuencial, almacenes=a.almacenes)
    r = plan["resumen"]
    print(f"\n=== Plan {r['semana']} ===")
    for k in ("visitas", "almacenes_usados", "reposiciones_troncal", "eur_por_kg", "nivel_servicio",
              "nivel_servicio_troncal", "segundos_calculo"):
        print(f"  {k:22s} {r[k]}")
    print(f"\n  {'':10s} {'rutas':>6s} {'camiones':>9s} {'km':>9s} {'coste €':>10s} {'CO2 kg':>8s} {'ocup.':>6s}")
    for esc in ("troncal", "reparto"):
        k = r[esc]
        print(f"  {esc:10s} {k['rutas']:6d} {k['camiones']:9d} {k['km']:9.0f} {k['coste_eur']:10.0f} "
              f"{k['co2_kg']:8.0f} {k['ocupacion_media_cajas']:6.0%}")
    if "almacenes" in r:
        al = r["almacenes"]
        print(f"  {'almacenes':10s} {r.get('almacenes_abiertos', r['almacenes_usados']):6d} abiertos, {r['almacenes_cerrados']} cerrados "
              f"{'':>2s} {al['total_eur']:10.0f}   (fijo {al['fijo_eur']:.0f} € + manipulación {al['manipulacion_eur']:.0f} €)")
    print(f"  {'TOTAL':10s} {r['rutas']:6d} {r['camiones']:9d} {r['km']:9.0f} {r.get('coste_total_eur', r['coste_eur']):10.0f} "
          f"{r['co2_kg']:8.0f}   -> {r.get('eur_por_kg_total', r['eur_por_kg'])} €/kg")
    print("\nFlota troncal (camiones físicos = máx. rutas simultáneas):")
    for f in plan["flota"]:
        if f.get("escalon") == "troncal":
            print(f"  {f['origen']}  {f['tarifa']:10s} {f['rutas']:3d} rutas -> {f['camiones']} camiones")
    print("\nPlantas:")
    for p in plan["plantas"]:
        print(f"  {p['id']}: {p.get('almacenes', 0)} almacenes, {p['tiendas']} tiendas, "
              f"{p['kg']:.0f}/{p['capacidad']:.0f} kg")
    print("Almacenes (detalle en almacenes.csv):")
    for a in plan.get("almacenes", []):
        if not a.get("abierto", True):
            continue
        origen = " + ".join(f"{p} {k:.0f} kg" for p, k in a["plantas"].items()) if len(a.get("plantas", {})) > 1 else a["planta"]
        print(f"  {a['id']:15s} <- {origen}  {a['tiendas']:2d} tiendas  {a['kg']:6.0f}/{a['capacidad']:.0f} kg  {a.get('coste_fijo_eur', 0) + a.get('coste_manipulacion_eur', 0):5.0f} €")
    cerrados = [a["id"] for a in plan.get("almacenes", []) if not a.get("abierto", True)]
    if cerrados:
        print("  cerrados:", ", ".join(cerrados))
    if plan["no_servidas"]:
        print(f"\n{len(plan['no_servidas'])} visitas sin servir:", [x["visita"] for x in plan["no_servidas"]])
    for av in plan["avisos"]:
        print("  aviso:", av["tienda"], av["motivo"])
    avisos_rutas = [(x.get("nombre", x["id"]), x["avisos"]) for x in plan["rutas"] if x["avisos"]]
    for rid, av in avisos_rutas:
        print("  ruta", rid, av)
    print(f"\nGuardado en {C.SALIDAS_DIR / r['semana']}  (abre mapa.html para ver las rutas)")


if __name__ == "__main__":
    sys.exit(main())
