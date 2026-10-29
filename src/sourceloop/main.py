"""uvicorn entrypoint: `uvicorn sourceloop.main:app`."""
from sourceloop.api.app import create_app

app = create_app()
