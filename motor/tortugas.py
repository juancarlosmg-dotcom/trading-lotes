"""lotes/motor/tortugas.py -- F227: Turtle System 2 (sealed) as F222 work units.

WHAT IT IS: pure computation, numpy + standard library ONLY (lotes/README.md s.2).
It reads ONE .npz per panel from the lot's own data directory and returns ONE 1-D
float64 array per work unit.

THE RULE IT IMPLEMENTS is docs/PREREGISTRO_TORTUGAS_SISTEMA2_v2.md, seal sha256
b344e266d7c087d084161b0eda3c89cb5d06d161e88281b805f2e793df5ac0d1 (v2 s.1 binds
the rule text of v1 s.2 verbatim):

  entry long   close(t) > max(high(t-55..t-1))     fill AT THE CLOSE OF t
  entry short  close(t) < min(low (t-55..t-1))     fill AT THE CLOSE OF t
  N            TR = max(H-L, H-PDC, PDC-L); N = (19*PDN + TR)/20, seeded with the
               simple 20-day mean of TR; FROZEN at the entry day of the FIRST
               unit of the position (sizing, 0,5N spacing and the 2N stop). The N
               used for the SIZE of a fill is the N OF THE BAR THAT FILLS, never
               a later one (pinned by T5c and by mutant X2).
  size         qty = 0,01 * equity / N
  pyramid      +1 unit every +0,5N (long) / -0,5N (short) from the previous fill,
               up to 4 units
  stop         2N from the entry fill; EVERY ADD MOVES ALL STOPS BY EXACTLY +0,5N
               (long) / -0,5N (short). THIS IS THE SEAL'S OWN WORDING, v2 s.1
               l.31-34: "the stop only moves when a UNIT IS ADDED, and it moves
               exactly 0,5N". Because an add FILLS AT THE CLOSE (which can be
               beyond the +0,5N trigger), the resulting stop is <= 2N from the
               last fill; the alternative reading ("2N from the last fill", the
               pass-1 implementation) is the tighter one and is NOT run. DECLARED
               in the spec section `infraespecificaciones_declaradas`.
  exit         close(t) < min(low(t-20..t-1)) long / > max(high(t-20..t-1)) short
  cap          gross notional of open units <= 1,0 x equity; a unit that would
               breach it is NOT opened and is COUNTED (v2 s.2.5)

FUNDING FALLBACK LADDER (v2 s.3, seal l.182-185), WIRED, COUNTED AND PUBLISHED.
For a position symbol-day WITHOUT a funding observation, in this order:
  rung 1  the symbol's OWN MEDIAN daily funding, if it has >= `funding_min_obs`
          observations (30). Signed, as sealed.
  rung 2  the symbol's OWN p90 of |daily funding|, charged PESSIMISTICALLY: the
          sign is always a COST to the side the position is on.
  rung 3  the PANEL's p90 of |daily funding| OF THAT DAY, same pessimistic sign.
  rung 3b the panel's p90 over the whole sample, when no symbol has an
          observation that day. Counted apart.
  none of them available -> ErrorTortugas. There is no zero rung: charging zero
  is exactly the "fallback (b) zeroed" scenario the seal calls a REFUTATION
  CONDITION, not a primary run.
Each rung is counted per unit and published in the header (slots 32-35).

THE INDEX OF THE UNIT, WITHOUT TOUCHING nucleo.py. lotes/motor/nucleo.py is
PINNED BY THE F222 PILOT SEAL (lotes/piloto/spec_piloto_sintetico.json,
motor/nucleo.py sha256 9414975f...), so this lot does not edit it: a lot that
breaks the seal of another lot is not a lot, it is a regression. nucleo calls
`calculo(rng_unidad(seed_master, i), parametros)`; the index is recovered from
the RNG ITSELF, matching its PCG64 state against the states of the U units
derived from the sealed `seed_master` (which travels inside `parametros` too and
is asserted equal to the spec's by the spec writer). The map is deterministic,
pure and pinned by T24b; a miss RAISES.

THE RUNNER MUST IMPORT THIS MODULE (`from lotes.motor import tortugas`, or
`from motor import tortugas` in the Colab mirror) before calling correr_lote:
importing it registers `tortugas_sistema2` in nucleo.CALCULOS.
scripts/aceptar_lote.py does it for the VPS gate. If it is not imported, nucleo's
own valida_spec refuses the spec by name -- loudly, never silently.

TWO THINGS THE SEAL DOES NOT FIX AND THIS FILE PINS (also in the spec):
  P1 `adds_por_barra = 1`: at most ONE add per bar, filled at the close.
  P2 `funding_ambos_lados = true`: funding is a cash flow of BOTH legs with its
     real sign. CONSEQUENCE: C3 ("no-funding twin expected BETTER") only holds if
     net funding is a cost over the sample; what PROVES funding is applied is
     that the twin DIFFERS, and both numbers travel together.

DETERMINISM: the simulation RNG is SeedSequence([seed, panel_code, variant_code]),
never the unit index nor the clock.

NO SILENT ZERO. sd == 0 does not produce Sharpe 0,0: it produces NaN plus the
`degenerada` flag, and a null replica in that state is marked INVALIDA and
counted. A bootstrap replica that drops symbols counts them and publishes the
count per replica. The aggregate REFUSES (raises) on a short common window
instead of returning zeros.

REDUCTIONS use math.fsum (nucleo.py's CROSS-MACHINE note).
"""
from __future__ import annotations

import math
import os
from pathlib import Path

import numpy as np

VERSION_TORTUGAS = "f227.2"
LAYOUT = 227.2

