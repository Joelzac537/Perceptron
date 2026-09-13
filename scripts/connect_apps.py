"""One-time OAuth for the four apps.

    python scripts/connect_apps.py            run the OAuth flows
    python scripts/connect_apps.py --status   just show what is connected

Safe to re-run. Connected apps are skipped, so if one tab fails just run it
again. Account IDs are written back into .env.
"""

import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.integrations.composio import (  # noqa: E402
    APPS,
    USER_ID,
    active_accounts,
    client,
    update_env,
)

TIMEOUT = 300


def find_auth_config(slug: str) -> str | None:
    """Reuse an auth config for this app if one already exists."""
    response = client.auth_configs.list()
    for item in getattr(response, "items", []) or []:
        toolkit = getattr(item, "toolkit", None)
        if str(getattr(toolkit, "slug", toolkit) or "").upper() == slug.upper():
            return item.id
    return None


def create_auth_config(slug: str, scopes: list[str]) -> str:
    created = client.auth_configs.create(
        toolkit=slug,
        options={
            "type": "use_composio_managed_auth",
            "name": f"loopgraph-{slug.lower()}",
            "credentials": {"scopes": scopes},
        },
    )
    return created.id


def connect_app(slug: str, spec: dict, already: dict) -> str | None:
    print(f"\n{spec['label']}")

    existing = already.get(slug)
    if existing and existing["status"] == "ACTIVE":
        print(f"  already connected: {existing['id']}")
        return existing["id"]

    auth_config_id = find_auth_config(slug)
    if auth_config_id:
        print(f"  reusing auth config {auth_config_id}")
    else:
        print(f"  creating auth config ({len(spec['scopes'])} scopes)")
        try:
            auth_config_id = create_auth_config(slug, spec["scopes"])
        except Exception as exc:
            print(f"  FAILED creating auth config: {exc}")
            return None

    # .link() is the current endpoint. .initiate() returns 400 for
    # Composio-managed OAuth configs, which is what these are.
    try:
        request = client.connected_accounts.link(
            user_id=USER_ID, auth_config_id=auth_config_id
        )
    except Exception as exc:
        print(f"  FAILED starting connection: {exc}")
        return None

    if request.redirect_url:
        print(f"  approve here: {request.redirect_url}")
        webbrowser.open(request.redirect_url)

    print(f"  waiting up to {TIMEOUT}s for approval...")
    try:
        account = request.wait_for_connection(timeout=TIMEOUT)
    except Exception as exc:
        print(f"  FAILED: {exc}")
        return None

    print(f"  connected: {account.id}")
    return account.id


def show_status() -> None:
    found = active_accounts()
    print(f"user_id = {USER_ID}\n")
    for slug, spec in APPS.items():
        info = found.get(slug)
        if not info:
            print(f"  {spec['label']:<10} not connected")
        else:
            warn = "" if info["status"] == "ACTIVE" else "  <-- reconnect"
            print(f"  {spec['label']:<10} {info['id']:<26} {info['status']}{warn}")
    active = sum(1 for s in APPS if found.get(s, {}).get("status") == "ACTIVE")
    print(f"\n{active}/{len(APPS)} active")


def main() -> None:
    if "--status" in sys.argv:
        show_status()
        return

    print(f"user_id = {USER_ID}")
    already = active_accounts()

    results = {slug: connect_app(slug, spec, already) for slug, spec in APPS.items()}

    to_save = {
        APPS[slug]["env_key"]: account_id
        for slug, account_id in results.items()
        if account_id
    }
    if to_save:
        update_env(to_save)
        print(f"\nsaved {len(to_save)} id(s) to .env")

    print("\n" + "-" * 44)
    for slug, account_id in results.items():
        print(f"  {APPS[slug]['label']:<10} {account_id or 'FAILED'}")

    done = sum(1 for v in results.values() if v)
    print(f"\n{done}/{len(APPS)} connected")
    if done < len(APPS):
        sys.exit(1)


if __name__ == "__main__":
    main()
