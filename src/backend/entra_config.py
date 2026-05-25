def issuer_candidates(tenant_id: str) -> tuple[str, ...]:
    return (
        f"https://login.microsoftonline.com/{tenant_id}/v2.0",
        f"https://sts.windows.net/{tenant_id}/",
    )


def allowed_audiences(client_id: str, api_scope: str) -> tuple[str, ...]:
    audiences: list[str] = []
    if client_id:
        audiences.append(client_id)

    raw_scope = (api_scope or "").strip()
    if raw_scope:
        scope_prefix = raw_scope.rsplit("/", 1)[0] if "/" in raw_scope else raw_scope
        if scope_prefix and scope_prefix not in audiences:
            audiences.append(scope_prefix)
        if scope_prefix.startswith("api://"):
            scope_client_id = scope_prefix.removeprefix("api://")
            if scope_client_id and scope_client_id not in audiences:
                audiences.append(scope_client_id)

    return tuple(audiences)
