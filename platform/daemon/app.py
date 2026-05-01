from shared.config import get_settings

from daemon.service import create_daemon_app


app = create_daemon_app(get_settings())
