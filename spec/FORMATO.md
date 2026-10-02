# `lotes/spec/` -- format of a sealed lot spec

One lot = one `run_id` = two files, committed together and never edited again:

    lotes/spec/<run_id>.json        the spec
    lotes/spec/<run_id>.json.seal   its seal (sha256 of the WHOLE json + git HEAD)

The machine-checkable version of this page is `esquema_spec.schema.json`
(JSON Schema draft 2020-12). The motor refuses a spec that misses ANY required
field: a missing field is never defaulted
[fuente: docs/DISENO_COLAB_LOTES.md seccion 1.a, consultado 2026-09-17].

| field | meaning |
|---|---|
| `run_id` | identity of the lot; also the Drive folder and the results folder |
| `code_commit` | commit of `trading-lotes` whose subtree the job executes |
| `code_subtree` | list of `{path, sha256}`; the job proves it runs the sealed code |
| `data_cut` | `{"tipo":"SINTETICO","generador":...}` or `{"tipo":"FICHERO","path":...,"sha256":...}` |
| `seed_master` | the master seed, a string |
| `derivacion_semilla` | must be the canonical rule, verbatim |
| `calculo` | key of the registry in `motor/nucleo.py` (`CALCULOS`) |
| `unidades` | U, the number of work units |
| `parametros` | the computation's own numbers (e.g. `n_por_unidad`) |
| `reglas_parada` | deciding statistic, threshold, max wall seconds per unit, max RAM, heartbeat |
| `outputs` | the exact artifact names the run must produce; anything else is junk |
| `ledger_id` | the PROSPECTIVE line of `docs/LEDGER_MULTIPLICIDAD.jsonl` |
| `entradas` | OPTIONAL (F236): the lot's INPUT files -- `{carpeta, variable_entorno, ficheros:[{nombre,bytes,sha256}]}` |
| `arranque_runtime` | OPTIONAL: the motors the runner imports before `correr_lote` (list, or the text naming them); consumed by `lotes/arranque.py` |
| `valores_esperados` | OPTIONAL, pilots only: per-unit `sha256_dato` (and media/desviacion for humans) |
| `reference_runtime_s` | OPTIONAL: seconds MEASURED for the whole lot on the VPS |

The canonical seed rule, which must appear verbatim in `derivacion_semilla`:

    sha256(seed_master||':'||i)[:16] -> SeedSequence -> PCG64

`W` (max wall-clock seconds per unit) lives in `reglas_parada.max_segundos_unidad`
and exists because the batch runtime dies without warning: a kill costs at most W
[fuente: docs/DISENO_COLAB_LOTES.md seccion 1.c, consultado 2026-09-17].

## Sealing

    ./venv/bin/python lotes/sellar_spec.py subarbol            # paste into code_subtree
    ./venv/bin/python lotes/sellar_spec.py sellar lotes/spec/<run_id>.json
    ./venv/bin/python lotes/sellar_spec.py verificar lotes/spec/<run_id>.json

Re-sealing is REFUSED (exit 3). A spec that must change gets a new `run_id`.

## `entradas`: the INPUT half of the circuit (F236, 2026-09-22)

A lot's input files NEVER travel in the public repository: `scripts/check_trading_lotes.py`
classifies `.npz .parquet .csv .zip` as "dato en bruto" and marks it PROHIBIDO, and
`lotes/datos/*/ ` is git-ignored here. They travel in the USER'S Drive, in

    CARPETA_DRIVE/entradas/<run_id>/<nombre>

one flat folder per run, no subdirectories. The spec pins them:

    "entradas": {
      "carpeta": "entradas/<run_id>",          # OPTIONAL, this is the default
      "variable_entorno": "LOTE_DATOS_TORTUGAS",  # OPTIONAL, default LOTE_ENTRADAS
      "ficheros": [{"nombre": "panel_a.npz", "bytes": 6365438, "sha256": "<64 hex>"}]
    }

Cell 5 of `lotes/colab_lote.ipynb` runs BEFORE the first draw and AFTER the seal
cell, so it checks against the SEALED spec: set equality with the folder (a missing
file and an EXTRA file are both refusals), then size and sha256 of every file, then
`os.environ[variable_entorno] = DIR_ENTRADAS` -- the ONE path variable the motor
reads (`lotes/motor/tortugas.py` already honours `LOTE_DATOS_TORTUGAS`). It writes
`entradas.json` into the run folder with the hashes it OBSERVED; that file is an
artifact like any other, so the manifest signs it, and `scripts/aceptar_lote.py`
refuses (exit 14) a lot whose cited input set is not the sealed one.

A spec that declares `entradas` must list `entradas.json` among its `outputs`, and
the input files must be in the Drive folder BEFORE the notebook is run: the
notebook downloads nothing from the network.

`ficheros` is a SET: a repeated `nombre` is refused by cell 5 ("ENTRADA DUPLICADA")
and the byte-identical duplicate is also refused by the schema (`uniqueItems`). The
folder is FLAT and holds regular files only: a subdirectory, a symlink to a
directory or any other non-regular entry found inside it is a refusal that names it.

MEASURED ASYMMETRY between the two ends of the circuit (review of 2026-09-22,
finding C4). At the INPUT end a declared file that is a SYMLINK is ACCEPTED as long
as the bytes behind it match the declared size and sha256 -- even if the target
lives outside the Drive folder -- because what the spec pins is the CONTENT, and
the content is what the run reads. At the OUTPUT end the returned lot refuses a
symlink outright (`scripts/aceptar_lote.py`, exit 8), because there what is being
defended is the file tree that lands in the repository. This is a statement of
fact, not a defect being fixed: the two ends defend different things.
