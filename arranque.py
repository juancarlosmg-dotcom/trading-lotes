"""Runtime start-up of a sealed lot: import the motors the spec declares.

F227 pass 3, condition 2 of reviews/review_f227_pass2.md: the runner
(lotes/colab_lote.ipynb) imported ONLY nucleo, so a lot whose `calculo` lives
in another module (lotes/motor/tortugas.py) could not run at all. The spec
declares the contract in the key `arranque_runtime`; this module CONSUMES it,
generically and loudly.

Where the key may live: at the top level of the spec, inside `parametros`, or
in any nested dict (the first `arranque_runtime` found wins).

Two accepted shapes:
  * list/tuple of module names -> EVERY name must import (all required).
  * free text (the shape the F227 spec uses) -> the dotted, all-lowercase
    tokens that look like module paths are read as ALTERNATIVES of the same
    motor (the sentence names `lotes.motor.tortugas` in the repo and
    `motor.tortugas` in the Colab mirror): at least ONE must import. Tokens
    with a slash or a file extension (`lotes/motor/nucleo.py`) and tokens with
    an uppercase segment (`nucleo.CALCULOS`) are not module names and drop out.

Nothing here is silent: a missing key, a value with no candidate, or a failed
required import RAISES ErrorArranque BEFORE the first draw. This file imports
nothing from the sealed subtree, so nucleo.py and lotes/motor/__init__.py keep
their sha256.
"""
import importlib
import re

CLAVE = "arranque_runtime"
_TOKEN = re.compile(r"^[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)+$")
_EXTENSIONES = (".py", ".json", ".ipynb", ".md", ".npz", ".npy", ".seal", ".txt")


class ErrorArranque(RuntimeError):
    """The lot cannot start: the declaration is missing or a motor does not import."""


def declaracion(spec):
    """Raw value of `arranque_runtime`, wherever it lives in the spec."""
    pila = [spec]
    while pila:
        d = pila.pop(0)
        if not isinstance(d, dict):
            continue
        if CLAVE in d:
            return d[CLAVE]
        pila.extend(v for v in d.values() if isinstance(v, dict))
    raise ErrorArranque(
        "la spec no declara %r: sin esa clave el runner no sabe que motores "
        "importar antes de correr_lote, y nucleo solo conoce los calculos ya "
        "registrados" % CLAVE)


def _candidatos(texto):
    fuera = []
    for bruto in re.split(r"\s+", texto):
        t = bruto.strip(" \t,;:()[]{}'\"`").rstrip(".")
        if not t or "/" in t or "\\" in t:
            continue
        if t.lower().endswith(_EXTENSIONES):
            continue
        if _TOKEN.match(t) and t not in fuera:
            fuera.append(t)
    return fuera


def modulos_declarados(spec):
    """(names, alternativas). alternativas=True -> at least one must import."""
    valor = declaracion(spec)
    if isinstance(valor, (list, tuple)):
        nombres = [str(x).strip() for x in valor if str(x).strip()]
        alternativas = False
    elif isinstance(valor, str):
        nombres = _candidatos(valor)
        alternativas = True
    else:
        raise ErrorArranque("%r es de tipo %s: se esperaba una lista de modulos "
                            "o el texto que los nombra" % (CLAVE, type(valor).__name__))
    if not nombres:
        raise ErrorArranque("%r no nombra ningun modulo importable: %r" % (CLAVE, valor))
    return nombres, alternativas


def importar_arranque(spec, registrar=None):
    """Import the motors the spec declares. Returns the list actually imported."""
    nombres, alternativas = modulos_declarados(spec)
    hechos, fallos = [], []
    for nombre in nombres:
        try:
            importlib.import_module(nombre)
        except Exception as exc:                       # noqa: BLE001 -- se republica
            fallos.append("%s -> %s: %s" % (nombre, type(exc).__name__, exc))
            continue
        hechos.append(nombre)
        if registrar is not None:
            registrar("arranque: importado %s" % nombre)
    if alternativas:
        if not hechos:
            raise ErrorArranque("ninguno de los modulos declarados en %r se pudo "
                                "importar: %s" % (CLAVE, " | ".join(fallos)))
    elif fallos:
        raise ErrorArranque("modulos declarados en %r que no importan: %s"
                            % (CLAVE, " | ".join(fallos)))
    return hechos
