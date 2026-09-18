"""lotes/motor/nucleo.py -- F222: the unit runner of a batch lot (Colab or VPS).

WHAT IT IS: pure computation. It takes a SEALED spec (a dict read from
lotes/spec/<run_id>.json) and a plain DIRECTORY PATH, and writes one checkpoint
per work unit into that directory. It imports numpy and the standard library and
NOTHING else: no Drive API, no google client, no network, no credential, no path
of the private repo. The "Drive" folder is just a mounted directory.

WHY CHECKPOINTS (design docs/DISENO_COLAB_LOTES.md seccion 1.c): the batch
runtime is assumed to die at any moment and its idle timeout is NOT PUBLISHED
[fuente: docs/DISENO_COLAB_LOTES.md seccion 1.c, tabla de limites, consultado
2026-09-17], so work must be additive across sessions.

THE THREE PROPERTIES THIS FILE OWES THE REST OF THE PIPELINE:

1. ORDER-INDEPENDENT SEEDS. seed_i = sha256(seed_master || ":" || i) truncated to
   16 bytes, fed to numpy.random.SeedSequence and thence to a PCG64 Generator.
   No global RNG, no np.random.* module-level call anywhere. Unit i therefore
   does not depend on how many units ran before it in THIS session nor on their
   order -- which is what makes a resumed run bit-identical to an uninterrupted
   one (design seccion 1.c, tests T1/T2).
2. NO HALF-WRITTEN UNIT CAN LOOK FINISHED. The data file is written to
   <name>.parcial, its sha256 sidecar and its json sidecar are written next, and
   only THEN is the .parcial renamed to its final name (rename is atomic on the
   same filesystem). A kill at any point leaves either nothing or a complete unit.
3. IDEMPOTENT RESUME. A unit counts as DONE only if its data file exists, its
   sha256 RECOMPUTES to the value in its .sha256 sidecar, and its .json parses.
   Anything else is deleted and recomputed. Re-running a finished lot recomputes
   nothing and only rewrites manifest.json.

DELIBERATE DEVIATION FROM THE DESIGN, declared: the design names the unit file
`unit_<i>.npz`. An .npz is a ZIP archive and ZIP entries embed the local
modification time, so two runs of the same computation produce DIFFERENT BYTES
and bit-identity could not be tested at all. The unit file here is a plain
`.npy`, whose header carries only dtype, shape and order. `.npz` stays in the
acceptance allow-list for lots that legitimately need several arrays, but then
bit-identity is asserted on the arrays, not on the container.

CROSS-MACHINE REPRODUCIBILITY, and its one real trap: the PCG64 stream is stable
across platforms by numpy's own stream-compatibility policy, but a floating SUM
is not -- pairwise summation in numpy depends on the SIMD width of the CPU, and
the machine type a free batch runtime hands out is not published [fuente:
docs/DISENO_COLAB_LOTES.md seccion 1.c, consultado 2026-09-17]. Any reduction in
a calculo registered here is therefore done with math.fsum, which is exactly
rounded and identical on every IEEE-754 machine. A calculo that reduces with
np.sum/np.mean may fail the VPS spot-check for a reason that is not a forgery.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import sys
import time
import uuid
from pathlib import Path

import numpy as np

VERSION_MOTOR = "f222.1"
EXT_DATO = ".npy"
BLOQUE = 1 << 20

#: Fields a spec MUST pin. A spec missing any of them is REFUSED, never defaulted
#: (docs/DISENO_COLAB_LOTES.md seccion 1.a, "WHAT THE SPEC MUST PIN").
CAMPOS_SPEC = (
    "run_id",
    "code_commit",
    "code_subtree",
    "data_cut",
    "seed_master",
    "derivacion_semilla",
    "calculo",
    "unidades",
    "parametros",
    "reglas_parada",
    "outputs",
    "ledger_id",
)

DERIVACION_CANONICA = "sha256(seed_master||':'||i)[:16] -> SeedSequence -> PCG64"


class ErrorLote(Exception):
    """Every refusal of this module. Loud, never a silent default."""


def _utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha256_fichero(ruta):
    h = hashlib.sha256()
    with open(ruta, "rb") as fh:
        for trozo in iter(lambda: fh.read(BLOQUE), b""):
            h.update(trozo)
    return h.hexdigest()


def sha256_bytes(datos):
    return hashlib.sha256(datos).hexdigest()


# --------------------------------------------------------------------------- #
# seeds
# --------------------------------------------------------------------------- #
def semilla_unidad(seed_master, i):
    """seed_i = first 16 bytes of sha256(seed_master || ':' || i). No state."""
    if not isinstance(seed_master, str) or not seed_master:
        raise ErrorLote("seed_master ausente o vacio")
    if isinstance(i, bool) or not isinstance(i, int) or i < 0:
        raise ErrorLote("indice de unidad invalido: " + repr(i))
    return hashlib.sha256((seed_master + ":" + str(i)).encode("utf-8")).digest()[:16]


def rng_unidad(seed_master, i):
    secuencia = np.random.SeedSequence(int.from_bytes(semilla_unidad(seed_master, i), "big"))
    return np.random.Generator(np.random.PCG64(secuencia))


# --------------------------------------------------------------------------- #
# the registry of computations
# --------------------------------------------------------------------------- #
def _calc_normales_resumen(rng, par):
    """Draw n standard normals; return [media, desviacion, minimo, maximo].

    The two reductions use math.fsum (exactly rounded) and the two extremes are
    selections, not sums: the four numbers are identical on any IEEE-754 machine
    that runs the same PCG64 stream. See the CROSS-MACHINE note of the module.
    """
    n = int(par["n_por_unidad"])
    if n < 2:
        raise ErrorLote("n_por_unidad debe ser >= 2, es " + str(n))
    x = rng.standard_normal(n)
    valores = x.tolist()
    media = math.fsum(valores) / n
    var = math.fsum((v - media) * (v - media) for v in valores) / (n - 1)
    return np.array([media, math.sqrt(var), float(x.min()), float(x.max())], dtype="<f8")


CALCULOS = {"normales_resumen": _calc_normales_resumen}


# --------------------------------------------------------------------------- #
# spec
# --------------------------------------------------------------------------- #
def valida_spec(spec):
    if not isinstance(spec, dict):
        raise ErrorLote("la spec no es un objeto JSON")
    faltan = [c for c in CAMPOS_SPEC if c not in spec]
    if faltan:
        raise ErrorLote("spec incompleta (no se rellena por defecto), faltan: " + ", ".join(faltan))
    if spec["calculo"] not in CALCULOS:
        raise ErrorLote("calculo no registrado en este motor: " + repr(spec["calculo"]))
    if spec["derivacion_semilla"] != DERIVACION_CANONICA:
        raise ErrorLote("derivacion_semilla no canonica: " + repr(spec["derivacion_semilla"]))
    u = spec["unidades"]
    if isinstance(u, bool) or not isinstance(u, int) or u < 1:
        raise ErrorLote("unidades invalido: " + repr(u))
    if not isinstance(spec.get("parametros"), dict):
        raise ErrorLote("parametros debe ser un objeto")


def raiz_subarbol():
    """Root of the mirrored subtree: the directory that holds motor/ ."""
    return Path(__file__).resolve().parent.parent


def ficheros_subarbol(raiz=None):
    raiz = Path(raiz) if raiz is not None else raiz_subarbol()
    salida = []
    for p in sorted(raiz.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        salida.append({"path": p.relative_to(raiz).as_posix(), "sha256": sha256_fichero(p)})
    return salida


def verificar_subarbol(spec, raiz=None):
    """Every path in code_subtree must exist under raiz with the sha256 pinned."""
    raiz = Path(raiz) if raiz is not None else raiz_subarbol()
    declarado = spec.get("code_subtree")
    if not isinstance(declarado, list) or not declarado:
        raise ErrorLote("code_subtree vacio: la spec no fija el codigo que corre")
    for entrada in declarado:
        ruta = raiz / entrada["path"]
        if not ruta.is_file():
            raise ErrorLote("code_subtree: falta " + entrada["path"])
        real = sha256_fichero(ruta)
        if real != entrada["sha256"]:
            raise ErrorLote(
                "code_subtree: " + entrada["path"] + " sha256 " + real
                + " != sellado " + entrada["sha256"]
            )


def leer_sello(ruta_sello):
    """Parse a <run_id>.json.seal written by lotes/sellar_spec.py."""
    ruta_sello = Path(ruta_sello)
    if not ruta_sello.is_file():
        raise ErrorLote("spec SIN SELLAR: no existe " + str(ruta_sello))
    datos = {}
    for linea in ruta_sello.read_text(encoding="utf-8").splitlines():
        if linea.startswith("SEAL sha256"):
            datos["sha256"] = linea.split("=", 1)[1].strip().split()[0]
        elif linea.startswith("SEAL git"):
            partes = linea.split("=", 1)[1].split()
            datos["commit"] = partes[0]
            datos["commit_time"] = partes[1] if len(partes) > 1 else ""
        elif linea.startswith("SEAL time"):
            datos["time"] = linea.split("=", 1)[1].strip()
    if "sha256" not in datos or "commit" not in datos:
        raise ErrorLote("sello ilegible (faltan SEAL sha256 / SEAL git): " + str(ruta_sello))
    return datos


# --------------------------------------------------------------------------- #
# units on disk
# --------------------------------------------------------------------------- #
def rutas_unidad(dir_corrida, i):
    base = "unit_%04d" % i
    d = Path(dir_corrida) / "units"
    return d / (base + EXT_DATO), d / (base + ".sha256"), d / (base + ".json")


def _linea_sha(hexa, relativa):
    return hexa + "  " + relativa + "\n"


def estado_unidad(dir_corrida, i):
    """'OK' | 'AUSENTE' | 'CORRUPTA'. Never trusts the mere presence of a file."""
    dato, sha, meta = rutas_unidad(dir_corrida, i)
    if not dato.is_file():
        return "AUSENTE"
    if not sha.is_file() or not meta.is_file():
        return "CORRUPTA"
    try:
        esperado = sha.read_text(encoding="utf-8").split()[0]
    except Exception:
        return "CORRUPTA"
    if sha256_fichero(dato) != esperado:
        return "CORRUPTA"
    try:
        json.loads(meta.read_text(encoding="utf-8"))
    except Exception:
        return "CORRUPTA"
    return "OK"


def _borrar_unidad(dir_corrida, i):
    dato, sha, meta = rutas_unidad(dir_corrida, i)
    for ruta in (dato, sha, meta, Path(str(dato) + ".parcial")):
        if ruta.exists():
            ruta.unlink()


def _escribir_npy(ruta, arr):
    with open(ruta, "wb") as fh:
        np.lib.format.write_array(fh, arr, allow_pickle=False)
        fh.flush()
        os.fsync(fh.fileno())


def calcular_unidad(spec, i):
    """The whole computation of unit i, from the sealed seed alone."""
    return CALCULOS[spec["calculo"]](rng_unidad(spec["seed_master"], i), spec["parametros"])


def huella_runtime():
    ram = None
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as fh:
            for linea in fh:
                if linea.startswith("MemTotal:"):
                    ram = int(linea.split()[1]) * 1024
                    break
    except OSError:
        ram = None
    return {
        "python": sys.version.split()[0],
        "numpy": np.__version__,
        "bit_generator": "PCG64",
        "machine": platform.machine(),
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "ram_bytes": ram,
        "acelerador": os.environ.get("COLAB_GPU") or "none",
        "colab_release": os.environ.get("COLAB_RELEASE_TAG") or "",
        "motor": VERSION_MOTOR,
    }


def correr_lote(spec, dir_corrida, gancho=None, registrar=None):
    """Compute every MISSING unit of the lot into dir_corrida. Idempotent.

    gancho(i) runs right BEFORE unit i is computed; a test raises there to
    simulate the runtime being reclaimed mid-unit. registrar(texto) receives one
    progress line per finished unit (LOG.txt, stdout, ...).
    """
    valida_spec(spec)
    dir_corrida = Path(dir_corrida)
    (dir_corrida / "units").mkdir(parents=True, exist_ok=True)
    sesion = uuid.uuid4().hex[:12]
    huella = huella_runtime()
    inicio = _utc()

    reusadas, recomputadas, calculadas, pendientes = [], [], [], []
    for i in range(int(spec["unidades"])):
        estado = estado_unidad(dir_corrida, i)
        if estado == "OK":
            reusadas.append(i)
            continue
        if estado == "CORRUPTA":
            recomputadas.append(i)
            _borrar_unidad(dir_corrida, i)
        pendientes.append(i)

    for i in pendientes:
        if gancho is not None:
            gancho(i)
        t0 = time.time()
        ini = _utc()
        arr = calcular_unidad(spec, i)
        dato, sha, meta = rutas_unidad(dir_corrida, i)
        parcial = Path(str(dato) + ".parcial")
        _escribir_npy(parcial, arr)
        hexa = sha256_fichero(parcial)
        sha.write_text(_linea_sha(hexa, "units/" + dato.name), encoding="utf-8")
        segundos = round(time.time() - t0, 6)
        meta.write_text(
            json.dumps(
                {
                    "unidad": i,
                    "semilla_hex": semilla_unidad(spec["seed_master"], i).hex(),
                    "sha256": hexa,
                    "filas": int(arr.size),
                    "dtype": str(arr.dtype),
                    "inicio_utc": ini,
                    "fin_utc": _utc(),
                    "segundos": segundos,
                    "sesion": sesion,
                    "runtime": huella,
                },
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(parcial, dato)  # atomic: only NOW does the unit look finished
        calculadas.append(i)
        if registrar is not None:
            registrar(_utc() + " unidad %d sha256=%s %ss sesion=%s" % (i, hexa, segundos, sesion))

    return {
        "sesion": sesion,
        "inicio_utc": inicio,
        "fin_utc": _utc(),
        "unidades": int(spec["unidades"]),
        "calculadas": calculadas,
        "reusadas": reusadas,
        "recomputadas_por_corrupcion": recomputadas,
    }


def _artefactos(dir_corrida):
    salida = []
    for p in sorted(Path(dir_corrida).rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(dir_corrida).as_posix()
        if rel in ("manifest.json", "manifest.json.sha256"):
            continue
        if rel.endswith(".parcial"):
            raise ErrorLote("queda un fichero a medias en la corrida: " + rel)
        salida.append({"path": rel, "bytes": p.stat().st_size, "sha256": sha256_fichero(p)})
    return salida


def escribir_manifiesto(spec, dir_corrida, spec_sha256, sello, resumen=None):
    """Write manifest.json + its detached manifest.json.sha256. Rewritable.

    Refuses to write while any unit is missing or corrupt: a manifest is a claim
    that the lot is COMPLETE, and a claim nobody checked is what this pipeline
    exists to stop.
    """
    valida_spec(spec)
    resumen = resumen or {}
    dir_corrida = Path(dir_corrida)
    unidades, sesiones = [], set()
    cpu = 0.0
    maximo = 0.0
    for i in range(int(spec["unidades"])):
        if estado_unidad(dir_corrida, i) != "OK":
            raise ErrorLote("no se escribe manifiesto: la unidad %d no esta completa" % i)
        meta = rutas_unidad(dir_corrida, i)[2]
        d = json.loads(meta.read_text(encoding="utf-8"))
        unidades.append(
            {
                "unidad": i,
                "semilla_hex": d["semilla_hex"],
                "sha256": d["sha256"],
                "segundos": d["segundos"],
                "inicio_utc": d["inicio_utc"],
                "fin_utc": d["fin_utc"],
            }
        )
        sesiones.add(d["sesion"])
        cpu += float(d["segundos"])
        maximo = max(maximo, float(d["segundos"]))

    manifiesto = {
        "run_id": spec["run_id"],
        "spec_sha256": spec_sha256,
        "spec_seal_commit": sello["commit"],
        "spec_seal_sha256": sello["sha256"],
        "code_commit": spec["code_commit"],
        "calculo": spec["calculo"],
        "motor": VERSION_MOTOR,
        "code_subtree": spec["code_subtree"],
        "artifacts": _artefactos(dir_corrida),
        "units": unidades,
        "cpu_segundos": round(cpu, 6),
        "max_unidad_segundos": round(maximo, 6),
        "sesiones": len(sesiones),
        "runtime": dict(
            huella_runtime(),
            sesion_actual=resumen.get("sesion", ""),
            inicio_utc=resumen.get("inicio_utc", ""),
            fin_utc=resumen.get("fin_utc", ""),
        ),
        "spec_echo": spec,
    }
    texto = json.dumps(manifiesto, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    ruta = dir_corrida / "manifest.json"
    ruta.write_text(texto, encoding="utf-8")
    (dir_corrida / "manifest.json.sha256").write_text(
        _linea_sha(sha256_bytes(texto.encode("utf-8")), "manifest.json"), encoding="utf-8"
    )
    return ruta
