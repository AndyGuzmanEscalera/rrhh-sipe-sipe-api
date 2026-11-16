import logging


def configure_logging():
    logging.basicConfig(level=logging.INFO,format='%(levelname)s %(name)s %(message)s')
    # Never log external request URLs/headers or SQL parameters at debug level.
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    logging.getLogger('sqlalchemy.engine').setLevel(logging.WARNING)
