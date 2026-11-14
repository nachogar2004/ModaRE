"""Simulación detallada de cada ruta devuelta por el solver.

El solver trabaja con aproximaciones lineales (pausas prorrateadas, jornada acumulada).
Aquí se recorre la ruta minuto a minuto con reglas exactas:
  * pausa de 20 min al cumplir cada 200 km de conducción
  * máximo 9 h de conducción y 13 h de jornada por día -> descanso de 11 h (pernocta)
  * si se llega antes de la ventana se espera; si se llega tarde, se espera al siguiente día permitido
  * consumo y CO2 interpolados según la carga real de cada tramo (vacío ↔ lleno)
y se calculan los costes facturables según la tarifa de cada operador.
"""
from __future__ import annotations

import math

from . import config as C
from .datos import Datos, min_a_hhmm


class Conductor:
    def __init__(self, P: dict, t0: float):
        self.P = P
        self.t = t0
        self.inicio_dia = t0
        self.cond_hoy = 0.0
        self.km_sin_pausa = 0.0
        self.noches = 0
        self.pausas = 0
        self.min_trabajo = 0.0   # tiempo facturable (sin descansos nocturnos)
        self.log = []
        self.descansos = []      # [(inicio, fin)] en minutos de la semana (para el cronograma)

    @property
    def jornada(self):
        return self.t - self.inicio_dia

    def _descanso(self):
        self.log.append((self.t, "descanso nocturno"))
        self.descansos.append((self.t, self.t + C.DESCANSO_NOCTURNO_MIN))
        self.t += C.DESCANSO_NOCTURNO_MIN
        self.inicio_dia, self.cond_hoy, self.km_sin_pausa = self.t, 0.0, 0.0
        self.noches += 1

    def avanzar(self, minutos: float, trabajo: bool = True):
        self.t += minutos
        if trabajo:
            self.min_trabajo += minutos

    def conducir(self, km: float, minutos: float):
        if km <= 0:
            return
        vel = minutos / km                     # min/km
        max_cond = self.P["conduccion_max_dia_h"] * 60
        max_jor = self.P["jornada_max_h"] * 60
        while km > 1e-6:
            if self.km_sin_pausa >= C.PAUSA_CADA_KM - 1e-6:
                self.avanzar(C.PAUSA_MIN)
                self.pausas += 1
                self.km_sin_pausa = 0.0
            tramo = min(km, C.PAUSA_CADA_KM - self.km_sin_pausa)
            m = tramo * vel
            margen = min(max_cond - self.cond_hoy, max_jor - self.jornada)
            if m > margen:                     # no cabe hoy: conduce lo que pueda y descansa
                if margen > 1:
                    parcial = margen / vel
                    self.avanzar(margen)
                    self.cond_hoy += margen
                    self.km_sin_pausa += parcial
                    km -= parcial
                self._descanso()
                continue
            self.avanzar(m)
            self.cond_hoy += m
            self.km_sin_pausa += tramo
            km -= tramo

    def esperar_hasta(self, t_obj: float):
        espera = t_obj - self.t
        if espera <= 0:
            return
        max_jor = self.P["jornada_max_h"] * 60
        if espera >= C.DESCANSO_NOCTURNO_MIN or self.jornada + espera > max_jor:
            # la espera se convierte en descanso nocturno: empieza un día nuevo al abrir la tienda
            self.log.append((self.t, "descanso nocturno"))
            self.descansos.append((self.t, t_obj))
            self.t = t_obj
            self.inicio_dia, self.cond_hoy, self.km_sin_pausa = self.t, 0.0, 0.0
            self.noches += 1
        else:
            self.avanzar(espera)           # espera en jornada: se factura


