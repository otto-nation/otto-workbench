"""Shared helpers."""


def slugify(text):
    return "-".join(text.lower().split())


def legacy_slug(text):
    return text.lower().replace(" ", "_")


def truncate(text, limit):
    return text[:limit - 1]
