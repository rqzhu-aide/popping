"""Regression checks for the shared Popping brand assets and templates."""

from pathlib import Path
import struct

import app as app_module


PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMAGE_ROOT = PROJECT_ROOT / "static" / "images"


def test_supplied_brand_sources_and_optimized_asset_are_present():
    for name in ("popping.png", "popping-alt.png", "popping-fav.png"):
        path = IMAGE_ROOT / name
        assert path.is_file()
        assert path.stat().st_size > 0

    optimized = IMAGE_ROOT / "popping-brand.webp"
    contents = optimized.read_bytes()
    assert contents[:4] == b"RIFF"
    assert contents[8:12] == b"WEBP"
    assert len(contents) < 200_000


def test_optimized_brand_asset_is_served_as_webp():
    with app_module.app.test_client() as client:
        response = client.get("/static/images/popping-brand.webp")

    assert response.status_code == 200
    assert response.mimetype == "image/webp"


def test_favicon_contains_common_browser_sizes():
    contents = (IMAGE_ROOT / "favicon.ico").read_bytes()
    reserved, image_type, count = struct.unpack_from("<HHH", contents)
    assert reserved == 0
    assert image_type == 1
    assert count >= 4

    sizes = set()
    for index in range(count):
        width, height = struct.unpack_from("BB", contents, 6 + index * 16)
        sizes.add((width or 256, height or 256))
    assert {(16, 16), (32, 32), (48, 48), (256, 256)} <= sizes

    png = (IMAGE_ROOT / "favicon-32x32.png").read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack_from(">II", png, 16)
    assert (width, height) == (32, 32)
    assert png[25] == 6  # RGBA, used only for transparent rounded corners.


def test_templates_use_the_new_brand_and_tagline():
    templates = {
        name: (PROJECT_ROOT / "templates" / name).read_text(encoding="utf-8")
        for name in (
            "base.html",
            "index.html",
            "login.html",
            "instructor_login.html",
            "demo.html",
        )
    }
    tagline = (
        "Discuss, Present and Challenge - An Interactive Course Platform"
    )

    assert "images/favicon.ico" in templates["base.html"]
    assert "images/favicon-32x32.png" in templates["base.html"]
    assert templates["base.html"].count("rev='rounded'") == 2
    assert tagline in templates["base.html"]
    assert tagline in templates["index.html"]
    assert all("images/popping-brand.webp" in text for text in templates.values())
    assert all("🍿" not in text for text in templates.values())


def test_homepage_and_social_metadata_use_the_platform_title():
    base = (PROJECT_ROOT / "templates" / "base.html").read_text(
        encoding="utf-8"
    )
    index = (PROJECT_ROOT / "templates" / "index.html").read_text(
        encoding="utf-8"
    )
    title = "Popping - An Interactive Course Platform"

    assert f"{{% block title %}}{title}{{% endblock %}}" in base
    assert f"{{% block title %}}{title}{{% endblock %}}" in index
    assert f'<meta property="og:title" content="{title}">' in base
    assert f'<meta name="twitter:title" content="{title}">' in base
