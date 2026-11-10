"""Cloud Scheduler から呼ばれる定期処理。合言葉がなければ存在しないものとして扱う。"""
import secrets

from fastapi import APIRouter, Header, HTTPException, Request

from routes.api import _deps, base_url
from services.notify import run_notifications

router = APIRouter(prefix="/internal", include_in_schema=False)


@router.post("/notify")
def notify(request: Request, x_notify_secret: str = Header(default="")) -> dict:
    deps = _deps(request)
    expected = deps.settings.notify_secret
    if not expected or deps.push is None or not secrets.compare_digest(x_notify_secret, expected):
        raise HTTPException(status_code=404)
    return run_notifications(deps, base_url(request))
