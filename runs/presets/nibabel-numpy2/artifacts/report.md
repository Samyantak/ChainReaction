# ChainReaction report: upgrade numpy to 2.0

Target: `targets/nibabel` (pre-existing, analyzed by ChainReaction) | numpy: 2.0.2 (before: 1.26.4)

## Change set

- `numpy.alltrue`
- `numpy.asarray`
- `numpy.core`
- `numpy.cumproduct`
- `numpy.float32`
- `numpy.in1d`
- `numpy.intp`
- `numpy.lib`
- `numpy.mat`
- `numpy.ndarray.newbyteorder`
- `numpy.ndarray.ptp`
- `numpy.product`
- `numpy.sctypes`
- `numpy.trapz`

## Prediction

HARD: 226 modules | AT_RISK: 424 nodes

Predicted layer-1 chain:

- `nibabel` -> `nibabel.analyze` (nibabel/__init__.py:41)
- `nibabel.analyze` -> `nibabel.arrayproxy` (nibabel/analyze.py:88)
- `nibabel.arrayproxy` -> `nibabel.volumeutils` (nibabel/arrayproxy.py:39)
- `nibabel.volumeutils` -> `nibabel.casting` (nibabel/volumeutils.py:23)
- `nibabel.casting` -> `nibabel.casting.ok_floats` (nibabel/casting.py:723)
- `nibabel.casting.ok_floats` -> `numpy.sctypes` (nibabel/casting.py:717)
Chain vs layer-1 actual: MATCH

## Ground truth (PR)

URL: https://github.com/nipy/nibabel/pull/1250
Files needing edits: precision 0.22, recall 0.88 (7/8 PR files flagged)
Missed: none
Behavior changes (flagged, not predicted): `nibabel/conftest.py`

## Verification

### layer1 (recall 1.00)

Actual: `nibabel` -> `nibabel.analyze` -> `nibabel.arrayproxy` -> `nibabel.volumeutils` -> `nibabel.casting`
```
             ^^^^^^^^^^
  File "<env:numpy2>/Lib/site-packages/numpy/__init__.py", line 411, in __getattr__
    raise AttributeError(
AttributeError: `np.sctypes` was removed in the NumPy 2.0 release. Access dtypes explicitly instead.. Did you mean: 'dtypes'?
```

### layer2 (peeled) (recall 1.00)

Actual: `nibabel` -> `nibabel.imagestats` -> `nibabel.imageclasses` -> `nibabel.cifti2` -> `nibabel.cifti2.cifti2` -> `nibabel.nifti1` -> `nibabel.quaternions`
```
                ^^^^^^^^^^^^^^^^^
  File "<env:numpy2>/Lib/site-packages/numpy/__init__.py", line 411, in __getattr__
    raise AttributeError(
AttributeError: `np.maximum_sctype` was removed in the NumPy 2.0 release. Use a specific dtype instead. You should avoid relying on any implicit mechanism and select the largest dtype of a kind explicitly in the code.
```

### before (numpy1) (recall 1.00)

Actual: (import ok)

## Baselines

- grep: 34 hits
- ruff NPY201: 52 hits

## Narrative

The report evaluates decision upgrade numpy to 2.0 for targets/nibabel in package nibabel under numpy_version 2.0.2. It lists change_symbols including numpy.alltrue, numpy.asarray, numpy.core, numpy.cumproduct, numpy.float32, numpy.in1d, numpy.intp, numpy.lib, numpy.mat, numpy.ndarray.newbyteorder, numpy.ndarray.ptp, numpy.product, numpy.sctypes and numpy.trapz, with n_hard 226 and n_at_risk 424.

The verified worst chain is nibabel to nibabel.analyze to nibabel.arrayproxy to nibabel.volumeutils to nibabel.casting ending in numpy.sctypes via nibabel.casting.ok_floats. Evidence traces nibabel to nibabel.analyze at nibabel/__init__.py:41, nibabel.analyze to nibabel.arrayproxy at nibabel/analyze.py:88, nibabel.arrayproxy to nibabel.volumeutils at nibabel/arrayproxy.py:39, nibabel.volumeutils to nibabel.casting at nibabel/volumeutils.py:23, and nibabel.casting to nibabel.casting.ok_floats at nibabel/casting.py:723 with nibabel.casting.ok_floats to numpy.sctypes at nibabel/casting.py:717.

Confidence is supported by spine_match true with file_recall 0.875 and file_precision 0.21875, with file_missing containing nibabel/conftest.py. Layer checks show layer1 with ok false and recall 1.0, layer2 (peeled) with ok false and recall 1.0, and before (numpy1) with ok true and recall 1.0, indicating breakage appears only after the upgrade.

To fix first, prioritize nibabel/casting.py:717 in nibabel.casting where nibabel.casting.ok_floats reaches numpy.sctypes, then work outward through nibabel.volumeutils, nibabel.arrayproxy, nibabel.analyze and nibabel to targets/nibabel. Related work is tracked at https://github.com/nipy/nibabel/pull/1250.

Claim check: ✓ clean