#: header slots of the returned array; index == position.
#: BODY, in this order: pnl_simbolo(S), pnl_largo(S), bajo_lote_simbolo(S),
#: bajo_minimo_simbolo(S), retornos_diarios(n_dias). `bloques_por_simbolo` = 4.
CABECERA = (
    "layout", "unidad", "tipo", "panel", "variante", "semilla", "n_replicas",
    "n_dias", "sharpe_anual", "max_dd", "retorno_total", "n_trades", "n_entradas",
    "unidades_bajo_lote", "unidades_bajo_minimo", "unidades_rechazadas_por_cap",
    "n_adds", "n_salidas_stop", "n_salidas_20d", "n_salidas_deslistada",
    "coste_total_usd", "financiacion_usd", "mediana_riesgo_unidad_usd",
    "n_simbolos", "n_cabecera", "anualizacion", "exposicion_bruta_media",
    "pct_pnl_simbolo_top", "anios", "t_stat", "cobertura_funding",
    "trades_bajo_20",
    # F227 pase 2
    "fallback_mediana_propia", "fallback_p90_propio", "fallback_p90_dia",
    "fallback_p90_panel", "entradas_sorteadas_c1", "bloques_por_simbolo",
    "campos_por_replica", "degenerada", "simbolos_descartados_nulo",
)
N_CAB = len(CABECERA)
BLOQUES_POR_SIMBOLO = 4
CAMPOS_POR_REPLICA = 5          # (sharpe, maxdd, trades, descartados, invalida)

PANEL_COD = {"a": 1.0, "b": 2.0, "c": 3.0, "agregado": 4.0}
#: no_borrow y no_funding YA NO comparten codigo (revision f227 c.8.i): el codigo
#: de variante identifica la celda por si solo y ademas siembra su propio RNG.
VARIANTE_COD = {
    "main": 0.0, "random_entry": 1.0, "long_only": 2.0,
    "no_borrow": 3.0, "no_graveyard": 4.0, "no_funding": 5.0,
}


class ErrorTortugas(Exception):
    """Every refusal of this module. Loud, never a silent default."""


# --------------------------------------------------------------------------- #
# nucleo (read-only: this module NEVER modifies nucleo.py)
# --------------------------------------------------------------------------- #
_NUCLEO = None


def nucleo_modulo():
    """The F222 nucleo, found under any of the layouts this file runs in."""
    global _NUCLEO
    if _NUCLEO is not None:
        return _NUCLEO
    intentos = []
    try:
        from . import nucleo as n                      # package import
        _NUCLEO = n
        return n
    except Exception as exc:                            # noqa: BLE001
        intentos.append("relativo: " + repr(exc))
    for mod in ("lotes.motor.nucleo", "motor.nucleo"):
        try:
            _NUCLEO = __import__(mod, fromlist=["nucleo"])
            return _NUCLEO
        except Exception as exc:                        # noqa: BLE001
            intentos.append(mod + ": " + repr(exc))
    ruta = Path(__file__).resolve().parent / "nucleo.py"
    if ruta.is_file():
        import importlib.util
        esp = importlib.util.spec_from_file_location("nucleo_para_tortugas", ruta)
        n = importlib.util.module_from_spec(esp)
        esp.loader.exec_module(n)
        _NUCLEO = n
        return n
    raise ErrorTortugas("no encuentro lotes/motor/nucleo.py: " + " | ".join(intentos))


# --------------------------------------------------------------------------- #
# data
# --------------------------------------------------------------------------- #
def raiz_datos(par):
    env = os.environ.get("LOTE_DATOS_TORTUGAS")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / par.get("datos_dir", "datos/tortugas")


_CACHE = {}


def cargar_panel(par, panel):
    ruta = raiz_datos(par) / (par["paneles"][panel]["fichero"])
    clave = str(ruta)
    if clave in _CACHE:
        return _CACHE[clave]
    if not ruta.is_file():
        raise ErrorTortugas("falta el fichero de datos del panel " + panel + ": " + clave)
    z = np.load(ruta, allow_pickle=False)
    d = {k: z[k] for k in z.files}
    for req in ("open", "high", "low", "close", "fechas", "simbolos", "ini", "fin"):
        if req not in d:
            raise ErrorTortugas("panel " + panel + ": falta el array " + req)
    d["T"], d["S"] = d["close"].shape
    _CACHE[clave] = d
    return d


# --------------------------------------------------------------------------- #
# indicators
# --------------------------------------------------------------------------- #
def atr_wilder(h, l, c):
    """N of the published rules: TR = max(H-L, H-PDC, PDC-L); N = (19*PDN+TR)/20,
    seeded with the SIMPLE 20-day mean of TR. 1-D, one symbol. NaN until seeded."""
    n = len(c)
    out = np.full(n, np.nan)
    if n < 21:
        return out
    tr = np.empty(n)
    tr[0] = np.nan
    hh, ll, pc = h[1:], l[1:], c[:-1]
    tr[1:] = np.maximum(hh - ll, np.maximum(np.abs(hh - pc), np.abs(pc - ll)))
    prev = float(math.fsum(tr[1:21].tolist()) / 20.0)
    out[20] = prev
    for k in range(21, n):
        prev = (19.0 * prev + float(tr[k])) / 20.0
        out[k] = prev
    return out


def extremos_previos(x, w):
    """(max, min) of the w bars BEFORE each bar; bar k never sees itself."""
    n = len(x)
    mx = np.full(n, np.nan)
    mn = np.full(n, np.nan)
    if n <= w:
        return mx, mn
    vent = np.lib.stride_tricks.sliding_window_view(x, w)
    mx[w:] = vent.max(axis=1)[: n - w]
    mn[w:] = vent.min(axis=1)[: n - w]
    return mx, mn


def prepara_indicadores(d, entrada=55, salida=20):
    T, S = d["T"], d["S"]
    N = np.full((T, S), np.nan)
    hh_e = np.full((T, S), np.nan)
    ll_e = np.full((T, S), np.nan)
    hh_s = np.full((T, S), np.nan)
    ll_s = np.full((T, S), np.nan)
    for s in range(S):
        i0, i1 = int(d["ini"][s]), int(d["fin"][s])
        if i1 - i0 < 60:
            continue
        sl = slice(i0, i1 + 1)
        h, l, c = d["high"][sl, s], d["low"][sl, s], d["close"][sl, s]
        N[sl, s] = atr_wilder(h, l, c)
        a, b = extremos_previos(h, entrada)
        hh_e[sl, s] = a
        a, b = extremos_previos(l, entrada)
        ll_e[sl, s] = b
        a, b = extremos_previos(h, salida)
        hh_s[sl, s] = a
        a, b = extremos_previos(l, salida)
        ll_s[sl, s] = b
    return {"N": N, "hh_e": hh_e, "ll_e": ll_e, "hh_s": hh_s, "ll_s": ll_s}


