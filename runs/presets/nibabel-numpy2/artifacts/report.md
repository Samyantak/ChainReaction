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

Deprecated (warnings — still work, excluded from scores):

- `numpy.in1d`
- `numpy.row_stack`
- `numpy.trapz`

Removal-table recall: Muse Spark alone 6/62 → with deterministic table extraction 62/62 (complete)

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
```
             ^^^^^^^^^^
  File "<env:numpy2>/site-packages/numpy/__init__.py", line 411, in __getattr__
    raise AttributeError(
AttributeError: `np.sctypes` was removed in the NumPy 2.0 release. Access dtypes explicitly instead.. Did you mean: 'dtypes'?
```

### layer2 (peeled) (recall 1.00)

Actual: `nibabel` -> `nibabel.imagestats` -> `nibabel.imageclasses` -> `nibabel.cifti2` -> `nibabel.cifti2.cifti2` -> `nibabel.nifti1` -> `nibabel.quaternions`
```
                ^^^^^^^^^^^^^^^^^
  File "<env:numpy2>/site-packages/numpy/__init__.py", line 411, in __getattr__
    raise AttributeError(
AttributeError: `np.maximum_sctype` was removed in the NumPy 2.0 release. Use a specific dtype instead. You should avoid relying on any implicit mechanism and select the largest dtype of a kind explicitly in the code.
```

### before (numpy1) (recall 1.00)

Actual: (import ok)

## Baselines

- grep: 34 hits
- ruff NPY201: 52 hits

## Narrative

Moving nibabel to NumPy 2.0 breaks imports. Tested under NumPy 2.0.2, up from 1.26.4, 63 removed NumPy symbols cause failures, leaving 226 modules failing at import with 155 more at risk. Three other symbols are deprecated but still work, so they do not need immediate action.

The single verified worst chain that matches the observed failure starts at nibabel, which loads nibabel.analyze, which loads nibabel.arrayproxy, which loads nibabel.volumeutils, which loads nibabel.casting. Specifically, nibabel calls nibabel.analyze at nibabel/__init__.py line 41, nibabel.analyze calls nibabel.arrayproxy at nibabel/analyze.py line 88, nibabel.arrayproxy calls nibabel.volumeutils at nibabel/arrayproxy.py line 39, nibabel.volumeutils calls nibabel.casting at nibabel/volumeutils.py line 23, and nibabel.casting fails in ok_floats at nibabel/casting.py lines 723 and 717 when it uses a type dictionary removed in NumPy 2.0.

Evidence is very strong. Import fails under NumPy 2.0.2 in both direct and peeled checks, while import works under the older NumPy. Comparison with the real upstream fix shows every flagged file was correct, with 5 of 8 total changes found, including all 5 breaking changes, and no missed files. Three additional files with behavior changes were flagged separately for review.

Fix nibabel.casting first, replacing the removed type lookup in ok_floats, then work outward through nibabel.volumeutils, nibabel.arrayproxy, nibabel.analyze and nibabel to restore import.


Claim check: ✓ clean
