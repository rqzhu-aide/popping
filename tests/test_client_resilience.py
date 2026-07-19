"""Static regressions for bounded browser API requests."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_all_browser_api_requests_use_one_bounded_json_helper():
    source = (PROJECT_ROOT / "static" / "js" / "app.js").read_text(
        encoding="utf-8"
    )

    helper_start = source.index("async function fetchJSONWithTimeout")
    helper_end = source.index("async function postJSON")
    helper = source[helper_start:helper_end]

    assert source.count("fetch(") == 1
    assert "const REQUEST_TIMEOUT_MS = 15000;" in source
    assert "const UPLOAD_TIMEOUT_MS = 30000;" in source
    assert source.count("UPLOAD_TIMEOUT_MS") == 3
    assert "new AbortController()" in helper
    assert "signal: controller.signal" in helper
    assert helper.index("await fetch(") < helper.index("await res.json()")
    assert helper.index("await res.json()") < helper.index("clearTimeout(timeout)")
