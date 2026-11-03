# ChainReaction report: upgrade werkzeug from 2.2 to 3.0

Target: `workspaces/maxcountryman__flask-login@31e20a3cc143` (pre-existing, analyzed by ChainReaction) | numpy: MISSING (before: MISSING)

## Change set

- `auth.key`
- `warnings.catch_warnings`
- `werkzeug.auth`
- `werkzeug.datastructures.WWWAuthenticate.set_basic`
- `werkzeug.datastructures.WWWAuthenticate.set_digest`
- `werkzeug.delete_cookie`
- `werkzeug.domain`
- `werkzeug.http.parse_authorization_header`
- `werkzeug.http.parse_www_authenticate_header`
- `werkzeug.max_form_parts`
- `werkzeug.path`
- `werkzeug.routing.Map.charset`
- `werkzeug.urls`
- `werkzeug.wrappers.Request.charset`
- `werkzeug.wrappers.Request.url_charset`
- `werkzeug.wsgi.make_chunk_iter`
- `werkzeug.wsgi.make_line_iter`

Used by this repo (1): `werkzeug.urls`

Deprecated (warnings — still work, excluded from scores):

- `Client.charset`
- `WWWAuthenticate.set_basic`
- `http.cookiejar`
- `werkzeug.Location`
- `werkzeug.__version__`
- `werkzeug.charset`
- `werkzeug.dump_cookie`
- `werkzeug.generate_password_hash`
- `werkzeug.iri_to_uri`
- `werkzeug.make_chunk_iter`
- `werkzeug.parse_cookie`
- `werkzeug.parse_www_authenticate_header`
- `werkzeug.quote_header_value`
- `werkzeug.set_digest`
- `werkzeug.unquote_header_value`
- `werkzeug.value`

Behavior-change flags (excluded from scores):

- `werkzeug.security.generate_password_hash`
- `werkzeug.wrappers.Request.get_json`

Interpreter metrics: quote survival 1.00; blind recall n/a (0/0/26 table/list/pattern rows); guided recall n/a; completion added 23; model-only finds (18): `werkzeug.datastructures.WWWAuthenticate.set_basic`, `werkzeug.datastructures.WWWAuthenticate.set_digest`, `werkzeug.http.dump_cookie`, `werkzeug.http.dump_header`, `werkzeug.http.parse_authorization_header`, `werkzeug.http.parse_dict_header`, `werkzeug.http.parse_www_authenticate_header`, `werkzeug.http.quote_header_value`, `werkzeug.http.unquote_header_value`, `werkzeug.routing.Map.charset`, `werkzeug.security.generate_password_hash`, `werkzeug.urls`, `werkzeug.urls.iri_to_uri`, `werkzeug.wrappers.Request.charset`, `werkzeug.wrappers.Request.get_json`, `werkzeug.wrappers.Request.url_charset`, `werkzeug.wsgi.make_chunk_iter`, `werkzeug.wsgi.make_line_iter`; releases with notes: `2.3.0`, `2.3.1`, `2.3.2`, `2.3.3`, `2.3.4`, `2.3.5`, `2.3.6`, `2.3.7`, `2.3.8`, `3.0.0`, `3.0.1`, `3.0.2`, `3.0.3`, `3.0.4`, `3.0.5`, `3.0.6`.

## Prediction

HARD: 0 modules | AT_RISK: 3 nodes

Import chain is clean (import succeeds); worst damage is at call time:

- `tests/test_login.py::LoginTestCase::test_needs_refresh_fires_flash_when_redirect_to_refresh_view` runs `flask_login.utils.login_url` which uses `werkzeug.urls.url_decode` (`src/flask_login/utils.py:126`)

## Test suites

