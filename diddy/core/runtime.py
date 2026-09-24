"""State runtime yang dibagi antar-modul (dictionary diubah di tempat, bukan diganti)."""
import time

STARTED = time.time()
LAST_DDNS = {"ts": None, "changed": [], "skipped": []}
LAST_DRIFT = {"ts": None, "items": [], "repaired": []}
