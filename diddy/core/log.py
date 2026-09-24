"""Logger aplikasi. Dipakai juga dari thread latar, jadi tidak bergantung pada Flask app context."""
import logging

log = logging.getLogger("diddy")
