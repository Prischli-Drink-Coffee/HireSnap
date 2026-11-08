from service.configuration.env import Env
from service.configuration.logging import setup_logging

env = Env()
log = setup_logging()


def debug_ex(ex):
    if env.__getattr__("DEBUG") == "TRUE":
        log.exception(ex)


def debug_err(err):
    if env.__getattr__("DEBUG") == "TRUE":
        log.error(err)


def debug_info(info):
    if env.__getattr__("DEBUG") == "TRUE":
        log.info(info)
