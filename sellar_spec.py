"""lotes/sellar_spec.py -- F222: seal a lot spec, once and only once.

A JSON file cannot carry an appended seal and stay valid JSON, so the seal of a
spec lives in the sibling file <run_id>.json.seal and covers the WHOLE json
[fuente: docs/DISENO_COLAB_LOTES.md seccion 1.a, bloque SEAL, consultado
2026-09-17]. The four lines follow the house convention already used by
docs/PREREGISTRO_F187_RL_SPY_v2.md lines 454-457 [fuente: ese fichero, lineas
454-457, consultado 2026-09-17]:

    SEAL sha256 = <hex>   (over the WHOLE file <path>)
    SEAL git     = <commit sha> <ISO8601Z>
    SEAL time    = <ISO8601Z>
    SEAL reproduce: sha256sum <path>

RULES, all of them refusals and none of them a default:
  - RESEALING IS REFUSED. If the .seal exists, this script exits 3 and changes
    nothing. A seal that can be rewritten seals nothing. To change a sealed spec
    you write a NEW run_id: that is the whole point of a pre-registration.
  - The spec must PARSE and must carry every field the motor requires; a spec
    that is sealed and then refused at run time has wasted the seal.
  - The git HEAD is recorded as the identity of the run. If the spec file is NOT
    yet committed at that HEAD the seal says so on its own comment line, because
    a seal that quietly points at a commit that does not contain the file is a
    lie told with a hash.

SUBCOMMANDS
  sellar <spec.json>      write <spec.json>.seal (exit 0) or refuse (2, 3, 4)
  verificar <spec.json>   recompute and compare against the existing seal
  subarbol [--raiz R]     print the code_subtree block (path + sha256) to paste
                          into a spec before sealing it

EXIT CODES: 0 ok | 2 spec unreadable/incomplete | 3 already sealed | 4 seal
mismatch on verificar | 5 git unavailable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

_AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(_AQUI))          # mirror layout: trading-lotes/motor/
sys.path.insert(0, str(_AQUI.parent))   # repo layout:   trading-system/lotes/motor/

try:  # the same file must import under BOTH layouts, mirror and repo
    from lotes.motor.nucleo import ErrorLote, ficheros_subarbol, valida_spec  # noqa: E402
except ImportError:  # pragma: no cover - exercised only inside the mirror
    from motor.nucleo import ErrorLote, ficheros_subarbol, valida_spec  # noqa: E402


def _sha256(ruta):
    return hashlib.sha256(Path(ruta).read_bytes()).hexdigest()


def _git(repo, *args):
    salida = subprocess.run(
        ["git", "-C", str(repo)] + list(args), capture_output=True, text=True, check=False
    )
    if salida.returncode != 0:
        raise ErrorLote("git " + " ".join(args) + " fallo: " + salida.stderr.strip())
    return salida.stdout.strip()


def sellar(ruta_spec, repo=None):
    ruta_spec = Path(ruta_spec).resolve()
    sello = Path(str(ruta_spec) + ".seal")
    if sello.exists():
        print("RECHAZO: ya existe " + str(sello) + " -- un sello no se reescribe, se usa otro run_id",
              file=sys.stderr)
        return 3
    try:
        spec = json.loads(ruta_spec.read_text(encoding="utf-8"))
        valida_spec(spec)
    except Exception as exc:
        print("RECHAZO: spec ilegible o incompleta: " + str(exc), file=sys.stderr)
        return 2
    repo = Path(repo).resolve() if repo else ruta_spec.parent
    try:
        commit = _git(repo, "rev-parse", "HEAD")
        fecha = _git(repo, "show", "-s", "--format=%cd", "--date=format-local:%Y-%m-%dT%H:%M:%SZ", commit)
    except ErrorLote as exc:
        print("RECHAZO: " + str(exc), file=sys.stderr)
        return 5
    try:
        rel = ruta_spec.relative_to(_git(repo, "rev-parse", "--show-toplevel"))
        seguido = _git(repo, "ls-files", "--error-unmatch", str(rel)) != ""
    except Exception:
        rel = ruta_spec.name
        seguido = False
    hexa = _sha256(ruta_spec)
    texto = (
        "SEAL sha256 = " + hexa + "  (over the WHOLE file " + str(rel) + ")\n"
        "SEAL git     = " + commit + " " + fecha + "\n"
        "SEAL time    = " + time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + "\n"
        "SEAL reproduce: sha256sum " + str(rel) + "\n"
        "# run_id = " + str(spec["run_id"]) + "\n"
        "# spec EN GIT en ese commit: " + ("si" if seguido else "NO (fichero aun sin commitear)") + "\n"
    )
    sello.write_text(texto, encoding="utf-8")
    print("SELLADO " + str(sello))
    print("SEAL sha256 = " + hexa)
    print("SEAL git     = " + commit)
    return 0


def verificar(ruta_spec):
    ruta_spec = Path(ruta_spec).resolve()
    sello = Path(str(ruta_spec) + ".seal")
    if not sello.is_file():
        print("RECHAZO: spec SIN SELLAR: " + str(sello), file=sys.stderr)
        return 4
    grabado = ""
    for linea in sello.read_text(encoding="utf-8").splitlines():
        if linea.startswith("SEAL sha256"):
            grabado = linea.split("=", 1)[1].strip().split()[0]
    real = _sha256(ruta_spec)
    if grabado != real:
        print("RECHAZO: sha256 " + real + " != sellado " + grabado, file=sys.stderr)
        return 4
    print("SELLO OK " + real)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="sella una spec de lote (F222)")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("sellar")
    a.add_argument("spec")
    a.add_argument("--repo", default=None)
    b = sub.add_parser("verificar")
    b.add_argument("spec")
    c = sub.add_parser("subarbol")
    c.add_argument("--raiz", default=None)
    args = p.parse_args(argv)
    if args.cmd == "sellar":
        return sellar(args.spec, args.repo)
    if args.cmd == "verificar":
        return verificar(args.spec)
    print(json.dumps(ficheros_subarbol(args.raiz), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
