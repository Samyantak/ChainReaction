# ChainReaction report: upgrade numpy to 2.0

Target: `workspaces/scverse__anndata@1461fecd1712` (pre-existing, analyzed by ChainReaction) | numpy: 1.26.4 -> 2.0.2

## Change set

- `f2py.compile`
- `numpy.ALLOW_THREADS`
- `numpy.ArrFuncs`
- `numpy.BUFSIZE`
- `numpy.CLIP`
- `numpy.FLOATING_POINT_SUPPORT`
- `numpy.Inf`
- `numpy.Infinity`
- `numpy.MAXDIMS`
- `numpy.MAY_SHARE_BOUNDS`
- `numpy.MAY_SHARE_EXACT`
- `numpy.MapIter`
- `numpy.NINF`
- `numpy.NPY_CHAR`
- `numpy.NPY_CONSTANT`
- `numpy.NPY_INTPLTR`
- `numpy.NPY_MAXARGS`
- `numpy.NPY_MAX_ELSIZE`
- `numpy.NPY_NTYPES`
- `numpy.NPY_REFCOUNT`
- `numpy.NPY_SIGINT_ON`
- `numpy.NZERO`
- `numpy.NaN`
- `numpy.PINF`
- `numpy.PZERO`
- `numpy.PyArrayFlagsObject`
- `numpy.PyArrayFlags_Type`
- `numpy.PyArray_CEQ`
- `numpy.PyArray_CGE`
- `numpy.PyArray_CGT`
- `numpy.PyArray_CLE`
- `numpy.PyArray_CLT`
- `numpy.PyArray_CNE`
- `numpy.PyArray_CONSTANT`
- `numpy.PyArray_CastAnyTo`
- `numpy.PyArray_CastBuffer`
- `numpy.PyArray_CastTo`
- `numpy.PyArray_CompareString`
- `numpy.PyArray_CompareUCS4`
- `numpy.PyArray_DescrFromType`
- `numpy.PyArray_FieldNames`
- `numpy.PyArray_FillObjectArray`
- `numpy.PyArray_GETITEM`
- `numpy.PyArray_GetCastFunc`
- `numpy.PyArray_GetNumericOps`
- `numpy.PyArray_ISPYTHON`
- `numpy.PyArray_MoveInto`
- `numpy.PyArray_NewFlagsObject`
- `numpy.PyArray_REFCNT`
- `numpy.PyArray_ScalarFromObject`
- `numpy.PyArray_SetNumericOps`
- `numpy.PyArray_TypestrConvert`
- `numpy.PyDataMem_SetEventHook`
- `numpy.PyDataType_GetDatetimeMetaData`
- `numpy.PyErr_CheckSignals`
- `numpy.PyOS_InterruptOccurred`
- `numpy.PyUFunc_GetPyVals`
- `numpy.PyUFunc_checkfperr`
- `numpy.PyUFunc_handlefperr`
- `numpy.RAISE`
- `numpy.UFUNC_BUFSIZE_DEFAULT`
- `numpy.UFUNC_PYVALS_NAME`
- `numpy.WRAP`
- `numpy.add_docstring`
- `numpy.add_newdoc`
- `numpy.add_newdoc_ufunc`
- `numpy.array_api`
- `numpy.asfarray`
- `numpy.binary_repr`
- `numpy.cast`
- `numpy.cfloat`
- `numpy.char.chararray`
- `numpy.chararray`
- `numpy.charrarray`
- `numpy.clongfloat`
- `numpy.compare_chararrays`
- `numpy.complex_`
- `numpy.core.umath_tests`
- `numpy.deprecate`
- `numpy.deprecate_with_doc`
- `numpy.distutils`
- `numpy.exceptions`
- `numpy.exceptions.ComplexWarning`
- `numpy.exceptions.VisibleDeprecationWarning`
- `numpy.fastCopyAndTranspose`
- `numpy.fastclip`
- `numpy.fastputmask`
- `numpy.fasttake`
- `numpy.fft.helper`
- `numpy.find_common_type`
- `numpy.float_`
- `numpy.format_parser`
- `numpy.geterrobj`
- `numpy.infty`
- `numpy.intp`
- `numpy.issctype`
- `numpy.issubclass_`
- `numpy.issubsctype`
- `numpy.legacy_inner_loop_selector`
- `numpy.lib`
- `numpy.lib.npyio`
- `numpy.linalg.linalg`
- `numpy.longcomplex`
- `numpy.longfloat`
- `numpy.lookfor`
- `numpy.mat`
- `numpy.maximum_sctype`
- `numpy.meson`
- `numpy.msort`
- `numpy.nbytes`
- `numpy.obj2sctype`
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
- `numpy.source`
- `numpy.string_`
- `numpy.tracemalloc_domain`
- `numpy.unicode_`
- `numpy.who`
- `subprocess.run`
- `ufunc.at`

