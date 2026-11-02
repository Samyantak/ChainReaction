# ChainReaction report: upgrade numpy to 2.0

Target: `targets/nibabel` (pre-existing, analyzed by ChainReaction) | numpy: 2.0.2 (before: 1.26.4)

## Change set

- `numpy.DataSource`
- `numpy.Inf`
- `numpy.Infinity`
- `numpy.NINF`
- `numpy.NZERO`
- `numpy.NaN`
- `numpy.PINF`
- `numpy.PZERO`
- `numpy.add_docstring`
- `numpy.add_newdoc`
- `numpy.add_newdoc_ufunc`
- `numpy.alltrue`
- `numpy.asfarray`
- `numpy.byte_bounds`
- `numpy.cast`
- `numpy.cfloat`
- `numpy.charrarray`
- `numpy.clongfloat`
- `numpy.compare_chararrays`
- `numpy.compat`
- `numpy.complex_`
- `numpy.core`
- `numpy.cumproduct`
- `numpy.deprecate`
- `numpy.deprecate_with_doc`
- `numpy.disp`
- `numpy.fastCopyAndTranspose`
- `numpy.find_common_type`
- `numpy.float_`
- `numpy.format_parser`
- `numpy.get_array_wrap`
- `numpy.geterrobj`
- `numpy.infty`
- `numpy.issctype`
- `numpy.issubclass_`
- `numpy.issubsctype`
- `numpy.longcomplex`
- `numpy.longfloat`
- `numpy.lookfor`
- `numpy.mat`
- `numpy.maximum_sctype`
- `numpy.nbytes`
- `numpy.ndarray.newbyteorder`
- `numpy.ndarray.ptp`
- `numpy.ndarray.setitem`
- `numpy.obj2sctype`
- `numpy.product`
- `numpy.recfromcsv`
- `numpy.recfromtxt`
- `numpy.round_`
- `numpy.safe_eval`
- `numpy.sctype2char`
- `numpy.sctypes`
- `numpy.set_numeric_ops`
- `numpy.set_string_function`
- `numpy.seterrobj`
- `numpy.singlecomplex`
- `numpy.sometrue`
- `numpy.source`
- `numpy.string_`
- `numpy.tracemalloc_domain`
- `numpy.unicode_`
- `numpy.who`

Used by this repo (5): `numpy.compat`, `numpy.longcomplex`, `numpy.maximum_sctype`, `numpy.ndarray.newbyteorder`, `numpy.sctypes`

Deprecated (warnings — still work, excluded from scores):

- `numpy.in1d`
- `numpy.row_stack`
- `numpy.trapz`

Behavior-change flags (excluded from scores):

- `numpy.float32`
- `numpy.intp`
- `numpy.promote_types`

Removal-table recall: Muse Spark alone 9/62 → with deterministic table extraction 62/62 (complete)

## Prediction

HARD: 226 modules | AT_RISK: 155 nodes

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
Files needing edits: precision 1.00, recall 0.62 (5/8 PR files flagged; 5/5 of the breaking changes)
Missed: none
Behavior changes (flagged, not predicted): `nibabel/conftest.py`, `nibabel/freesurfer/io.py`, `nibabel/spatialimages.py`

## Verification

### layer1 (recall 1.00)

Actual: `nibabel` -> `nibabel.analyze` -> `nibabel.arrayproxy` -> `nibabel.volumeutils` -> `nibabel.casting`
Site: `nibabel/casting.py:717`
```
             ^^^^^^^^^^
  File "<env:numpy2>/site-packages/numpy/__init__.py", line 411, in __getattr__
    raise AttributeError(
AttributeError: `np.sctypes` was removed in the NumPy 2.0 release. Access dtypes explicitly instead.. Did you mean: 'dtypes'?
```

### layer2 (peeled) (recall 1.00)

Actual: `nibabel` -> `nibabel.imagestats` -> `nibabel.imageclasses` -> `nibabel.cifti2` -> `nibabel.cifti2.cifti2` -> `nibabel.nifti1` -> `nibabel.quaternions`
Site: `nibabel/quaternions.py:32`
```
                ^^^^^^^^^^^^^^^^^
  File "<env:numpy2>/site-packages/numpy/__init__.py", line 411, in __getattr__
    raise AttributeError(
AttributeError: `np.maximum_sctype` was removed in the NumPy 2.0 release. Use a specific dtype instead. You should avoid relying on any implicit mechanism and select the largest dtype of a kind explicitly in the code.
```

### before (numpy1) (recall 1.00)

Actual: (import ok)

## Baselines

- grep: 315 hits
- ruff NPY201: 52 hits

## Narrative

Moving nibabel to NumPy 2.0 breaks imports at scale. Tested under NumPy 2.0.2, up from 1.26.4, 226 modules fail at import and 155 more are at risk. Of 63 removed NumPy symbols overall, only 5 are used in this package, so only those can cause failures here. Three other used symbols still work but are deprecated, and three behavior changes are flagged separately.

The verified worst path matches the observed failure and runs from nibabel through nibabel.analyze and nibabel.arrayproxy and nibabel.volumeutils to nibabel.casting. nibabel imports nibabel.analyze at nibabel/__init__.py:41, nibabel.analyze imports nibabel.arrayproxy at nibabel/analyze.py:88, nibabel.arrayproxy imports nibabel.volumeutils at nibabel/arrayproxy.py:39, and nibabel.volumeutils imports nibabel.casting at nibabel/volumeutils.py:23. nibabel.casting then runs nibabel.casting.ok_floats at import at nibabel/casting.py:723, which uses the removed numpy.sctypes at nibabel/casting.py:717.

Evidence is strong. The import fails under NumPy 2.0.2 and works under 1.26.4, including after peeling back outer layers. Compared against the real fix, every flagged file was correct and all 5 breaking files were found, with 5 of 8 changed files overall and no missed files among the breaking changes.

Fix nibabel/casting.py:717 first, then nibabel/quaternions.py:32. The modules in the chain above are importers, not edit sites, so do not edit them to work around the failure. Fixing each edit site in order unblocks its chain in one step. The three files with behavior changes, nibabel/conftest.py, nibabel/freesurfer/io.py and nibabel/spatialimages.py, should be reviewed separately.

Claim check: ✓ clean
