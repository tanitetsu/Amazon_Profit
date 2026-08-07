"""Grant / revoke IAP access for provisioned users.

End users need resource-level ``roles/iap.httpsResourceAccessor`` on IAP-backed
Cloud Run services (e.g. ai-cripping-data-viewer). Project-level grants alone
are not enough for Cloud Run IAP. With IAP enabled, the IAP service agent holds
``run.invoker`` — callers only need the IAP accessor role.

Configured via env (Cloud Run Admin service):

- ``IAP_AUTO_GRANT`` — default on; set ``0``/``false`` to skip
- ``GCP_PROJECT_ID`` / ``GOOGLE_CLOUD_PROJECT`` — project id
- ``GCP_PROJECT_NUMBER`` — optional; looked up if unset
- ``IAP_REGION`` — default ``asia-northeast1``
- ``IAP_CLOUD_RUN_SERVICES`` — comma-separated service names
  (default ``ai-cripping-data-viewer``)
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from app.sheets_retry import execute_with_retry

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_IAP_ROLE = "roles/iap.httpsResourceAccessor"
_CLOUD_PLATFORM = ("https://www.googleapis.com/auth/cloud-platform",)


def iap_auto_grant_enabled() -> bool:
    raw = (os.environ.get("IAP_AUTO_GRANT") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def iap_region() -> str:
    return (os.environ.get("IAP_REGION") or "asia-northeast1").strip()


def iap_cloud_run_services() -> list[str]:
    raw = (
        os.environ.get("IAP_CLOUD_RUN_SERVICES") or "ai-cripping-data-viewer"
    ).strip()
    if not raw:
        return []
    return [s.strip() for s in raw.split(",") if s.strip()]


def gcp_project_id() -> str | None:
    for key in ("GCP_PROJECT_ID", "GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT"):
        val = (os.environ.get(key) or "").strip()
        if val:
            return val
    return None


def _member(gmail: str) -> str:
    return f"user:{gmail.strip().lower()}"


def _policy_has_member(policy: dict[str, Any], role: str, member: str) -> bool:
    for binding in policy.get("bindings") or []:
        if binding.get("role") != role:
            continue
        if member in (binding.get("members") or []):
            return True
    return False


def _add_member(policy: dict[str, Any], role: str, member: str) -> bool:
    """Mutate policy; return True if changed."""
    if _policy_has_member(policy, role, member):
        return False
    bindings = policy.setdefault("bindings", [])
    for binding in bindings:
        if binding.get("role") == role and not binding.get("condition"):
            members = binding.setdefault("members", [])
            members.append(member)
            return True
    bindings.append({"role": role, "members": [member]})
    return True


def _remove_member(policy: dict[str, Any], role: str, member: str) -> bool:
    """Mutate policy; return True if changed."""
    changed = False
    bindings = policy.get("bindings") or []
    keep: list[dict[str, Any]] = []
    for binding in bindings:
        if binding.get("role") != role:
            keep.append(binding)
            continue
        members = list(binding.get("members") or [])
        if member not in members:
            keep.append(binding)
            continue
        members = [m for m in members if m != member]
        changed = True
        if members:
            binding = {**binding, "members": members}
            keep.append(binding)
    if changed:
        policy["bindings"] = keep
    return changed


def _cloud_platform_creds():
    import google.auth

    creds, project = google.auth.default(scopes=list(_CLOUD_PLATFORM))
    return creds, project


def _resolve_project_number(creds, project_id: str) -> str:
    explicit = (os.environ.get("GCP_PROJECT_NUMBER") or "").strip()
    if explicit:
        return explicit
    from googleapiclient.discovery import build

    crm = build("cloudresourcemanager", "v1", credentials=creds, cache_discovery=False)
    meta = execute_with_retry(
        crm.projects().get(projectId=project_id),
        label="crm.projects.get",
    )
    number = str(meta.get("projectNumber") or "").strip()
    if not number:
        raise RuntimeError(f"could not resolve project number for {project_id}")
    return number


def _iap_resource_name(project_number: str, region: str, service: str) -> str:
    return (
        f"projects/{project_number}/iap_web/"
        f"cloud_run-{region}/services/{service}"
    )


def _set_iap_accessor(
    *,
    creds,
    project_number: str,
    region: str,
    service: str,
    member: str,
    grant: bool,
) -> dict[str, Any]:
    from googleapiclient.discovery import build

    # Discovery puts IAM methods under the nested ``v1`` collection
    # (root Resource has no getIamPolicy / setIamPolicy).
    iap = build("iap", "v1", credentials=creds, cache_discovery=False)
    iap_v1 = iap.v1()
    resource = _iap_resource_name(project_number, region, service)
    policy = execute_with_retry(
        iap_v1.getIamPolicy(resource=resource, body={}),
        label="iap.getIamPolicy",
    )
    changed = (
        _add_member(policy, _IAP_ROLE, member)
        if grant
        else _remove_member(policy, _IAP_ROLE, member)
    )
    if changed:
        # Drop empty optional fields that break setIamPolicy.
        body_policy = {
            "bindings": policy.get("bindings") or [],
            "etag": policy.get("etag"),
            "version": policy.get("version", 1),
        }
        execute_with_retry(
            iap_v1.setIamPolicy(resource=resource, body={"policy": body_policy}),
            label="iap.setIamPolicy",
        )
    return {
        "service": service,
        "resource": resource,
        "role": _IAP_ROLE,
        "changed": changed,
        "granted": grant,
    }


def grant_iap_access(gmail: str) -> dict[str, Any]:
    """Add the user to IAP on configured Cloud Run services."""
    return _sync_iap_access(gmail, grant=True)


def revoke_iap_access(gmail: str) -> dict[str, Any]:
    """Remove the user from IAP on configured Cloud Run services."""
    return _sync_iap_access(gmail, grant=False)


def _sync_iap_access(gmail: str, *, grant: bool) -> dict[str, Any]:
    gmail = (gmail or "").strip()
    if not _EMAIL_RE.match(gmail):
        raise ValueError(f"invalid email: {gmail}")

    if not iap_auto_grant_enabled():
        return {"skipped": True, "reason": "IAP_AUTO_GRANT disabled", "gmail": gmail}

    services = iap_cloud_run_services()
    if not services:
        return {
            "skipped": True,
            "reason": "IAP_CLOUD_RUN_SERVICES empty",
            "gmail": gmail,
        }

    project_id = gcp_project_id()
    creds, adc_project = _cloud_platform_creds()
    if not project_id:
        project_id = (adc_project or "").strip() or None
    if not project_id:
        raise RuntimeError(
            "GCP_PROJECT_ID / GOOGLE_CLOUD_PROJECT is required for IAP grants"
        )

    region = iap_region()
    member = _member(gmail)
    project_number = _resolve_project_number(creds, project_id)

    iap_results: list[dict[str, Any]] = []
    for service in services:
        iap_results.append(
            _set_iap_accessor(
                creds=creds,
                project_number=project_number,
                region=region,
                service=service,
                member=member,
                grant=grant,
            )
        )

    return {
        "skipped": False,
        "gmail": gmail,
        "member": member,
        "project_id": project_id,
        "project_number": project_number,
        "region": region,
        "grant": grant,
        "iap": iap_results,
    }
