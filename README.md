# The branchless mesh

A program for routing logic that contains no `if`, no `else`, no `switch`, and no
conditional expression at all — *and a test that enforces it.*

Every fork in the road is decided by asking an energy model (JEV or LAYA) a
multiple-choice question and following whatever it answers. The flow you want to
build is written as plain text in a `.rule` file: layers, the question each layer
asks, the candidate branches, and what to call when you reach the end.

```
            .rule text                engine                    model
   ┌──────────────────────┐   ┌────────────────────┐   ┌────────────────────┐
   │ @node billing_play   │   │ ledger = seed text │   │ JEV   (remote)     │
   │ @ask how to settle?  │──▶│ + one line per     │──▶│ LAYA  (local)      │
   │ @option escalate ... │   │   absorbed fact    │◀──│ picks one option   │
   │ @option refund   ... │   │                    │   │ + confidence       │
   └──────────────────────┘   └─────────┬──────────┘   └────────────────────┘
                                        │
                                        ▼
                          next layer, or a final action
```

## Why

Normally a decision tree is code: `if urgency > 2 and churn then escalate`. That
is a hand-written policy that only changes when a programmer changes it, and it
can only see structured fields somebody remembered to define.

Here the decision is a question. The model reads the *entire situation as prose*
— the original report plus every fact absorbed so far — and picks one of the
branches you wrote down. Adding a nuance means editing a sentence, not shipping a
release. And because the routing question and the signal questions are separate,
you get *why*: a probability spread, a confidence, and a readable reading of each
signal.

The cost of this is real: it is slower than a comparison, it can be wrong, and
LAYAs confidences in these meshes are often low when the candidates are genuinely
close. That is the experiment.

## Install

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt     # Windows
# python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # macOS/Linux
```

JEV is remote and needs a key:

```bash
echo 'OPENROUTER_API_KEY=sk-or-...' > .env.local
```

`.env.local` is gitignored. LAYA needs no key — it downloads its checkpoint on
first use, then runs locally.

## Run it

```bash
python -m engine --list                       # what meshes exist
python -m engine --flow refund_triage         # backend from the rule's @suggest
python -m engine --flow incident_response -b laya
python -m engine --flow loan_underwriting -b jev --json
```

| flag | meaning |
| --- | --- |
| `--flow`, `-f` | rule file path, or a bare name under `rules/` |
| `--backend`, `-b` | `jev` or `laya` — **switch before running** |
| `--seed`, `-s` | the free-text situation the run starts from |
| `--live` | really perform `http` actions instead of simulating |
| `--json` | emit one JSON object per line instead of the rendered trace |
| `--max-layers` | safety budget, default 80 |
| `--list` | show every rule file with its size and suggested backend |

### The console

```bash
python -m webui          # http://127.0.0.1:8765
```

Pick a flow, flip the **JEV / LAYA** toggle, paste a situation (or click one of
the example seeds), and press run. The trace streams over SSE as the model
decides: each layer shows the question, the chosen branch and its semantics, the
full probability spread, and every signal it read. The state ledger column shows
the exact text the model was asked about, growing as the run proceeds. The editor
saves a `.rule` file only if it parses.

## The language

One directive per line, `|` to separate fields. Blank lines and `#` comments are
ignored.

```text
@rule    Refund triage                    # the title shown in the UI
@about   Routes a billing complaint...    # one paragraph
@suggest jev                              # default backend
@start   classify                         # the first layer
@example We were billed twice...          # a ready-made seed for the UI

@node classify                            # ── a layer ──────────────────
@effect  record | triage-started           #   runs before the layer asks
@ask     Which capability should own this? #  the routing question
@option  billing | invoices, refunds  | -> billing_play
@option  risk    | suspected fraud    | !  trigger | fraud-desk | freeze | frozen

@node billing_play
@signal  urgency | score | how much revenue is at risk | none | some | blocking:sev1
@signal  churn   | noul  | is the customer threatening to leave | yes = threat | no = no threat
@signal  lane    | choice| which queue owns it | self_serve = FAQ | assisted = agent
@ask     How should billing settle this?
@option  refund  | refund it now | ! trigger | payments | refunded | Refund issued
@option  escalate| risky or churning | ! trigger | pager | billing-lead | Page the lead

@node never_used
@end     Unreachable in practice      # a terminal layer: fires, then stops
```

### Directives

