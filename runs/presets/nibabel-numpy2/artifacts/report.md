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

Removal-table recall: Muse Spark alone 12/65 → with deterministic table extraction 65/65 (complete)

## Prediction

HARD: 226 modules | AT_RISK: 150 nodes

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

Upgrading nibabel for NumPy 2.0 breaks imports under NumPy 2.0.2 compared with 1.26.4. The new version removes 63 symbols, but only five are used in this code and can cause failures here: numpy.compat, numpy.longcomplex, numpy.maximum_sctype, numpy.ndarray.newbyteorder and numpy.sctypes. Three other symbols still work though deprecated, and separate behavior differences are flagged. In total 226 modules fail at import and 150 more are at risk.

The single verified worst chain that matches the observed failure goes through nibabel to nibabel.analyze to nibabel.arrayproxy to nibabel.volumeutils to nibabel.casting. nibabel imports nibabel.analyze in nibabel/__init__.py at line 41, nibabel.analyze imports nibabel.arrayproxy in nibabel/analyze.py at line 88, nibabel.arrayproxy imports nibabel.volumeutils in nibabel/arrayproxy.py at line 39, and nibabel.volumeutils imports nibabel.casting in nibabel/volumeutils.py at line 23. That last module then runs nibabel.casting.ok_floats at import in nibabel/casting.py at line 723, which uses numpy.sctypes in nibabel/casting.py at line 717.

Evidence is strong for this brief. Import was shown to fail under NumPy 2.0.2 and to work under 1.26.4, with full coverage in each check. Comparison with https://github.com/nipy/nibabel/pull/1250 found every predicted file was correct and found 5 of 8 files overall, including 5 of 5 of the breaking changes, with no missed files in the main count. Three other files with behavior differences in nibabel/conftest.py, nibabel/freesurfer/io.py and nibabel/spatialimages.py were flagged separately.

Fix first the use in nibabel/casting.py at line 717, then address nibabel/quaternions.py at line 32. The modules in the chain above are importers, not edit sites, so fixing each site in order unblocks its chain in one step without editing the importers.

Claim check: ✓ clean
