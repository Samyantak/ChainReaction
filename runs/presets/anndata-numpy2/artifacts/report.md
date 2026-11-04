# ChainReaction report: upgrade numpy to 2.0

Target: `workspaces/scverse__anndata@1461fecd1712` (pre-existing, analyzed by ChainReaction) | numpy: 1.26.4 -> 2.0.2

## Change set

- `f2py.compile`
- `inspect.getsource`
- `noprefix.h`
- `npy_interrupt.h`
- `numpy.0`
- `numpy.0j`
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
- `numpy.NPY_`
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
- `numpy.asarray`
- `numpy.asfarray`
- `numpy.binary_repr`
- `numpy.bool8`
- `numpy.bytes0`
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
- `numpy.int0`
- `numpy.intp`
- `numpy.issctype`
- `numpy.issubclass_`
- `numpy.issubsctype`
- `numpy.kernel_version`
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
- `numpy.npy_`
- `numpy.numarray`
- `numpy.obj2sctype`
- `numpy.object0`
- `numpy.oldnumeric`
- `numpy.recfromcsv`
- `numpy.recfromtxt`
- `numpy.result_type`
- `numpy.round_`
- `numpy.safe_eval`
- `numpy.sctype2char`
- `numpy.sctypes`
- `numpy.set_numeric_ops`
- `numpy.set_string_function`
- `numpy.seterrobj`
- `numpy.singlecomplex`
- `numpy.sort`
- `numpy.source`
- `numpy.str0`
- `numpy.string_`
- `numpy.tracemalloc_domain`
- `numpy.uint0`
- `numpy.unicode_`
- `numpy.void0`
- `numpy.who`
- `subprocess.run`
- `ufunc.at`

Used by this repo (3): `numpy.NaN`, `numpy.asarray`, `numpy.string_`

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
- `scipy.integrate`

Behavior-change flags (excluded from scores):

- `numpy.linalg.lstsq`

Removal-table recall: Muse Spark alone 10/133 → with deterministic table extraction 38/133 (missed: `inspect.getsource`, `noprefix.h`, `npy_interrupt.h`, `numpy.0`, `numpy.0j`, `numpy.ALLOW_THREADS`, `numpy.ArrFuncs`, `numpy.BUFSIZE`, `numpy.CLIP`, `numpy.FLOATING_POINT_SUPPORT`, `numpy.MAXDIMS`, `numpy.MAY_SHARE_BOUNDS`, `numpy.MAY_SHARE_EXACT`, `numpy.MapIter`, `numpy.NINF`, `numpy.NPY_`, `numpy.NPY_CHAR`, `numpy.NPY_CONSTANT`, `numpy.NPY_INTPLTR`, `numpy.NPY_MAX_ELSIZE`, `numpy.NPY_REFCOUNT`, `numpy.NPY_SIGINT_ON`, `numpy.NZERO`, `numpy.PINF`, `numpy.PZERO`, `numpy.PyArrayFlagsObject`, `numpy.PyArrayFlags_Type`, `numpy.PyArray_CONSTANT`, `numpy.PyArray_CastAnyTo`, `numpy.PyArray_CastBuffer`, `numpy.PyArray_CastTo`, `numpy.PyArray_CompareString`, `numpy.PyArray_CompareUCS4`, `numpy.PyArray_DescrFromType`, `numpy.PyArray_FieldNames`, `numpy.PyArray_FillObjectArray`, `numpy.PyArray_GetCastFunc`, `numpy.PyArray_GetNumericOps`, `numpy.PyArray_ISPYTHON`, `numpy.PyArray_MoveInto`, `numpy.PyArray_NewFlagsObject`, `numpy.PyArray_REFCNT`, `numpy.PyArray_ScalarFromObject`, `numpy.PyArray_SetNumericOps`, `numpy.PyArray_TypestrConvert`, `numpy.PyDataMem_SetEventHook`, `numpy.PyDataType_GetDatetimeMetaData`, `numpy.PyErr_CheckSignals`, `numpy.PyOS_InterruptOccurred`, `numpy.PyUFunc_GetPyVals`, `numpy.PyUFunc_checkfperr`, `numpy.PyUFunc_handlefperr`, `numpy.RAISE`, `numpy.UFUNC_BUFSIZE_DEFAULT`, `numpy.UFUNC_PYVALS_NAME`, `numpy.WRAP`, `numpy.add_docstring`, `numpy.add_newdoc`, `numpy.add_newdoc_ufunc`, `numpy.array_api`, `numpy.asarray`, `numpy.binary_repr`, `numpy.bool8`, `numpy.bytes0`, `numpy.char.chararray`, `numpy.chararray`, `numpy.charrarray`, `numpy.core.umath_tests`, `numpy.exceptions`, `numpy.exceptions.ComplexWarning`, `numpy.exceptions.VisibleDeprecationWarning`, `numpy.fastCopyAndTranspose`, `numpy.fastclip`, `numpy.fastputmask`, `numpy.fasttake`, `numpy.int0`, `numpy.intp`, `numpy.kernel_version`, `numpy.legacy_inner_loop_selector`, `numpy.lib`, `numpy.lib.npyio`, `numpy.npy_`, `numpy.numarray`, `numpy.object0`, `numpy.oldnumeric`, `numpy.recfromcsv`, `numpy.recfromtxt`, `numpy.result_type`, `numpy.set_numeric_ops`, `numpy.sort`, `numpy.str0`, `numpy.tracemalloc_domain`, `numpy.uint0`, `numpy.void0`, `ufunc.at`)

