from store import load, save


def put(path, payload):
    save(path, payload)
    return {"ok": True}


def get(path):
    return load(path)
