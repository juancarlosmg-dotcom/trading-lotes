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
