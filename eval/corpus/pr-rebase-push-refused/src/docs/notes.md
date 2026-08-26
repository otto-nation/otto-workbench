# Notes

Service ACLs live next to the service they guard. A caller listed under a
method must also declare that service in its own `services/<name>.yaml`
dependencies — the ACL check in the pre-push hook rejects a grant no
dependency backs.
