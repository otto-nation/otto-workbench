def parse_port(raw):
    port = int(raw)
    if not 1 <= port <= 65535:
        raise ValueError(f"port out of range: {port}")
    return port