Used by this repo (2): `numpy.NaN`, `numpy.string_`

Deprecated (warnings — still work, excluded from scores):

- `ast.literal_eval`
- `numpy.None`
- `numpy.__array_wrap__`
- `numpy._core`
- `numpy.arr`
- `numpy.char`
- `numpy.compat`
- `numpy.context`
- `numpy.core`
- `numpy.cross`
- `numpy.disp`
- `numpy.dtype`
- `numpy.get_array_wrap`
- `numpy.iinfo`
- `numpy.in1d`
- `numpy.int8`
- `numpy.rcond`
- `numpy.return_scalar`
- `numpy.row_stack`
- `numpy.strings`
- `numpy.trapz`

Behavior-change flags (excluded from scores):

- `numpy.linalg.lstsq`

Removal-table recall: Muse Spark alone 10/113 → with deterministic table extraction 32/113 (missed: `numpy.ALLOW_THREADS`, `numpy.ArrFuncs`, `numpy.BUFSIZE`, `numpy.CLIP`, `numpy.FLOATING_POINT_SUPPORT`, `numpy.MAXDIMS`, `numpy.MAY_SHARE_BOUNDS`, `numpy.MAY_SHARE_EXACT`, `numpy.MapIter`, `numpy.NINF`, `numpy.NPY_CHAR`, `numpy.NPY_CONSTANT`, `numpy.NPY_INTPLTR`, `numpy.NPY_MAX_ELSIZE`, `numpy.NPY_REFCOUNT`, `numpy.NPY_SIGINT_ON`, `numpy.NZERO`, `numpy.PINF`, `numpy.PZERO`, `numpy.PyArrayFlagsObject`, `numpy.PyArrayFlags_Type`, `numpy.PyArray_CONSTANT`, `numpy.PyArray_CastAnyTo`, `numpy.PyArray_CastBuffer`, `numpy.PyArray_CastTo`, `numpy.PyArray_CompareString`, `numpy.PyArray_CompareUCS4`, `numpy.PyArray_DescrFromType`, `numpy.PyArray_FieldNames`, `numpy.PyArray_FillObjectArray`, `numpy.PyArray_GetCastFunc`, `numpy.PyArray_GetNumericOps`, `numpy.PyArray_ISPYTHON`, `numpy.PyArray_MoveInto`, `numpy.PyArray_NewFlagsObject`, `numpy.PyArray_REFCNT`, `numpy.PyArray_ScalarFromObject`, `numpy.PyArray_SetNumericOps`, `numpy.PyArray_TypestrConvert`, `numpy.PyDataMem_SetEventHook`, `numpy.PyDataType_GetDatetimeMetaData`, `numpy.PyErr_CheckSignals`, `numpy.PyOS_InterruptOccurred`, `numpy.PyUFunc_GetPyVals`, `numpy.PyUFunc_checkfperr`, `numpy.PyUFunc_handlefperr`, `numpy.RAISE`, `numpy.UFUNC_BUFSIZE_DEFAULT`, `numpy.UFUNC_PYVALS_NAME`, `numpy.WRAP`, `numpy.add_docstring`, `numpy.add_newdoc`, `numpy.add_newdoc_ufunc`, `numpy.binary_repr`, `numpy.char.chararray`, `numpy.chararray`, `numpy.charrarray`, `numpy.core.umath_tests`, `numpy.deprecate`, `numpy.deprecate_with_doc`, `numpy.exceptions`, `numpy.exceptions.ComplexWarning`, `numpy.exceptions.VisibleDeprecationWarning`, `numpy.fastCopyAndTranspose`, `numpy.fastclip`, `numpy.fastputmask`, `numpy.fasttake`, `numpy.intp`, `numpy.issctype`, `numpy.issubsctype`, `numpy.legacy_inner_loop_selector`, `numpy.lib`, `numpy.lib.npyio`, `numpy.maximum_sctype`, `numpy.obj2sctype`, `numpy.recfromtxt`, `numpy.safe_eval`, `numpy.sctype2char`, `numpy.sctypes`, `numpy.set_numeric_ops`, `ufunc.at`)

Interpreter metrics: quote survival 1.00; blind recall 0.09 (0/128/21 table/list/pattern rows); guided recall 0.28; completion added 109; model-only finds (5): `numpy.compare_chararrays`, `numpy.format_parser`, `numpy.linalg.lstsq`, `numpy.recfromcsv`, `numpy.tracemalloc_domain`; releases with notes: `2.0.0`, `2.0.1`, `2.0.2`.

