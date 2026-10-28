from util import legacy_slug, slugify, truncate


def page_path(title):
    return "/p/" + slugify(title)


def archive_path(title):
    return "/old/" + legacy_slug(title)


def preview(text):
    return truncate(text, 80)
