from api import create_app, make_lifespan
from config import Config
from node import Node

cfg = Config.from_env()
node = Node(cfg)

app = create_app(node, lifespan=make_lifespan(node))