Interpreter metrics: quote survival 1.00; blind recall 0.08 (0/149/21 table/list/pattern rows); guided recall 0.29; completion added 124; model-only finds (1): `numpy.linalg.lstsq`; releases with notes: `2.0.0`, `2.0.1`, `2.0.2`.

## Prediction

HARD: 36 modules | AT_RISK: 92 nodes

Predicted layer-1 chain:

- `anndata` -> `anndata._core.anndata` (src/anndata/__init__.py:24)
- `anndata._core.anndata` -> `anndata.utils` (src/anndata/_core/anndata.py:43)
- `anndata.utils` -> `anndata.utils.asarray` (src/anndata/utils.py:47)
- `anndata.utils.asarray` -> `numpy.asarray` (src/anndata/utils.py:44)
Chain vs layer-1 actual: DIFFERS

## Test suites

- before: 16 errors, 1 failed, 4243 passed, 607 skipped, 144 xfailed
- after: 19 errors, 602 failed, 3634 passed, 607 skipped, 144 xfailed
- new failures (612): `tests/test_concatenate.py::test_batch_key[0]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-inner-None-no_obs]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-inner-None-no_var]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-inner-first-no_obs]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-inner-first-no_var]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-inner-only-no_obs]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-inner-only-no_var]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-inner-same-no_obs]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-inner-same-no_var]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-inner-unique-no_obs]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-inner-unique-no_var]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-outer-None-no_obs]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-outer-None-no_var]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-outer-first-no_obs]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-outer-first-no_var]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-outer-only-no_obs]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-outer-only-no_var]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-outer-same-no_obs]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-outer-same-no_var]`, `tests/test_concatenate.py::test_concat_size_0_dim[0-outer-unique-no_obs]`, +592 more
  - 317 x AttributeError: `np.string_` was removed in the NumPy 2.0 release. Use `np.bytes_` instead.. Did you mean: 'strings'?
  - 285 x AttributeError: `np.NaN` was removed in the NumPy 2.0 release. Use `np.nan` instead.. Did you mean: 'nan'?
  - 9 x AttributeError: `np.float_` was removed in the NumPy 2.0 release. Use `np.float64` instead.. Did you mean: 'float16'?
  - 1 x ValueError: Unable to avoid copy while creating an array as requested.
