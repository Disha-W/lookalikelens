"""Resolve a stored screenshot path safely inside the evidence folder.

The app serves case screenshots straight from the evidence volume. The
stored path comes from our own database, but defence in depth is cheap:
anything that would land outside the evidence folder resolves to None.
"""
import os


def safe_screenshot_path(evidence_dir, screenshot_path):
    """Return the absolute file path, or None when unsafe or missing."""
    if not screenshot_path:
        return None
    base = os.path.realpath(evidence_dir)
    full = os.path.realpath(os.path.join(base, screenshot_path))
    if full.startswith(base + os.sep) and os.path.isfile(full):
        return full
    return None
