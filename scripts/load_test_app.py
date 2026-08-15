"""WSGI entry point for isolated local load tests.

This module refuses to start unless both data and class directories are
explicitly supplied. It must never point at the repository's real directories.
"""

import os
from pathlib import Path


if os.environ.get("POPPING_LOAD_TEST") != "1":
    raise RuntimeError("POPPING_LOAD_TEST=1 is required")

data_dir = os.environ.get("DATA_DIR")
classes_dir = os.environ.get("LOAD_TEST_CLASSES_DIR")
if not data_dir or not classes_dir:
    raise RuntimeError("DATA_DIR and LOAD_TEST_CLASSES_DIR are required")

project_root = Path(__file__).resolve().parents[1]
resolved_data = Path(data_dir).resolve()
resolved_classes = Path(classes_dir).resolve()
for supplied, forbidden, label in (
    (resolved_data, (project_root / "data").resolve(), "DATA_DIR"),
    (resolved_classes, (project_root / "classes").resolve(), "LOAD_TEST_CLASSES_DIR"),
):
    if supplied == forbidden:
        raise RuntimeError(f"{label} must not use the repository's live path")

import config

config.DATA_DIR = str(resolved_data)
config.CLASSES_DIR = str(resolved_classes)
config.CONFIG_DIR = str(resolved_classes)

from app import app
