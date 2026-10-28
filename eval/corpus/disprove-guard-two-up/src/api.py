"""HTTP routes. Every route here is registered through the admin router."""

import service


def require_admin(handler):
    def wrapped(request, *args):
        if not request.user.is_admin:
            raise PermissionError("admin only")
        return handler(request, *args)

    return wrapped


@require_admin
def delete_route(request, conn, account_id):
    service.delete_account(conn, account_id)
    return {"deleted": account_id}
