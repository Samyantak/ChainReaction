# ChainReaction report: upgrade numpy to 2.0

Target: `workspaces/scverse__anndata@1461fecd1712` (pre-existing, analyzed by ChainReaction) | numpy: 2.0.2 (before: 1.26.4)

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

Used by this repo (2): `numpy.NaN`, `numpy.string_`

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

HARD: 0 modules | AT_RISK: 17 nodes

Predicted layer-1 chain:

Chain vs layer-1 actual: MATCH

## Test suites

- before: 16 errors, 1 failed, 4243 passed, 607 skipped, 144 xfailed
- after: 19 errors, 602 failed, 3634 passed, 607 skipped, 144 xfailed
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

## Baselines

- grep: 125 hits
- ruff NPY201: 5 hits

## Narrative

Upgrading the anndata codebase to NumPy 2.0 breaks call-time use of removed symbols. Tested under NumPy 2.0.2 compared with 1.26.4, no modules fail at import but 17 more are at risk. Of 63 removed symbols in the release, only 2 are used in this repository, while 3 deprecated symbols still work and 3 behavior changes were flagged. The test suite moves from 16 errors with 1 failed and 4243 passed to 19 errors with 602 failed and 3634 passed, with 607 skipped and 144 expected failures unchanged, for 612 new failures caused by the upgrade.

The verified worst damage has a clean import chain and matches the observed failure at call time. An example is tests/test_concatenate.py::test_batch_key[0] which runs anndata._core.merge.Reindexer._apply_to_df which uses the removed NaN symbol at src/anndata/_core/merge.py:552.

Evidence is strong for this brief. Import checks succeed in both before and after layers with recall 1.00. Prediction of failing tests covers 592 tests with precision 1.00 and recall 0.97. Most failures are AttributeError for the removed string and NaN symbols, with 317 for one and 285 for the other, plus 9 for the removed float symbol and 1 copy error.

Fix the removed-symbol line at src/anndata/_core/merge.py:552 first, replacing use of the removed NaN symbol, then address the removed string symbol and the float symbol.

Claim check: ✓ clean
