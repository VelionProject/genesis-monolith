"""Pytest-Setup für stabile Imports aus dem Repository-Root."""

import sys
from pathlib import Path

# Strukturelle Dokumentation: Tests leben in /tests und binden das Monolith-Modul explizit vom Repo-Root ein.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
