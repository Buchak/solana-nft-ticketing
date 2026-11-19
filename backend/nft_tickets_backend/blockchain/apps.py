from django.apps import AppConfig
import sys


class BlockchainConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'blockchain'

    def ready(self):
        if "test" in sys.argv:
            return
        if sys.argv[0].endswith("manage.py") and len(sys.argv) > 1:
            if sys.argv[1] != "runserver":
                return

        from .scheduler import start_event_scheduler

        start_event_scheduler()
