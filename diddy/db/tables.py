"""Daftar tabel Diddy dan penambahan prefix tabel dari config."""
import re

from ..config import PFX

TABLES = ["users", "networks", "ranges", "zones", "records", "hosts", "discovered", "audit", "state",
          "forwarders", "deployed", "metrics"]
_TBL_RE = re.compile(r"\b(FROM|JOIN|INTO|UPDATE|EXISTS|REFERENCES|ON)\s+(" + "|".join(TABLES) + r")\b", re.I)


def T(name):
    """Nama tabel sebenarnya, termasuk prefix dari config."""
    return PFX + name
