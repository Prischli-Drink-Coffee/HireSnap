import os

from fastapi.responses import FileResponse, JSONResponse
from service.configuration.env import Env
env = Env()


def return_url_object(url: str):
    url = url[2:]
    # Behind a reverse proxy HOST:SERVER_PORT is an internal address that is not reachable
    # from outside, so a non-empty PUBLIC_BASE_URL wins; its trailing slashes are stripped
    # so exactly one slash joins it to the path. When PUBLIC_BASE_URL is unset or empty the
    # original HOST:SERVER_PORT expression is used unchanged. Read through Env (which loaded
    # backend/.env in __init__ above) and only when present, because __getattr__ logs and
    # raises AttributeError for missing variables.
    public_base_url = ''
    if 'PUBLIC_BASE_URL' in os.environ:
        public_base_url = env.__getattr__('PUBLIC_BASE_URL').strip().rstrip('/')
    if not public_base_url:
        public_base_url = f"http://{env.__getattr__('HOST')}:{env.__getattr__('SERVER_PORT')}"
    return (f"{public_base_url}/"
            f"{url}")
