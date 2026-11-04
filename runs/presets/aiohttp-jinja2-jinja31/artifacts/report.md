# ChainReaction report: upgrade jinja2 to 3.1

Target: `workspaces/aio-libs__aiohttp-jinja2@fd97cef408ec` (pre-existing, analyzed by ChainReaction) | jinja2: 2.11.2 -> 3.1.6

## Change set

- `Context.resolve`
- `jinja2.Context.resolve`
- `jinja2.Markup`
- `jinja2.autoescape`
- `jinja2.contextfilter`
- `jinja2.contextfunction`
- `jinja2.environmentfilter`
- `jinja2.environmentfunction`
- `jinja2.escape`
- `jinja2.evalcontextfilter`
- `jinja2.evalcontextfunction`
- `jinja2.ext.AutoEscapeExtension`
- `jinja2.ext.WithExtension`
- `jinja2.filters.unicode_urlencode`
- `jinja2.unicode_urlencode`
- `jinja2.with`
- `jinja2.with_`

Used by this repo (1): `jinja2.contextfunction`

Interpreter metrics: quote survival 1.00; blind recall n/a (0/0/12 table/list/pattern rows); guided recall n/a; completion added 0; model-only finds (15): `Context.resolve`, `jinja2.Context.resolve`, `jinja2.Markup`, `jinja2.autoescape`, `jinja2.contextfilter`, `jinja2.contextfunction`, `jinja2.environmentfilter`, `jinja2.environmentfunction`, `jinja2.evalcontextfilter`, `jinja2.evalcontextfunction`, `jinja2.ext.AutoEscapeExtension`, `jinja2.ext.WithExtension`, `jinja2.filters.unicode_urlencode`, `jinja2.with`, `jinja2.with_`; releases with notes: `2.11.3`, `3.0.0`, `3.0.1`, `3.0.2`, `3.0.3`, `3.1.0`, `3.1.1`, `3.1.2`, `3.1.3`, `3.1.4`, `3.1.5`, `3.1.6`.

## Prediction

HARD: 3 modules | AT_RISK: 0 nodes

Predicted layer-1 chain:

- `aiohttp_jinja2` -> `aiohttp_jinja2.helpers` (aiohttp_jinja2/__init__.py:24)
- `aiohttp_jinja2.helpers` -> `jinja2.contextfunction` (aiohttp_jinja2/helpers.py:23)
Chain vs layer-1 actual: MATCH

## Test suites

- before: 31 passed
- after: 4 errors
- new failures (0): none
- collateral upgrades: `markupsafe` 1.1.1->3.0.4

## Verification

### after-import (recall 1.00)

Actual: `aiohttp_jinja2` -> `aiohttp_jinja2.helpers`
Site: `aiohttp_jinja2/helpers.py:23`
```
    from .helpers import GLOBAL_HELPERS
  File "<ws>/aiohttp_jinja2/helpers.py", line 23, in <module>
    @jinja2.contextfunction
AttributeError: module 'jinja2' has no attribute 'contextfunction'
```

### before-import (recall 1.00)

Actual: (import ok)

## Fix loop

Status: green (green)

Round 1: probe aiohttp_jinja2/helpers.py:23 -> `aiohttp_jinja2/helpers.py` (probe FAILED)
Round 2: probe aiohttp_jinja2/helpers.py:61 -> `aiohttp_jinja2/helpers.py` (probe ok)

Counts: 0 fixed, 0 still failing, 0 newly broken (baseline: 0 failures)

Guards: clean

Patch: `fixes/final.patch`

## Baselines

- grep: 5 hits

## Narrative

Upgrading Jinja2 from 2.11.2 to 3.1.6 breaks the aiohttp_jinja2 package. The new version removes 17 features, but only one of them is actually used in this repository. Because of that use, 3 modules fail at import with no additional modules at risk.

The verified worst chain is aiohttp_jinja2 to aiohttp_jinja2.helpers, which matches the observed failure. The first module imports the second, and the second uses the removed Jinja2 helper at line 23 of its file.

Evidence for this chain is complete. The import works under the old version and fails under the new version, with both checks showing full agreement. Testing also shows 31 passes before the change and 4 errors after, with no new failures from the upgrade decision.

Fix the use in aiohttp_jinja2/helpers.py at line 23 first. The modules in the chain are importers and should not be edited directly, so repairing that site unblocks its chain in one step.


Claim check: ✓ clean
