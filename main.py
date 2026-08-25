import os
import sys

# Ensure the project directory is importable regardless of how the app is
# launched (uvicorn CLI, `python -m uvicorn`, gunicorn, Railway/nixpacks,
# mise shims...). Fixes: ModuleNotFoundError: No module named 'tx'
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from api import create_app, make_lifespan
from config import Config
from node import Node

cfg = Config.from_env()
node = Node(cfg)

app = create_app(node, lifespan=make_lifespan(node))
