"""Put the repo root on sys.path so `import common`, `orchestrator`, `aegis.*`, `bastion.*` work."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
