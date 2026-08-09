#!/usr/bin/env python3
"""
Build a RAG index for a public knowledge-base theme pack.

Usage:
    python scripts/build_public_kb.py --theme dino-world
    python scripts/build_public_kb.py --theme all

This script scans ``data/public_kb/<theme_id>/`` for the manifest and source
Markdown files, then runs them through the project's RAG pipeline to produce
an embeddable index. The index is stored alongside the theme data so the
gamification retrieval layer (T03) can query it at runtime.

If the RAG pipeline cannot be imported (e.g. running in a minimal test
environment without embedding model dependencies), the script prints a clear
message and exits gracefully.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

# Resolve project root relative to this script (scripts/build_public_kb.py →
# project root is two levels up).
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_PUBLIC_KB_ROOT = _PROJECT_ROOT / "data" / "public_kb"


def _list_themes() -> list[str]:
    """Return sorted theme IDs that have a valid manifest.yaml."""
    if not _PUBLIC_KB_ROOT.is_dir():
        return []
    themes: list[str] = []
    for entry in sorted(_PUBLIC_KB_ROOT.iterdir()):
        if entry.is_dir() and (entry / "manifest.yaml").is_file():
            themes.append(entry.name)
    return themes


def _collect_source_files(theme_id: str) -> list[Path]:
    """Collect all ``.md`` source files for a theme."""
    theme_dir = _PUBLIC_KB_ROOT / theme_id
    if not theme_dir.is_dir():
        return []
    return sorted(theme_dir.glob("*.md"))


def build_theme_index(theme_id: str) -> bool:
    """Build the RAG index for a single public theme.

    Returns ``True`` on success, ``False`` if the pipeline is unavailable.
    """
    theme_dir = _PUBLIC_KB_ROOT / theme_id
    if not theme_dir.is_dir():
        print(f"  [ERROR] Theme directory not found: {theme_dir}")
        return False

    source_files = _collect_source_files(theme_id)
    if not source_files:
        print(f"  [WARN] No source Markdown files found for theme '{theme_id}'")
        return False

    print(f"  Found {len(source_files)} source file(s):")
    for sf in source_files:
        print(f"    - {sf.name}")

    # Attempt to import the RAG pipeline. If the project's embedding / indexing
    # dependencies are not installed, we report and exit gracefully.
    try:
        from deeptutor.services.rag.factory import DEFAULT_PROVIDER
        from deeptutor.services.rag.pipeline import build_index_for_documents
    except ImportError:
        print(
            "  [INFO] RAG pipeline not available in this environment.\n"
            "         Install the project's RAG dependencies to build indexes."
        )
        return False

    # Build the index using the default provider.
    try:
        doc_paths = [str(sf) for sf in source_files]
        print(f"  Building index with provider '{DEFAULT_PROVIDER}'...")
        build_index_for_documents(
            documents=doc_paths,
            output_dir=str(theme_dir),
            provider=DEFAULT_PROVIDER,
        )
        print(f"  [OK] Index built successfully for '{theme_id}'.")
        return True
    except Exception as exc:
        print(f"  [ERROR] Index build failed for '{theme_id}': {exc}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build RAG indexes for public knowledge-base theme packs.",
    )
    parser.add_argument(
        "--theme",
        default="all",
        help="Theme ID to build (e.g. 'dino-world'), or 'all' for every theme. "
        "Default: all",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all available public themes and exit.",
    )
    args = parser.parse_args()

    if args.list:
        themes = _list_themes()
        if not themes:
            print("No public themes found.")
            return 0
        print("Available public themes:")
        for tid in themes:
            print(f"  - {tid}")
        return 0

    if args.theme == "all":
        themes = _list_themes()
        if not themes:
            print("No public themes found.")
            return 1
    else:
        themes = [args.theme]

    print(f"Building indexes for: {', '.join(themes)}")
    print(f"  Public KB root: {_PUBLIC_KB_ROOT}")
    print()

    all_ok = True
    for theme_id in themes:
        print(f"--- Theme: {theme_id} ---")
        ok = build_theme_index(theme_id)
        if not ok:
            all_ok = False
        print()

    if all_ok:
        print("[DONE] All theme indexes built successfully.")
        return 0
    else:
        print("[DONE] Some indexes could not be built (see messages above).")
        return 1


if __name__ == "__main__":
    sys.exit(main())