def dias_calendario(fechas):
    y = fechas // 10000
    m = (fechas // 100) % 100
    dd = fechas % 100
    jul = np.array(
        [_juliano(int(a), int(b), int(c)) for a, b, c in zip(y, m, dd)], dtype="f8"
    )
    dif = np.empty(len(jul))
    dif[0] = 1.0
    dif[1:] = np.diff(jul)
    return dif


def _juliano(y, m, d):
    a = (14 - m) // 12
    yy = y + 4800 - a
    mm = m + 12 * a - 3
    return d + (153 * mm + 2) // 5 + 365 * yy + yy // 4 - yy // 100 + yy // 400 - 32045


# --------------------------------------------------------------------------- #
# the simulation
# --------------------------------------------------------------------------- #
class Posicion(object):
    __slots__ = ("lado", "N", "qty", "fills", "stop", "ultimo", "coste_base")

    def __init__(self, lado, N):
        self.lado = lado
        self.N = N
        self.qty = 0.0
        self.fills = []
        self.stop = 0.0
        self.ultimo = 0.0
        self.coste_base = 0.0

    def precio_medio(self):
        return self.coste_base / self.qty if self.qty else 0.0


def simular(d, ind, cfg, rng, variante, dias_entrada=None):
    """One run of the whole rule set over one panel. Returns a dict of numbers."""
    T, S = d["T"], d["S"]
    o, h, l, c = d["open"], d["high"], d["low"], d["close"]
    N, hh_e, ll_e, hh_s, ll_s = ind["N"], ind["hh_e"], ind["ll_e"], ind["hh_s"], ind["ll_s"]
    fin = d["fin"].astype(int)
    ini = d["ini"].astype(int)
    dias = dias_calendario(d["fechas"])
    borrow = d["borrow"] if ("borrow" in d and cfg["borrow"]) else np.zeros(S)
    if cfg.get("funding"):
        if "funding" not in d:
            # revision f227 c.8.ii: fallar ABIERTO aqui publicaba cobertura 1,0 y
            # cobraba cero. Se rechaza.
            raise ErrorTortugas("el panel pide funding y el npz no trae el array "
                                "'funding': cobrar cero y publicar cobertura 1,0 "
                                "seria una mentira con forma de numero")
        fund = d["funding"]
        if "funding_ok" not in d:
            raise ErrorTortugas("el panel trae 'funding' pero no 'funding_ok': sin "
                                "la mascara no se puede medir la cobertura S3")
        fund_ok = d["funding_ok"]
    else:
        fund = np.zeros((T, S))
        fund_ok = np.ones((T, S), dtype="u1")
    activo = d["activo"] if "activo" in d else np.ones(S, dtype="u1")

    equity0 = float(cfg["equity"])
    caja = equity0
    pos = {}
    t0 = int(cfg["offset"])
    ret = np.zeros(T)
    eq_prev = equity0
    pnl_sim = np.zeros(S)
    pnl_largo = np.zeros(S)
    bajo_lote_sim = np.zeros(S)
    bajo_min_sim = np.zeros(S)
    ct = dict(trades=0, entradas=0, adds=0, bajo_lote=0, bajo_min=0, cap=0,
              stop=0, salida20=0, deslistada=0, coste=0.0, fin=0.0,
              sd_ok=0, sd_tot=0, fb_mediana=0, fb_p90_sim=0, fb_p90_dia=0,
              fb_p90_panel=0)
    riesgos = []
    bruto_acum = 0.0
    n_dias = 0
    ent_largo = np.zeros(S)
    ent_corto = np.zeros(S)

    bps = float(cfg["bps_lado"]) / 10000.0
    lote = cfg["lote"]
    min_not = float(cfg.get("min_notional", 0.0))
    tope = float(cfg.get("tope_bruto", 1.0))
    max_u = int(cfg.get("max_unidades", 4))
    paso = float(cfg.get("paso_add", 0.5))
    stop_n = float(cfg.get("stop_n", 2.0))
    desliz_gap = float(cfg.get("desliz_gap_n", 1.0))
    solo_largo = variante == "long_only"
    # escalera de respaldo (s.3 del sello), inyectada desde el npz del panel
    fb_med = cfg.get("funding_mediana_sym")
    fb_p90s = cfg.get("funding_p90_sym")
    fb_nobs = cfg.get("funding_n_obs_sym")
    fb_p90d = cfg.get("funding_p90_dia")
    fb_p90p = cfg.get("funding_p90_panel")
    min_obs = int(cfg.get("funding_min_obs", 30))
    if cfg.get("funding") and (fb_med is None or fb_p90s is None or fb_nobs is None):
        raise ErrorTortugas("panel con funding sin la escalera de respaldo sellada "
                            "(funding_mediana_sym / funding_p90_sym / funding_n_obs_sym)")

    traza = cfg.get("traza")

    def cerrar(s, p, precio, motivo):
        nonlocal caja
        if traza is not None:
            traza.append({"ev": "cierre", "t": t, "s": s, "precio": float(precio),
                          "motivo": motivo, "qty": p.qty, "lado": p.lado})
        signo = 1.0 if p.lado > 0 else -1.0
        bruto = signo * p.qty * precio - signo * p.coste_base
        coste = abs(p.qty * precio) * bps
        caja += bruto - coste
        ct["coste"] += coste
        pnl_sim[s] += bruto - coste
        if p.lado > 0:
            pnl_largo[s] += bruto - coste
        ct["trades"] += 1
        ct[motivo] += 1
        del pos[s]

    for t in range(t0 + 1, T):
        # 1. stops, on the bar of the day (gap -> OPEN plus 1,0 x N adverse)
        for s in list(pos.keys()):
            p = pos[s]
            if np.isnan(c[t, s]) or t > fin[s]:
                continue
            if p.lado > 0 and (l[t, s] <= p.stop):
                precio = (o[t, s] - desliz_gap * p.N) if o[t, s] <= p.stop else p.stop
                cerrar(s, p, float(precio), "stop")
            elif p.lado < 0 and (h[t, s] >= p.stop):
                precio = (o[t, s] + desliz_gap * p.N) if o[t, s] >= p.stop else p.stop
                cerrar(s, p, float(precio), "stop")
        # 2. delisting: close on the LAST session with data, at its close
        for s in list(pos.keys()):
            if t >= fin[s]:
                cerrar(s, pos[s], float(c[fin[s], s]), "deslistada")
        # 3. the 20-day exit
        for s in list(pos.keys()):
            p = pos[s]
            if np.isnan(c[t, s]):
                continue
            if p.lado > 0 and not np.isnan(ll_s[t, s]) and c[t, s] < ll_s[t, s]:
                cerrar(s, p, float(c[t, s]), "salida20")
            elif p.lado < 0 and not np.isnan(hh_s[t, s]) and c[t, s] > hh_s[t, s]:
                cerrar(s, p, float(c[t, s]), "salida20")
        # 4. mark to market
        no_real = 0.0
        bruto = 0.0
        for s, p in pos.items():
            precio = float(c[t, s]) if not np.isnan(c[t, s]) else p.precio_medio()
            signo = 1.0 if p.lado > 0 else -1.0
            no_real += signo * (p.qty * precio - p.coste_base)
            bruto += p.qty * precio
        equity = caja + no_real
        # 5. financing: borrow act/360 on short notional, funding on both legs
        for s, p in pos.items():
            precio = float(c[t, s]) if not np.isnan(c[t, s]) else p.precio_medio()
            nocional = p.qty * precio
            if p.lado < 0 and borrow[s] > 0:
                cargo = nocional * float(borrow[s]) * float(dias[t]) / 360.0
                caja -= cargo
                ct["fin"] += cargo
                pnl_sim[s] -= cargo
            if cfg.get("funding"):
                ct["sd_tot"] += 1
                signo = 1.0 if p.lado > 0 else -1.0
                if fund_ok[t, s]:
                    ct["sd_ok"] += 1
                    tasa = float(fund[t, s])
                else:
                    tasa = _respaldo(ct, s, t, signo, fb_med, fb_p90s, fb_nobs,
                                     fb_p90d, fb_p90p, min_obs)
                flujo = -signo * nocional * tasa
                caja += flujo
                ct["fin"] -= flujo
                pnl_sim[s] += flujo
        equity = caja + no_real
        if not np.isfinite(equity):
            raise ErrorTortugas("equity NO FINITA en la sesion %d: un NaN de precio "
                                "entro en la contabilidad y un Sharpe 0,0 lo taparia" % t)
        if equity <= 0:
            ret[t] = -1.0 if eq_prev > 0 else 0.0
            n_dias += 1
            break
        # 6. candidates of the day, served in a random permutation (rationing)
        orden = rng.permutation(S)
        for s in orden:
            s = int(s)
            if not activo[s] or t <= ini[s] or t > fin[s] or np.isnan(c[t, s]):
                continue
            p = pos.get(s)
            if p is not None:
                if len(p.fills) >= max_u:
                    continue
                if p.lado > 0 and c[t, s] >= p.ultimo + paso * p.N:
                    lado, Nu = 1, p.N
                elif p.lado < 0 and c[t, s] <= p.ultimo - paso * p.N:
                    lado, Nu = -1, p.N
                else:
                    continue
                es_add = True
            else:
                # N DE LA BARRA QUE LLENA: nada de t+1 (T5c / mutante X2)
                Nu = float(N[t, s]) if not np.isnan(N[t, s]) else 0.0
                if Nu <= 0:
                    continue
                if not np.isnan(hh_e[t, s]) and c[t, s] > hh_e[t, s]:
                    lado = 1
                elif not np.isnan(ll_e[t, s]) and c[t, s] < ll_e[t, s]:
                    lado = -1
                else:
                    lado = 0
                if dias_entrada is not None:
                    lado = int(dias_entrada[t, s])
                if lado == 0:
                    continue
                if lado < 0 and solo_largo:
                    continue
                es_add = False
            precio = float(c[t, s])
            qty = 0.01 * equity / Nu
            if lote == "entero":
                q_ent = math.floor(qty)
                if q_ent < 1:
                    ct["bajo_lote"] += 1
                    bajo_lote_sim[s] += 1.0
                    continue
                qty = float(q_ent)
            nocional = qty * precio
            if min_not > 0 and nocional < min_not:
                ct["bajo_min"] += 1
                bajo_min_sim[s] += 1.0
                continue
            if bruto + nocional > tope * equity:
                ct["cap"] += 1
                continue
            coste = nocional * bps
            caja -= coste
            ct["coste"] += coste
            pnl_sim[s] -= coste
            if es_add:
                pnl_largo[s] -= coste if p.lado > 0 else 0.0
                p.qty += qty
                p.coste_base += qty * precio
                p.fills.append((qty, precio))
                p.ultimo = precio
                # SELLO v2 s.1 l.31-34: el stop SOLO se mueve al anadir unidad, y
                # se mueve EXACTAMENTE 0,5N desde donde estaba.
                p.stop = p.stop + paso * p.N if p.lado > 0 else p.stop - paso * p.N
                ct["adds"] += 1
                if traza is not None:
                    traza.append({"ev": "add", "t": t, "s": s, "precio": precio,
                                  "qty": qty, "stop": p.stop, "N": p.N,
                                  "unidades": len(p.fills), "lado": p.lado})
            else:
                p = Posicion(lado, Nu)
                p.qty = qty
                p.coste_base = qty * precio
                p.fills.append((qty, precio))
                p.ultimo = precio
                p.stop = precio - stop_n * Nu if lado > 0 else precio + stop_n * Nu
                pos[s] = p
                ct["entradas"] += 1
                if traza is not None:
                    traza.append({"ev": "entrada", "t": t, "s": s, "precio": precio,
                                  "qty": qty, "stop": p.stop, "N": Nu,
                                  "unidades": 1, "lado": lado})
                if lado > 0:
                    pnl_largo[s] -= coste
                    ent_largo[s] += 1
                else:
                    ent_corto[s] += 1
            riesgos.append(qty * Nu)
            bruto += nocional
        # 7. the day's return, on the marked equity
        no_real = 0.0
        bruto_fin = 0.0
        for s, p in pos.items():
            precio = float(c[t, s]) if not np.isnan(c[t, s]) else p.precio_medio()
            signo = 1.0 if p.lado > 0 else -1.0
            no_real += signo * (p.qty * precio - p.coste_base)
            bruto_fin += p.qty * precio
        equity = caja + no_real
        ret[t] = equity / eq_prev - 1.0
        eq_prev = equity
        bruto_acum += bruto_fin / equity if equity > 0 else 0.0
        n_dias += 1

    r = ret[t0 + 1: t0 + 1 + n_dias]
    res = _resumen(r, cfg, ct, riesgos, pnl_sim, pnl_largo, bruto_acum, equity0, eq_prev)
    res["entradas_largo"] = ent_largo
    res["entradas_corto"] = ent_corto
    res["bajo_lote_sim"] = bajo_lote_sim
    res["bajo_min_sim"] = bajo_min_sim
    res["t_ini"] = t0 + 1
    return res


def _respaldo(ct, s, t, signo, fb_med, fb_p90s, fb_nobs, fb_p90d, fb_p90p, min_obs):
    """The sealed fallback ladder, rung by rung. NEVER returns a silent zero:
    if no rung has a number, it RAISES. `signo` is +1 for a long position and -1
    for a short one; rungs 2, 3 and 3b are charged as a COST to that side."""
    if fb_med is not None and fb_nobs is not None:
        if int(fb_nobs[s]) >= min_obs and np.isfinite(fb_med[s]):
            ct["fb_mediana"] += 1
            return float(fb_med[s])                      # rung 1: signed, as sealed
    if fb_p90s is not None and np.isfinite(fb_p90s[s]):
        ct["fb_p90_sim"] += 1
        return signo * abs(float(fb_p90s[s]))            # rung 2: pessimistic
    if fb_p90d is not None and np.isfinite(fb_p90d[t]):
        ct["fb_p90_dia"] += 1
        return signo * abs(float(fb_p90d[t]))            # rung 3: pessimistic
    if fb_p90p is not None and np.isfinite(fb_p90p):
        ct["fb_p90_panel"] += 1
        return signo * abs(float(fb_p90p))               # rung 3b: pessimistic
    raise ErrorTortugas(
        "escalera de respaldo AGOTADA en (simbolo %d, sesion %d): el sello no "
        "permite cobrar cero -- un dia-simbolo sin funding y sin respaldo es el "
        "escenario que el sello llama REFUTACION, no una corrida primaria" % (s, t))


def _resumen(r, cfg, ct, riesgos, pnl_sim, pnl_largo, bruto_acum, equity0, eq_fin):
    n = len(r)
    vals = r.tolist()
    media = math.fsum(vals) / n if n else 0.0
    var = math.fsum((v - media) * (v - media) for v in vals) / (n - 1) if n > 1 else 0.0
    sd = math.sqrt(var)
    ann = float(cfg["anualizacion"])
    # SIN CERO MUDO: sd == 0 (o menos de 2 sesiones) no es Sharpe 0,0 -- es NaN
    # con bandera. El 0,0000 exacto es la firma de una prueba que no corrio.
    degenerada = 1.0 if (n < 2 or sd <= 0.0) else 0.0
    sharpe = (media / sd) * math.sqrt(ann) if sd > 0 else float("nan")
    eq = equity0 * np.cumprod(1.0 + r) if n else np.array([equity0])
    pico = np.maximum.accumulate(eq)
    dd = float(np.min(eq / pico - 1.0)) if n else float("nan")
    anios = n / ann
    total = abs(float(math.fsum(np.abs(pnl_sim).tolist())))
    top = float(np.max(np.abs(pnl_sim))) / total if total > 0 else 0.0
    return {
        "r": r, "sharpe": sharpe, "dd": dd, "media": media, "sd": sd,
        "degenerada": degenerada,
        "retorno_total": float(eq[-1] / equity0 - 1.0) if n else 0.0,
        "anios": anios, "t": sharpe * math.sqrt(anios),
        "riesgo_mediano": float(np.median(riesgos)) if riesgos else 0.0,
        "bruto_medio": bruto_acum / n if n else 0.0,
        "top": top, "pnl_sim": pnl_sim, "pnl_largo": pnl_largo, "ct": ct, "n": n,
    }


# --------------------------------------------------------------------------- #
# cells, twins and the null
# --------------------------------------------------------------------------- #
def _cfg_panel(par, panel, variante):
    base = dict(par["paneles"][panel])
    cfg = {
        "equity": base["equity"], "bps_lado": base["bps_lado"], "lote": base["lote"],
        "min_notional": base.get("min_notional", 0.0),
        "anualizacion": base["anualizacion"],
        "borrow": bool(base.get("borrow", False)),
        "funding": bool(base.get("funding", False)),
        "tope_bruto": par["tope_bruto"], "max_unidades": par["max_unidades"],
        "paso_add": par["paso_add"], "stop_n": par["stop_n"],
        "desliz_gap_n": par["desliz_gap_n"],
        "funding_min_obs": int(par.get("funding_min_obs_mediana", 30)),
    }
    if variante == "no_borrow":
        cfg["borrow"] = False
    if variante == "no_funding":
        cfg["funding"] = False
    return cfg


def _inyecta_respaldo(cfg, d):
    """The fallback ladder travels with the DATA (it is measured from the funding
    store in scripts/preparar_datos_tortugas.py and written into the npz), never
    as a constant in the spec: a constant 0,0 is what made the ladder dead code in
    pass 1 [revision f227 B2]."""
    if not cfg.get("funding"):
        return cfg
    for clave in ("funding_mediana_sym", "funding_p90_sym", "funding_n_obs_sym"):
        if clave not in d:
            raise ErrorTortugas("el npz del panel no trae " + clave + ": la escalera "
                                "de respaldo sellada no se puede aplicar")
        cfg[clave] = d[clave]
    cfg["funding_p90_dia"] = d["funding_p90_dia"] if "funding_p90_dia" in d else None
    cfg["funding_p90_panel"] = (float(d["funding_p90_panel"])
                                if "funding_p90_panel" in d else None)
    return cfg


def _rng(semilla, panel, variante):
    ss = np.random.SeedSequence([int(semilla), int(PANEL_COD[panel]),
                                 int(VARIANTE_COD[variante])])
    return np.random.Generator(np.random.PCG64(ss))


def _dias_entrada_aleatorios(d, res_main, rng, solo_largo):
    """C1: entries drawn at random days, matched in COUNT per symbol and
    direction with the primary run of the same seed.

    DECLARED (revision f227 c.6): the match is in DRAWS, not in realised entries
    -- a drawn day that falls inside an open position is skipped by the simulator,
    so C1 realises FEWER trades than the primary. The drawn count travels in the
    header (slot `entradas_sorteadas_c1`) so every S1 comparison can publish both.
    Long and short draws of one symbol are now drawn from a SINGLE sample without
    replacement, so they can no longer overwrite each other."""
    T, S = d["T"], d["S"]
    m = np.zeros((T, S), dtype="i1")
    sorteadas = 0
    for s in range(S):
        i0, i1 = int(d["ini"][s]) + 56, int(d["fin"][s])
        if i1 <= i0:
            continue
        kl = int(res_main["entradas_largo"][s])
        kc = 0 if solo_largo else int(res_main["entradas_corto"][s])
        k = min(kl + kc, i1 - i0)
        if k <= 0:
            continue
        dias = rng.choice(np.arange(i0, i1), size=k, replace=False)
        kl_ef = min(kl, k)
        m[dias[:kl_ef], s] = 1
        if k > kl_ef:
            m[dias[kl_ef:], s] = -1
        sorteadas += k
    return m, sorteadas


def correr_celda(par, panel, variante, semilla):
    d = cargar_panel(par, panel)
    if variante == "no_graveyard":
        d = _sin_cementerio(d)
    ind = prepara_indicadores(d, par["ventana_entrada"], par["ventana_salida"])
    cfg = _inyecta_respaldo(_cfg_panel(par, panel, variante), d)
    cfg["offset"] = int(semilla) % int(par["jitter_modulo"])
    rng = _rng(semilla, panel, variante)
    if variante == "random_entry":
        base = simular(d, ind, cfg, _rng(semilla, panel, "main"), "main")
        dias, sorteadas = _dias_entrada_aleatorios(d, base, rng, False)
        res = simular(d, ind, cfg, _rng(semilla, panel, variante), variante, dias)
        res["sorteadas"] = sorteadas
        return res
    res = simular(d, ind, cfg, rng, variante)
    res["sorteadas"] = 0
    return res


def _sin_cementerio(d):
    """C4: the graveyard (delisted symbols) removed from panel (b)."""
    if "muerta" not in d:
        raise ErrorTortugas("C4 pide el cementerio y el panel no trae el array 'muerta'")
    vivos = d["muerta"] == 0
    out = dict(d)
    for k in ("open", "high", "low", "close", "funding", "funding_ok"):
        if k in d:
            out[k] = d[k][:, vivos]
    for k in ("ini", "fin", "borrow", "muerta", "activo", "simbolos",
              "funding_mediana_sym", "funding_p90_sym", "funding_n_obs_sym"):
        if k in d:
            out[k] = d[k][vivos]
    out["S"] = int(np.count_nonzero(vivos))
    return out


def _bootstrap(d, rng, bloque):
    """Block bootstrap of each symbol's daily bar, DECLARED as FIXED-LENGTH
    CIRCULAR MOVING BLOCKS of `bloque` sessions (NOT Politis-Romano stationary
    blocks with geometric lengths; the seal s.5.4 says "stationary block
    bootstrap" and this is the reading that is run -- declared in the spec's
    `infraespecificaciones_declaradas`).

    Returns (panel, n_simbolos_descartados). A symbol with fewer than bloque+2
    VALID sessions cannot carry a block and is dropped from the replica: it is
    COUNTED and the count is published per replica, never dropped in silence."""
    T, S = d["T"], d["S"]
    out = dict(d)
    descartados = 0
    for k in ("open", "high", "low", "close"):
        out[k] = np.full((T, S), np.nan)
    if "funding" in d:
        out["funding"] = np.zeros((T, S))
        out["funding_ok"] = np.zeros((T, S), dtype="u1")
    for s in range(S):
        i0, i1 = int(d["ini"][s]), int(d["fin"][s])
        L = i1 - i0 + 1
        if L < bloque + 2:
            descartados += 1
            continue
        crudo = d["close"][i0:i1 + 1, s]
        val = np.nonzero(~np.isnan(crudo))[0]
        if len(val) < bloque + 2:
            descartados += 1
            continue
        c = crudo[val]
        L = len(c)
        r = np.empty(L)
        r[0] = 0.0
        r[1:] = c[1:] / c[:-1] - 1.0
        ro = d["open"][i0:i1 + 1, s][val] / c
        rh = d["high"][i0:i1 + 1, s][val] / c
        rl = d["low"][i0:i1 + 1, s][val] / c
        nb = int(math.ceil(L / bloque))
        arranques = rng.integers(0, L, size=nb)
        idx = np.concatenate([(np.arange(a, a + bloque) % L) for a in arranques])[:L]
        nc = np.empty(L)
        nc[0] = c[0]
        rr = r[idx]
        nc[1:] = c[0] * np.cumprod(1.0 + rr[1:])
        fil = i0 + val
        out["close"][fil, s] = nc
        out["open"][fil, s] = nc * ro[idx]
        out["high"][fil, s] = np.maximum(nc * rh[idx], nc)
        out["low"][fil, s] = np.minimum(nc * rl[idx], nc)
        if "funding" in d:
            out["funding"][fil, s] = d["funding"][i0:i1 + 1, s][val][idx]
            out["funding_ok"][fil, s] = d["funding_ok"][i0:i1 + 1, s][val][idx]
    return out, descartados


def _una_replica(par, panel, semilla):
    """One bootstrap replica of one panel: (fechas, res, cfg, descartados)."""
    d = cargar_panel(par, panel)
    rng = np.random.Generator(np.random.PCG64(
        np.random.SeedSequence([int(semilla), int(PANEL_COD[panel])])))
    dd, descartados = _bootstrap(d, rng, int(par["bloque_bootstrap"]))
    ind = prepara_indicadores(dd, par["ventana_entrada"], par["ventana_salida"])
    cfg = _inyecta_respaldo(_cfg_panel(par, panel, "main"), dd)
    cfg["offset"] = int(semilla) % int(par["jitter_modulo"])
    res = simular(dd, ind, cfg, rng, "main")
    fechas = dd["fechas"][res["t_ini"]: res["t_ini"] + res["n"]]
    return fechas, res, cfg, descartados


def _pool_equi_riesgo(series, ann):
    """v2 s.5.1 SECONDARY: each panel's daily net series scaled to the same
    ex-ante vol (target = the MEDIAN realised vol of the three over the COMMON
    window) and averaged 1/3.

    DECLARED ANNUALISATION: each panel's OWN unit is annualised with its OWN
    calendar (252 for (a) and (c), 365 for (b), header slot `anualizacion`). The
    aggregate lives on the INTERSECTION of the three calendars, which is the NYSE
    session calendar (~252/year), so it is annualised at 252 and panel (b)'s
    weekend sessions DO NOT enter the pool. That truncation is the price of
    pooling three calendars and it is declared, not hidden.

    Returns (sharpe, dd, trades, invalida). A common window shorter than 30
    sessions RAISES: returning (0,0 / 0,0 / 0,0) published a number that looked
    like a measurement and was not."""
    comun = None
    for fechas, _res in series:
        comun = set(fechas.tolist()) if comun is None else (comun & set(fechas.tolist()))
    comun = np.array(sorted(comun), dtype="i8")
    if len(comun) < 30:
        raise ErrorTortugas(
            "ventana comun del agregado de %d sesiones (< 30): se RECHAZA en vez "
            "de devolver ceros -- un cero aqui es indistinguible de una medida" % len(comun))
    trozos, vols = [], []
    for fechas, res in series:
        idx = np.searchsorted(fechas, comun)
        r = res["r"][idx]
        trozos.append(r)
        vals = r.tolist()
        m = math.fsum(vals) / len(vals)
        vols.append(math.sqrt(math.fsum((v - m) * (v - m) for v in vals) / (len(vals) - 1)))
    objetivo = float(np.median(vols))
    escalados = [t * (objetivo / v) if v > 0 else t * 0.0 for t, v in zip(trozos, vols)]
    r = sum(escalados) / float(len(escalados))
    vals = r.tolist()
    m = math.fsum(vals) / len(vals)
    sd = math.sqrt(math.fsum((v - m) * (v - m) for v in vals) / (len(vals) - 1))
    invalida = 1.0 if (sd <= 0.0 or min(vols) <= 0.0) else 0.0
    sharpe = (m / sd) * math.sqrt(ann) if sd > 0 else float("nan")
    eq = np.cumprod(1.0 + r)
    dd = float(np.min(eq / np.maximum.accumulate(eq) - 1.0))
    trades = float(math.fsum(res["ct"]["trades"] for _f, res in series))
    return sharpe, dd, trades, invalida


def correr_nulo(par, panel, replicas):
    """`replicas` = list of GLOBAL replica indices. Each row is
    (sharpe, max_dd, trades, simbolos_descartados, invalida): a replica whose sd
    is 0 carries sharpe = NaN and invalida = 1, never a silent 0,0."""
    salida = []
    for j in replicas:
        if panel == "agregado":
            semilla = int(par["semilla_nulo_agregado"]) + int(j)
            partes = [_una_replica(par, p, semilla) for p in par["paneles_agregado"]]
            series = [(x[0], x[1]) for x in partes]
            desc = float(math.fsum(float(x[3]) for x in partes))
            sh, dd, tr, inval = _pool_equi_riesgo(
                series, float(par["anualizacion_agregado"]))
            inval = 1.0 if (inval or any(x[1]["degenerada"] for x in partes)) else 0.0
            salida.append((sh, dd, tr, desc, inval))
            continue
        semilla = int(par["semilla_nulo_base"]) + int(j)
        _fechas, res, _cfg, desc = _una_replica(par, panel, semilla)
        salida.append((res["sharpe"], res["dd"], float(res["ct"]["trades"]),
                       float(desc), float(res["degenerada"])))
    return salida


# --------------------------------------------------------------------------- #
# the unit plan: index -> (panel, variant, seed) or (panel, null chunk)
# --------------------------------------------------------------------------- #
def plan_unidades(par):
    plan = []
    for celda in par["celdas"]:
        for k in range(int(par["n_semillas"])):
            plan.append({"tipo": "real", "panel": celda["panel"],
                         "variante": celda["variante"],
                         "semilla": int(par["semilla_base"]) + k})
    for nulo in par["nulos"]:
        total = int(nulo["replicas"])
        paso = int(nulo["por_unidad"])
        for j0 in range(0, total, paso):
            plan.append({"tipo": "nulo", "panel": nulo["panel"],
                         "replicas": list(range(j0, min(j0 + paso, total)))})
    return plan


# --------------------------------------------------------------------------- #
# which unit am I? (without touching nucleo.py)
# --------------------------------------------------------------------------- #
_MAPA_INDICE = {}


def _clave_estado(rng):
    e = rng.bit_generator.state
    s = e["state"]
    return (e["bit_generator"], int(s["state"]), int(s["inc"]))


def indice_de_rng(rng, par):
    """Recover i from the per-unit RNG nucleo hands over. nucleo builds it as
    rng_unidad(seed_master, i), a pure function of the sealed seed_master and i,
    so the U possible states are enumerable and the map is deterministic. A state
    that is not in the map RAISES: guessing an index would run the wrong cell."""
    sm = par.get("seed_master")
    if not sm:
        raise ErrorTortugas("parametros.seed_master ausente: sin el no se puede "
                            "recuperar el indice de la unidad sin tocar nucleo.py")
    n = nucleo_modulo()
    u = len(plan_unidades(par))
    clave = (sm, u)
    mapa = _MAPA_INDICE.get(clave)
    if mapa is None:
        mapa = {_clave_estado(n.rng_unidad(sm, i)): i for i in range(u)}
        if len(mapa) != u:
            raise ErrorTortugas(
                "colision de estados PCG64: %d claves distintas para %d unidades "
                "derivadas de seed_master %r. El mapa perderia una unidad y otra "
                "correria la celda EQUIVOCADA en silencio: el lote se para aqui"
                % (len(mapa), u, sm))
        _MAPA_INDICE[clave] = mapa
    est = _clave_estado(rng)
    if est not in mapa:
        raise ErrorTortugas("el rng recibido no corresponde a ninguna de las %d "
                            "unidades derivadas de seed_master %r" % (u, sm))
    return mapa[est]


def _empaqueta_real(i, u, res, par):
    ct = res["ct"]
    S = len(res["pnl_sim"])
    cab = np.zeros(N_CAB)
    cab[0] = LAYOUT
    cab[1] = i
    cab[2] = 0.0
    cab[3] = PANEL_COD[u["panel"]]
    cab[4] = VARIANTE_COD[u["variante"]]
    cab[5] = u["semilla"]
    cab[6] = 0.0
    cab[7] = res["n"]
    cab[8] = res["sharpe"]
    cab[9] = res["dd"]
    cab[10] = res["retorno_total"]
    cab[11] = ct["trades"]
    cab[12] = ct["entradas"]
    cab[13] = ct["bajo_lote"]
    cab[14] = ct["bajo_min"]
    cab[15] = ct["cap"]
    cab[16] = ct["adds"]
    cab[17] = ct["stop"]
    cab[18] = ct["salida20"]
    cab[19] = ct["deslistada"]
    cab[20] = ct["coste"]
    cab[21] = ct["fin"]
    cab[22] = res["riesgo_mediano"]
    cab[23] = S
    cab[24] = N_CAB
    cab[25] = par["paneles"][u["panel"]]["anualizacion"]
    cab[26] = res["bruto_medio"]
    cab[27] = res["top"]
    cab[28] = res["anios"]
    cab[29] = res["t"]
    cab[30] = (ct["sd_ok"] / ct["sd_tot"]) if ct["sd_tot"] else -1.0
    cab[31] = 1.0 if ct["trades"] < 20 else 0.0
    cab[32] = ct["fb_mediana"]
    cab[33] = ct["fb_p90_sim"]
    cab[34] = ct["fb_p90_dia"]
    cab[35] = ct["fb_p90_panel"]
    cab[36] = res.get("sorteadas", 0)
    cab[37] = BLOQUES_POR_SIMBOLO
    cab[38] = CAMPOS_POR_REPLICA
    cab[39] = res["degenerada"]
    cab[40] = 0.0
    return np.concatenate([cab, res["pnl_sim"], res["pnl_largo"],
                           res["bajo_lote_sim"], res["bajo_min_sim"],
                           res["r"]]).astype("<f8")


def _empaqueta_nulo(i, u, filas, par):
    cab = np.zeros(N_CAB)
    cab[0] = LAYOUT
    cab[1] = i
    cab[2] = 1.0
    cab[3] = PANEL_COD[u["panel"]]
    cab[4] = -1.0
    cab[5] = (int(par["semilla_nulo_agregado"]) if u["panel"] == "agregado"
              else int(par["semilla_nulo_base"])) + u["replicas"][0]
    cab[6] = len(filas)
    cab[24] = N_CAB
    cab[25] = (float(par["anualizacion_agregado"]) if u["panel"] == "agregado"
               else par["paneles"][u["panel"]]["anualizacion"])
    cab[37] = BLOQUES_POR_SIMBOLO
    cab[38] = CAMPOS_POR_REPLICA
    cab[39] = float(math.fsum(float(f[4]) for f in filas))      # replicas INVALIDAS
    cab[40] = float(math.fsum(float(f[3]) for f in filas))      # simbolos descartados
    cuerpo = np.array([v for fila in filas for v in fila], dtype="f8")
    return np.concatenate([cab, cuerpo]).astype("<f8")


def calcular(rng, par, i=None):
    """The whole computation of unit i.

    nucleo.py (UNTOUCHED, F222-sealed) calls this as calculo(rng, parametros);
    the index is then recovered from the rng. Tests and the smoke pass i
    explicitly. The rng is NOT used for any draw: see the DETERMINISM note."""
    if i is None:
        i = indice_de_rng(rng, par)
    plan = plan_unidades(par)
    if i >= len(plan):
        raise ErrorTortugas("unidad %d fuera del plan (%d unidades)" % (i, len(plan)))
    u = plan[i]
    if u["tipo"] == "real":
        res = correr_celda(par, u["panel"], u["variante"], u["semilla"])
        return _empaqueta_real(i, u, res, par)
    filas = correr_nulo(par, u["panel"], u["replicas"])
    return _empaqueta_nulo(i, u, filas, par)


CALCULOS = {"tortugas_sistema2": calcular}


def registrar_en_nucleo():
    """Register this lot's calculo in nucleo.CALCULOS WITHOUT editing nucleo.py
    (its sha256 is pinned by the F222 pilot seal). Called at import time."""
    n = nucleo_modulo()
    n.CALCULOS.update(CALCULOS)
    return n


#: None when the registration succeeded; the reason when it did not (this file
#: is also loaded stand-alone, out of the subtree, by the mutant harness). It is
#: PUBLISHED, never swallowed: T24 asserts it is None inside the repo.
MOTIVO_SIN_REGISTRO = None
try:
    registrar_en_nucleo()
except ErrorTortugas as _exc:          # nucleo.py no alcanzable desde esta copia
    MOTIVO_SIN_REGISTRO = str(_exc)
