# `lotes/` -- the ONLY subtree that may be mirrored into `trading-lotes`

Everything in this directory is written to be readable by a machine we do not
control. Treat it as public even though the mirror repository is private.

## 1. WHAT THE MIRROR IS, AND WHY THE VPS CANNOT PUSH IT

`trading-lotes` is a SEPARATE, PRIVATE repository of the user, with a
**read-only deploy key limited to that repository**; the key was generated on the
VPS and only its public half was pasted into GitHub
[fuente: condiciones del usuario 2026-09-17, cola id 222]. The private half lives
at `~/.ssh/github_lotes_ro` on the VPS and is never read, copied or quoted by any
agent -- only its PATH is.

**CONSEQUENCE, and it is not a detail: a read-only key CANNOT PUSH.** The mirror
step is therefore MANUAL and belongs to the user:

1. the VPS produces `lotes/` (spec + `.seal` + motor + notebook) inside the
   private repo `trading-system`;
2. the USER uploads the CONTENTS of `lotes/` to `trading-lotes` (web upload, or a
   local clone with his own credential), so the mirror's root holds
   `motor/`, `spec/`, `piloto/`, `sellar_spec.py`, `colab_lote.ipynb`;
3. the user reports the COMMIT SHA of that upload; that sha is what the notebook
   checks out and what the run quotes.

Nothing is automated in step 2 today, and nothing here tries to hide that. If the
user later grants a WRITE key scoped to `trading-lotes`, a push step can be added
-- it would be a change to this file and to nothing else.

The mirror is a ONE-WAY copy: nothing ever comes back from `trading-lotes` into
`trading-system` as code. What comes back is DATA, through
`scripts/aceptar_lote.py`, and only after it is verified.

## 2. WHAT MAY LIVE HERE

- `spec/<run_id>.json` and `spec/<run_id>.json.seal`: the sealed pre-registration
  of a lot. Numbers, seeds, unit counts, stop rules. No paths of the private repo.
- `motor/`: PURE COMPUTATION. Code that takes a seed and numbers and returns
  numbers. numpy and the standard library only.
- `sellar_spec.py`: the sealer. It reads a spec and writes its seal.
- `colab_lote.ipynb`: the notebook that runs a lot on a free batch runtime.
- `piloto/`: the synthetic pilot lot, whose result is KNOWN and pinned.

## 3. WHAT MAY NEVER LIVE HERE

`.env*`, anything under `state/`, `configs/deployment/`, `logs/`,
`progress/mailbox.md`, `results/` of live bots, anything matching
`GO_LIVE_APPROVED*`, any broker or exchange API key, anything under `~/.ssh`, any
file of PURCHASED data, and the private repository itself in any form, including
a partial clone [fuente: docs/DISENO_COLAB_LOTES.md seccion 4.1, consultado
2026-09-17].

Purchased data is a hard NO until its licence is read and quoted: the default
rule is that it never leaves the VPS [fuente: docs/DISENO_COLAB_LOTES.md seccion
4.2, consultado 2026-09-17].

## 4. THE CYCLE, END TO END

    1  write lotes/spec/<run_id>.json          (all fields pinned, none defaulted)
    2  ./venv/bin/python lotes/sellar_spec.py sellar lotes/spec/<run_id>.json
    3  USER uploads lotes/ to trading-lotes and reports the commit sha
    4  the notebook clones THAT sha, mounts the user's Drive, verifies the seal,
       runs the motor with the Drive folder as the checkpoint directory
    5  scripts/bajar_lote_drive.py --folder-id <id> --destino <dir>   (or by hand)
    6  ./venv/bin/python scripts/aceptar_lote.py --run-dir <dir> \
           --spec lotes/spec/<run_id>.json           (--dry-run first, always)
    7  accepted -> results/lotes/<run_id>/ at 0444 + one line in
       docs/LOTES_ACEPTADOS.jsonl

A lot that fails step 6 is refused WHOLE. There is no partial acceptance and no
manual fix of a delivered file: a corrected lot is a NEW run_id.

## 5. THE ONE THING THE MANIFEST DOES NOT PROVE

The manifest proves TRANSPORT integrity -- that what arrived is what the folder
held. It proves NOTHING about the computation, because whoever controls the batch
machine controls the artifacts and the manifest at the same time. What closes
that gap is the re-computation of randomly drawn units ON THE VPS
[fuente: docs/DISENO_COLAB_LOTES.md seccion 4.3, consultado 2026-09-17]. A result
that would move money is re-run IN FULL here, or it gates nothing.

## DESVIACIONES DECLARADAS (siguen abiertas tras el pase 2)

Ninguna se arregla con codigo; las cuatro las comprueba el CIO ANTES del primer
lote REAL [fuente: research/f222_andamiaje/review_f222.md condicion 7]:

1. **PRECONDICION DURA**: la linea PROSPECTIVA de `docs/LEDGER_MULTIPLICIDAD.jsonl`
   (hipotesis, unidades, criterio de parada) tiene que existir **ANTES** de correr
   cualquier lote real. Es lo unico que separa un circuito de lotes de la
   multiplicidad silenciosa. El andamiaje NO la exige por codigo.
2. El commit del sello no se comprueba por ANCESTRIA (`git cat-file`): el subarbol
   `lotes/` aun no esta en un commit publico.
3. `code_commit = PENDIENTE_SUBIDA_USUARIO` hasta que el usuario suba el subarbol
   al repo publico de lotes.
4. El camino de red de `scripts/bajar_lote_drive.py` esta **SIN VERIFICAR** (aqui
   no hay red por diseno): solo su parseo offline tiene pruebas.

## COMO SE RETIRA UN LOTE ACEPTADO

Lo aceptado queda 0555/0444 a proposito. Para retirarlo:
`chmod -R u+w results/lotes/<run_id> && rm -rf results/lotes/<run_id>`, y se anota
la retirada en `docs/LOTES_ACEPTADOS.jsonl` (el ledger es append-only).