## Prediction

HARD: 0 modules | AT_RISK: 17 nodes

Import chain is clean (import succeeds); worst damage is at call time:

- `tests/test_concatenate.py::test_batch_key[0]` runs `anndata._core.merge.Reindexer._apply_to_df` which uses `numpy.NaN` (`src/anndata/_core/merge.py:552`)

## Test suites

- before: 16 errors, 1 failed, 4243 passed, 607 skipped
- after: 19 errors, 602 failed, 3634 passed, 607 skipped
- new failures (612): `tests/test_concatenate.py::test_batch_key[0]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-inner-None-no_obs]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-inner-None-no_var]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-inner-first-no_obs]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-inner-first-no_var]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-inner-only-no_obs]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-inner-only-no_var]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-inner-same-no_obs]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-inner-same-no_var]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-inner-unique-no_obs]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-inner-unique-no_var]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-outer-None-no_obs]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-outer-None-no_var]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-outer-first-no_obs]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-outer-first-no_var]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-outer-only-no_obs]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-outer-only-no_var]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-outer-same-no_obs]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-outer-same-no_var]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-outer-unique-no_obs]`, +592 more
  - 317 x AttributeError: `np.string_` was removed in the NumPy 2.0 release. Use `np.bytes_` instead.. Did you mean: 'strings'?
  - 285 x AttributeError: `np.NaN` was removed in the NumPy 2.0 release. Use `np.nan` instead.. Did you mean: 'nan'?
  - 9 x AttributeError: `np.float_` was removed in the NumPy 2.0 release. Use `np.float64` instead.. Did you mean: 'float16'?
  - 1 x ValueError: Unable to avoid copy while creating an array as requested.
- predicted failing (592): `tests/test_concatenate.py::test_batch_key[0]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-inner-None-no_obs]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-inner-None-no_var]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-inner-first-no_obs]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-inner-first-no_var]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-inner-only-no_obs]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-inner-only-no_var]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-inner-same-no_obs]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-inner-same-no_var]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-inner-unique-no_obs]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-inner-unique-no_var]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-outer-None-no_obs]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-outer-None-no_var]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-outer-first-no_obs]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-outer-first-no_var]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-outer-only-no_obs]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-outer-only-no_var]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-outer-same-no_obs]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-outer-same-no_var]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-outer-unique-no_obs]`, +572 more
- test precision/recall: 1.00/0.97
- example failure: `tests/test_concatenate.py::test_batch_key[0]` runs `anndata._core.merge.Reindexer._apply_to_df` which uses `numpy.NaN` (`src/anndata/_core/merge.py:552`)
- collateral upgrades: `llvmlite` 0.42.0->0.50.0, `numba` 0.59.1->0.68.1

## Verification

### after-import (recall 1.00)

Actual: (import ok)

### before-import (recall 1.00)

Actual: (import ok)

## Fix loop

Status: partial (max-rounds)

Round 1: probe src/anndata/__init__.py:13 -> `src/anndata/__init__.py` (probe ok)
Round 2: test ci/scripts/min-deps.py::ci.scripts.min-deps.min_dep -> `conftest.py` (probe ok)

Counts: 9 fixed, 603 still failing, 6 newly broken (baseline: 1 failures)

Guards: clean

Patch: `fixes/final.patch`

## Baselines

- grep: 87 hits
- ruff NPY201: 5 hits

## Narrative

Upgrading anndata tested under NumPy 2.0.2 from 1.26.4 to NumPy 2.0 breaks because NumPy 2.0 removed 128 names and this repository still uses two of them. Nothing fails while importing, so the problem only appears when the code runs, with 17 additional places at risk when those names are reached.

The single verified chain of damage matches the observed failure and stays clean at import. One example test runs through Reindexer _apply_to_df in anndata._core.merge which uses the removed NaN name at line 552 of src/anndata/_core/merge.py. That call time use is the representative failure path.

Confidence is high for this brief. Before the change there were 16 errors with 1 failed and 4243 passed and 607 skipped, while after there were 19 errors with 602 failed and 3634 passed and 607 skipped, for 612 new failures caused by the change. Prediction expected 592 tests to fail with precision 1.00 and recall 0.97, and import checks work in both layers with recall 1.00. The failures split into 317 cases from the removed string name, 285 from the removed NaN name, 9 from the removed float name, and 1 from an inability to avoid a copy while creating an array.

Fix the removed name use in anndata._core.merge first, replacing the NaN use with the supported lowercase form and doing the same for the string name uses elsewhere. The 21 deprecated names still work and the behavior flag for linalg lstsq is not driving the current failures, so leave those for later.


Claim check: ✓ clean
