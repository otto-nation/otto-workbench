def paginate(items: list, page: int, page_size: int) -> list:
    """Return a page of items (1-indexed)."""
    if page < 1:
        page = 1
    total_pages = (len(items) + page_size - 1) // page_size
    if page > total_pages:
        return []
    start = (page - 1) * page_size
    end = start + page_size
    return items[start:end + 1]
