"""Regression coverage for resetting pre-versioning private demos."""

from pathlib import Path
import sqlite3

import demo_instance


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_private_demo_reset_adopts_missing_schema_ledger(tmp_path):
    data_dir = tmp_path / "data"
    slug = "demo_" + "b" * 32
    demo_instance.create_demo_instance(
        str(data_dir),
        str(PROJECT_ROOT / "classes"),
        str(PROJECT_ROOT / "popping.sql"),
        slug=slug,
    )
    path = Path(demo_instance.demo_database_path(str(data_dir), slug))
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE schema_migrations")

    demo_instance.reset_demo_instance(
        str(data_dir),
        str(PROJECT_ROOT / "classes"),
        slug,
        cooldown_seconds=0,
    )

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT schema_version FROM schema_migrations"
        ).fetchall() == [("1.0.0",)]
        assert connection.execute(
            "SELECT COUNT(*) FROM students"
        ).fetchone()[0] == 2
        for table in (
            "teammate_thumbs",
            "presentation_ratings",
            "challenge_rounds",
            "challenge_ratings",
        ):
            columns = {
                row[1] for row in connection.execute(
                    f"PRAGMA table_info({table})"
                )
            }
            assert "data_version" in columns
