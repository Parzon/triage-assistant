"""ASGI entrypoint for servers: `gunicorn app.asgi:app`, `uvicorn app.asgi:app`.

Process-wide setup lives here, not in create_app: the factory must not
touch global state (logging.config.dictConfig replaces the root logger's
handlers - in tests that silently removes pytest's log capture).
"""

from app.config import get_settings
from app.logs import configure_logging
from app.main import create_app

settings = get_settings()
configure_logging(settings.log_level)
app = create_app(settings)
