"""Reading user uploads and app config."""

import os

BASE = "/srv/uploads"

CONFIGS = {
    "app": "/etc/app/app.conf",
    "db": "/etc/app/db.conf",
}


def resolve(name):
    path = os.path.join(BASE, name)
    if not path.startswith(BASE):
        raise ValueError(name)
    return path


def read_upload(name):
    with open(resolve(name)) as handle:
        return handle.read()


def read_config(key):
    with open(CONFIGS[key]) as handle:
        return handle.read()