- before: 113 passed
- after: 2 errors
- new failures (111): `tests/test_login.py::AnonymousUserTestCase::test_values`, `tests/test_login.py::CookieEncodingTestCase::test_cookie_encoding`, `tests/test_login.py::CookieEncodingTestCase::test_cookie_encoding_with_key`, `tests/test_login.py::CustomTestClientTestCase::test_fresh_login_arg_to_test_client`, `tests/test_login.py::CustomTestClientTestCase::test_no_args_to_test_client`, `tests/test_login.py::CustomTestClientTestCase::test_session_protection_modes`, `tests/test_login.py::CustomTestClientTestCase::test_user_arg_to_test_client`, `tests/test_login.py::InitializationTestCase::test_class_init`, `tests/test_login.py::InitializationTestCase::test_init_app`, `tests/test_login.py::InitializationTestCase::test_no_user_loader_raises`, `tests/test_login.py::LoginTestCase::test_authentication_is_fresh`, `tests/test_login.py::LoginTestCase::test_confirm_login`, `tests/test_login.py::LoginTestCase::test_decorators_are_disabled`, `tests/test_login.py::LoginTestCase::test_defaults_anonymous`, `tests/test_login.py::LoginTestCase::test_fresh_login_required_decorator`, `tests/test_login.py::LoginTestCase::test_incorrect_id_logs_out`, `tests/test_login.py::LoginTestCase::test_invalid_remember_cookie`, `tests/test_login.py::LoginTestCase::test_login_inactive_user`, `tests/test_login.py::LoginTestCase::test_login_inactive_user_forced`, `tests/test_login.py::LoginTestCase::test_login_invalid_user_with_request`, +91 more
  - 111 x other
- predicted failing (14): `tests/test_login.py::LoginTestCase::test_needs_refresh_fires_flash_when_redirect_to_refresh_view`, `tests/test_login.py::LoginTestCase::test_needs_refresh_flash_message_localized`, `tests/test_login.py::LoginTestCase::test_redirects_to_refresh_view`, `tests/test_login.py::LoginTestCase::test_set_login_view_without_blueprints`, `tests/test_login.py::LoginTestCase::test_unauthorized_flash_message_localized`, `tests/test_login.py::LoginTestCase::test_unauthorized_flashes_message_with_login_view`, `tests/test_login.py::LoginTestCase::test_unauthorized_redirects_to_login_view`, `tests/test_login.py::LoginTestCase::test_unauthorized_uses_blueprint_login_view`, `tests/test_login.py::StrictHostForRedirectsTestCase::test_unauthorized_ignores_host_from_x_forwarded_for_header`, `tests/test_login.py::StrictHostForRedirectsTestCase::test_unauthorized_uses_host_from_config_when_available`, `tests/test_login.py::StrictHostForRedirectsTestCase::test_unauthorized_uses_host_from_next_url`, `tests/test_login.py::StrictHostForRedirectsTestCase::test_unauthorized_uses_host_from_x_forwarded_for_header`, `tests/test_login.py::TestLoginUrlGeneration::test_login_url_generation`, `tests/test_login.py::TestLoginUrlGeneration::test_login_url_generation_with_view`
- test precision/recall: 1.00/0.13
- example failure: `tests/test_login.py::LoginTestCase::test_needs_refresh_fires_flash_when_redirect_to_refresh_view` runs `flask_login.utils.login_url` which uses `werkzeug.urls.url_decode` (`src/flask_login/utils.py:126`)

## Verification

### after-import (recall 1.00)

Actual: (import ok)
```
    from .utils import _create_identifier
  File "<env:after>/site-packages/flask_login/utils.py", line 14, in <module>
    from werkzeug.urls import url_decode
ImportError: cannot import name 'url_decode' from 'werkzeug.urls' (<env:after>/site-packages/werkzeug/urls.py)
```

### before-import (recall 1.00)

Actual: (import ok)

## Baselines

- grep: 29 hits

## Narrative

Upgrading werkzeug from 2.2 to 3.0 breaks the flask_login package. Seventeen symbols changed in the new release, but only one of them is actually used by this repository, the address helper werkzeug.urls. Sixteen other uses are deprecated but still work, and two behavior changes were noted. No modules fail at import, though three more are at risk.

The verified worst chain of damage is clean at import time, which matches the observed failure. Nothing breaks until the code actually runs. For example, a login test for refresh handling calls login_url in flask_login.utils, which in turn uses url decoding from werkzeug.urls around line 126 in src/flask_login/utils.py.

The evidence for cause is strong but prediction was narrow. Before the upgrade all 113 tests passed, and after the upgrade there were 2 errors with 111 new failures caused by the decision. Checks confirmed the affected import path works before and fails after with full agreement. A forecast that flagged 14 tests was right every time it flagged, but it captured only a small share of the total failures.

Fix the removed helper use first. Update the call in flask_login.utils around line 126 to stop using the removed symbol from werkzeug.urls, then re-run the login tests and review the three at-risk modules for the same helper and for the two flagged behavior changes.

Claim check: ✓ clean
