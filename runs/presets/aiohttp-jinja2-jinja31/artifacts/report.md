# ChainReaction report: upgrade jinja2 to 3.1

Target: `workspaces/aio-libs__aiohttp-jinja2@fd97cef408ec` (pre-existing, analyzed by ChainReaction) | numpy: MISSING (before: MISSING)

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

HARD: 0 modules | AT_RISK: 2 nodes

Predicted layer-1 chain:

Chain vs layer-1 actual: DIFFERS

## Test suites

- before: 31 passed
- after: 4 errors
- new failures (0): none
- collateral upgrades: `markupsafe` 1.1.1->3.0.4

## Verification

### after-import (recall 0.00)

Actual: `aiohttp_jinja2` -> `aiohttp_jinja2.helpers`
Site: `aiohttp_jinja2/helpers.py:23`
Missed: `aiohttp_jinja2`, `aiohttp_jinja2.helpers`
```
    from .helpers import GLOBAL_HELPERS
  File "<ws>/aiohttp_jinja2/helpers.py", line 23, in <module>
    @jinja2.contextfunction
AttributeError: module 'jinja2' has no attribute 'contextfunction'
```

### before-import (recall 1.00)

Actual: (import ok)

## Baselines

- grep: 5 hits

## Narrative

Upgrading to jinja2 3.1 in the aiohttp_jinja2 package would remove 17 symbols from jinja2, but only one of those is actually used in this repository. That single used removal is the only direct source of failure here. The analysis was done without a recorded NumPy version.

No module was confirmed to fail at import, but two modules are marked at risk, aiohttp_jinja2 and aiohttp_jinja2.helpers. The one chain examined came back clean and differs from the observed failure, so there is no verified chain of damage that matches what was actually seen.

Confidence in the evidence is limited. The check that assumed imports fail missed both at-risk modules entirely, while the check that assumed imports still work captured the risk fully. Test results in this brief show 31 passes before and 4 errors after, with none counted as new failures from this upgrade choice.

Fix first at line 23 in aiohttp_jinja2/helpers.py. The modules named above are importers, not places to edit, so fixing that site should unblock its chain in one step without editing the importers themselves.


Claim check: ✓ clean