- predicted failing (3230): `tests/test_anncollection.py::test_full_selection[array]`, `tests/test_anncollection.py::test_full_selection[csr_matrix]`, `tests/test_backed_sparse.py::test_anndata_sparse_compat[h5ad]`, `tests/test_backed_sparse.py::test_anndata_sparse_compat[zarr]`, `tests/test_backed_sparse.py::test_append_overflow_check[csr_matrix-h5py]`, `tests/test_backed_sparse.py::test_append_overflow_check[csr_matrix-zarr]`, `tests/test_backed_sparse.py::test_backed_indexing[array_bool_subset-array_bool_subset-h5ad]`, `tests/test_backed_sparse.py::test_backed_indexing[array_bool_subset-array_bool_subset-zarr]`, `tests/test_backed_sparse.py::test_backed_indexing[array_bool_subset-array_int_subset-h5ad]`, `tests/test_backed_sparse.py::test_backed_indexing[array_bool_subset-array_int_subset-zarr]`, `tests/test_backed_sparse.py::test_backed_indexing[array_bool_subset-array_subset-h5ad]`, `tests/test_backed_sparse.py::test_backed_indexing[array_bool_subset-array_subset-zarr]`, `tests/test_backed_sparse.py::test_backed_indexing[array_bool_subset-list_bool_subset-h5ad]`, `tests/test_backed_sparse.py::test_backed_indexing[array_bool_subset-list_bool_subset-zarr]`, `tests/test_backed_sparse.py::test_backed_indexing[array_bool_subset-list_int_subset-h5ad]`, `tests/test_backed_sparse.py::test_backed_indexing[array_bool_subset-list_int_subset-zarr]`, `tests/test_backed_sparse.py::test_backed_indexing[array_bool_subset-matrix_bool_subset-h5ad]`, `tests/test_backed_sparse.py::test_backed_indexing[array_bool_subset-matrix_bool_subset-zarr]`, `tests/test_backed_sparse.py::test_backed_indexing[array_bool_subset-single_subset-h5ad]`, `tests/test_backed_sparse.py::test_backed_indexing[array_bool_subset-single_subset-zarr]`, +3210 more
- test precision/recall: 0.18/0.97
- example failure: `tests/test_concatenate.py::test_batch_key[0]` runs `anndata._core.merge.Reindexer._apply_to_df` which uses `numpy.NaN` (`src/anndata/_core/merge.py:552`)
- collateral upgrades: `llvmlite` 0.42.0->0.50.0, `numba` 0.59.1->0.68.1

## Verification

### after-import (recall 1.00)

Actual: (import ok)

### before-import (recall 1.00)

Actual: (import ok)

## Baselines

- grep: 572 hits
- ruff NPY201: 5 hits

## Narrative

Upgrading anndata to work under NumPy 2.0.2, up from 1.26.4, broke compatibility because removed NumPy features used by this code are gone. The impact is broad with 36 modules failing at import and 92 more at risk. Test results show 612 new failures, moving from 1 failed test before to 602 failed after. The failures are dominated by the removed string alias with 317 errors and the removed NaN alias with 285 errors, plus 9 errors from the removed float alias and 1 copy error.

The single verified worst chain is anndata to anndata._core.anndata to anndata.utils. The top package imports anndata._core.anndata, which in turn imports anndata.utils, where a helper that runs at import time relies on the removed array creation routine. This chain differs from the observed failure, which instead goes through the merge helper in the concatenation test that uses the removed NaN alias.

Evidence for the chain is strong on imports. Checks made just before import and just after import both show the import still works with recall of 1.00. Broader failure prediction catches almost everything with recall near 0.97 but over-predicts heavily, forecasting 3230 failures with precision around 0.18.

Fix first the NaN and string helpers that cause almost all test failures, then address the array helper in anndata.utils that drives the import-time chain. Also review the single flagged behavior change in the linear least-squares routine before declaring the upgrade safe.


Claim check: ✓ clean