def simular_ruta(d: Datos, planta: str, ruta: dict) -> dict:
    P, veh = d.P, ruta["vehiculo"]
    H = int(P["horizonte_planificacion_dias"])
    carga_min = P["tiempo_carga_planta_min"]
    paradas = ruta["paradas"]
    carga_kg = sum(p["visita"].kg for p in paradas)
    cond = Conductor(P, ruta["salida"])
    cond.avanzar(carga_min)
    salida_real = cond.t

    km_tot = litros = co2 = 0.0
    km_cargado = 0.0
    detalle, previo, ok_kg, tarde_kg = [], planta, 0.0, 0.0
    ultima_entrega = cond.t

    def tramo(a, b, kg_a_bordo):
        nonlocal km_tot, litros, co2, km_cargado
        km, mins = d.dist(a, b)
        f = min(1.0, kg_a_bordo / veh.cap_kg)
        e = veh.co2_vacio + (veh.co2_lleno - veh.co2_vacio) * f
        km_tot += km
        km_cargado += km * kg_a_bordo
        co2 += km * e
        litros += km * e / C.KG_CO2E_POR_LITRO_DIESEL
        cond.conducir(km, mins)
        return km

    for p in paradas:
        v = p["visita"]
        km = tramo(previo, v.tienda, carga_kg)
        llegada = cond.t
        ventanas = [(dd * 1440 + v.ini, dd * 1440 + v.fin - v.descarga) for dd in v.dias]
        destino = next(((max(a, cond.t), b) for a, b in ventanas if cond.t <= b), None)
        a_tiempo = destino is not None
        if a_tiempo:
            cond.esperar_hasta(destino[0])
        inicio_desc = cond.t
        cond.avanzar(v.descarga)
        ultima_entrega = cond.t
        carga_kg -= v.kg
        if a_tiempo:
            ok_kg += v.kg
        else:
            tarde_kg += v.kg
        detalle.append({
            "visita": v.id, "tienda": v.tienda, "kg": v.kg, "cajas": v.cajas, "km_tramo": round(km, 1),
            "llegada": min_a_hhmm(llegada), "inicio_descarga": min_a_hhmm(inicio_desc),
            "ventana": f"{''.join(C.DIAS[x] for x in v.dias)} {v.ini // 60:02d}:{v.ini % 60:02d}-{v.fin // 60:02d}:{v.fin % 60:02d}",
            "a_tiempo": a_tiempo, "t_abs": inicio_desc,
        })
        previo = v.tienda
    tramo(previo, planta, 0.0)  # vuelta en vacío (la logística inversa sería carga aquí)
    vuelta = cond.t

    dias_ruta = math.floor((ultima_entrega - ruta["salida"]) / 1440) + 1
    dias_fact = cond.noches + 1
    horas = cond.min_trabajo / 60
    coste_km = km_tot * veh.coste_km
    coste_h = horas * veh.coste_hora
    coste_fijo = veh.coste_fijo_dia * dias_fact
    coste_pern = cond.noches * P["coste_pernocta_eur"]
    avisos = []
    if dias_ruta > int(P["dias_max_ruta"]):
        avisos.append(f"Ruta de {dias_ruta} días > dias_max_ruta")
    if vuelta > (H + 1) * 1440:
        avisos.append("Vuelve a planta después del sábado")
    if tarde_kg:
        avisos.append(f"{tarde_kg:.0f} kg fuera de ventana")

    return {
        "id": veh.id, "planta": planta, "tipo": veh.tipo, "operador": veh.operador, "tarifa": veh.tarifa,
        "salida": min_a_hhmm(salida_real), "vuelta": min_a_hhmm(vuelta),
        "t_salida": ruta["salida"], "t_vuelta": vuelta,
        "paradas": detalle, "n_paradas": len(detalle),
        "kg": round(sum(p["kg"] for p in detalle), 1), "cajas": sum(p["cajas"] for p in detalle),
        "ocupacion_kg": round(sum(p["kg"] for p in detalle) / veh.cap_kg, 3),
        "ocupacion_cajas": round(sum(p["cajas"] for p in detalle) / veh.cap_cajas, 3),
        "km": round(km_tot, 1), "horas_trabajo": round(horas, 2), "pausas_20min": cond.pausas,
        "noches": cond.noches, "dias_ruta": dias_ruta,
        "litros": round(litros, 1), "l_100km": round(100 * litros / km_tot, 1) if km_tot else 0,
        "co2_kg": round(co2, 1), "t_km": round(km_cargado / 1000, 1),
        "coste_eur": round(coste_km + coste_h + coste_fijo + coste_pern, 2),
        "desglose_coste": {"km": round(coste_km, 2), "horas": round(coste_h, 2),
                           "fijo": round(coste_fijo, 2), "pernoctas": round(coste_pern, 2)},
        "coste_combustible_eur_info": round(litros * C.PRECIO_GASOLEO_EUR_L, 2),
        "kg_a_tiempo": ok_kg, "kg_tarde": tarde_kg, "avisos": avisos,
        "descansos": [[round(a), round(b)] for a, b in cond.descansos],
    }


def flota_necesaria(rutas: list[dict]) -> list[dict]:
    """Camiones físicos = máximo de rutas solapadas en el tiempo por (depósito, tarifa).
    Un mismo camión puede hacer una ruta el lunes y otra el martes."""
    grupos: dict[tuple, list] = {}
    for r in rutas:
        grupos.setdefault((r.get("escalon", "reparto"), r.get("origen", r["planta"]), r["tarifa"], r["tipo"]),
                          []).append((r["t_salida"], r["t_vuelta"]))
    out = []
    for (esc, pl, tar, tipo), iv in sorted(grupos.items()):
        ev = sorted([(a, 1) for a, _ in iv] + [(b, -1) for _, b in iv], key=lambda x: (x[0], x[1]))
        cur = pico = 0
        for _, s in ev:
            cur += s
            pico = max(pico, cur)
        out.append({"escalon": esc, "origen": pl, "planta": pl, "tarifa": tar, "tipo": tipo, "rutas": len(iv), "camiones": pico})
    return out