| directive | purpose |
| --- | --- |
| `@rule` / `@about` / `@suggest` / `@start` / `@example` | header, once each |
| `@node <key>` | open a layer |
| `@ask <text>` | the routing question this layer hands the model |
| `@note <text>` | prose shown to the human, never to the model |
| `@effect <action> \| arg \| arg` | an action that runs *before* the layer asks |
| `@option <key> \| <semantics> \| -> <node>` | a branch that continues the mesh |
| `@option <key> \| <semantics> \| ! <action> \| arg \| arg` | a branch that ends the run |
| `@signal <name> \| score \| <question> \| band1 \| band2 \| band3` | a value read, not a branch |
| `@signal <name> \| noul \| <question> \| yes = <wording> \| no = <wording>` | a yes/no read |
| `@signal <name> \| choice \| <question> \| key = <wording> \| key = <wording>` | a category read |
| `@end <text>` | a terminal layer |

### Three kinds of question

**Routing** is always a `choice` question. The `@option` entries *are* the
options, and each option's `semantics` text is what the model reads when it
decides — so write it as a description of when that branch is right.

**Signals** are extra reads on a layer. They never steer the flow; they are
recorded in the ledger for later layers to reason over.

- `score` — a number from 0 to `len(bands) - 1`, shaped like a banded likert
  scale. Positive scores only; a band index, not a threshold.
- `noul` — a yes/no probability. Criteria must be spelled `yes`/`no` (or
  `y`/`n`/`true`/`false`); anything else is a parse error.
- `choice` — a category from the keys you list.

### Actions

`! <action> | arg | arg` fires on arrival at a terminal option. Built-ins:

| action | effect |
| --- | --- |
| `trigger` | `trigger \| channel \| target \| message` — the usual "call this" |
| `emit` | record a named event |
| `annotate` | append a fact to the ledger, so later layers can see it |
| `record` | append a line to `runs/<flow>.jsonl` |
| `http` | a real request when `--live`, simulated otherwise |

Unknown action names do not crash the run — they land in the trace as
`unknown-action` with the registry listed, so a typo is visible rather than fatal.
`plugins.py` in the project root can add your own; see `slack_ping`, `page` and
`freeze_account` there.

## The one allowed `if`

`engine/core/backends/factory.py` contains a single `if` — the JEV/LAYA choice,
and nothing else. `tests/test_purity.py` AST-parses every file under `engine/` and
fails the build on any other `if`, `if`-expression, `match`, or comprehension
filter, and on any `==`, `!=`, `<=`, `>=`, `&&` or `||` in a `.rule` file.

Because of that, the engine has no place to hide a policy. If a branch happens,
a model chose it.

The one concession is `main.py` at the project root: it holds the standard
`if __name__ == "__main__"` guard. That is not a decision and it lives outside the
scanned package. `python -m engine` needs no guard at all.

Everything here is table-driven instead. Where you would expect
`if kind == LAYA: ... else: ...` you will find:

```python
_CRITERIA_BUILDERS = {
    CHOICE: lambda c: {"criteria": dict(c or {})},
    SCORE:  lambda c: {"criteria": list(c or [])},
    NOUL:   _noul_payload,
}
```

## Layout

```
engine/                 the branchless core — this is the part the test guards
  backends/             JEV, LAYA, and the factory with the single if
  rules/                the .rule language: model, parser
  core/                 state ledger, action registry, the walk itself
  cli.py                branchless command line runner
rules/                  the meshes, as text
webui/                  FastAPI console (needs its own ifs; it decides nothing)
tools/validate_rules.py reachability, dangling links, a path count per mesh
plugins.py              your own actions
tests/                  purity, parser, engine, CLI
runs/                   JSONL written by the `record` action
```

## The meshes

| mesh | shape |
| --- | --- |
| `refund_triage` | 3 layers, 5 nodes, 11 distinct paths — the small readable one |
| `incident_response` | 5 layers, 17 nodes, ~560 distinct paths — autonomous incident commander |
| `loan_underwriting` | 5 layers, 17 nodes, ~545 distinct paths — credit underwriting pipeline |

Check any mesh for unreachable layers, links that go nowhere, and how many
distinct routes it really has:

```bash
python -m tools.validate_rules
```

## Tests

```bash
python -m pytest -q
```

The interesting one is `tests/test_purity.py`. It also rejects conditional
operators in `.rule` files, because a rule file is data — the moment it can
express `if`, the whole point is gone.

## Known sharp edges

- **LAYAs confidences are often low** when two candidate branches are genuinely
  close — 0.01 is normal at a coin-flip layer. Read the probability spread, not
  the confidence alone. LAYA also warns on load that part of its checkpoint ships
  uncalibrated temperatures.
- **LAYAs routing is not deterministic** between runs. The same seed can take a
  different branch. That is a property of the model, not a bug in the engine.
- **The ledger grows monotonically.** By layer 5, the model is reading every fact
  absorbed since layer 1, and input tokens climb with it. That is deliberate —
  later layers see earlier reasoning — but it is the thing to watch on cost.
