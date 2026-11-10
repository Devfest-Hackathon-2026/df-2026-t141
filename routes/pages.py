"""B担当の画面（拠点・予約・成果）と、A担当の画面ができるまでの仮ページ。"""
import hashlib
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from routes.api import IdPath, _deps, authorize, mission_public, user_public
from services import game, missions
from services.missions import GameError

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


STATIC_DIR = Path(__file__).parent.parent / "static"


def _static_files() -> list[Path]:
    return [path for path in sorted(STATIC_DIR.rglob("*")) if path.is_file() and "vendor" not in path.parts]


def _asset_version() -> str:
    """CSS と JavaScript の中身から作る版。中身が変わればURLが変わるので、ブラウザに長く保存させてよい。
    起動時刻にすると、Cloud Run のインスタンスが入れ替わるたびに保存が無駄になる。"""
    digest = hashlib.sha256()
    for path in _static_files():
        digest.update(path.relative_to(STATIC_DIR).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:10]


class _AssetVersion:
    """テンプレートの {{ asset_version }}。開発中に JS や CSS だけを書き換えると、サーバーは再起動しないので
    起動時の版のままになり、ブラウザが古いファイルを使い続けていた。ファイルの更新時刻が変わったら計算し直す。"""

    def __init__(self) -> None:
        self._signature: tuple | None = None
        self._value = ""

    def __str__(self) -> str:
        signature = tuple((path.as_posix(), path.stat().st_mtime_ns, path.stat().st_size) for path in _static_files())
        if signature != self._signature:
            self._signature = signature
            self._value = _asset_version()
        return self._value


templates.env.globals["asset_version"] = _AssetVersion()
MISSION_ID = re.compile(r"^[A-Za-z0-9_-]{20,64}$")

DEV_HOME = (35.681, 139.767)


def _firebase_config(settings) -> dict:
    """Firebase の Web 設定。ブラウザへ渡す前提の値で、秘密情報ではない。"""
    return {
        "apiKey": settings.firebase_api_key,
        "projectId": settings.project,
        "appId": settings.firebase_app_id,
        "messagingSenderId": settings.firebase_sender_id,
    }


def _locked_page(request: Request, user_id: str):
    """他の端末で開いたら、トップページへ移す。持ち主の端末なら None。"""
    deps = _deps(request)
    user = deps.repo.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404)
    try:
        authorize(request, user)
    except GameError:
        return RedirectResponse("/?locked=1", status_code=303)
    return None


@router.get("/firebase-messaging-sw.js")
def service_worker(request: Request):
    deps = _deps(request)
    if deps.push is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "firebase-messaging-sw.js",
        {"firebase": _firebase_config(deps.settings)},
        media_type="text/javascript",
    )


@router.get("/manifest/{user_id}.webmanifest")
def manifest(request: Request, user_id: IdPath):
    """ホーム画面に追加したときに、自分のホームが開くようにする。"""
    if _deps(request).repo.get_user(user_id) is None:
        raise HTTPException(status_code=404)
    return JSONResponse(
        {
            "name": "デッドアワー",
            "short_name": "デッドアワー",
            "start_url": f"/home/{user_id}",
            "scope": "/",
            "display": "standalone",
            "background_color": "#0f1412",
            "theme_color": "#0f1412",
            "icons": [{"src": "/static/icon.svg", "sizes": "any", "type": "image/svg+xml"}],
        },
        media_type="application/manifest+json",
    )


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/", response_class=HTMLResponse)
def welcome(request: Request):
    # ①ようこそ・②初期設定はA担当。できたらここを差し替える
    deps = _deps(request)
    return templates.TemplateResponse(
        request,
        "welcome.html",
        {
            "title": "ようこそ", "owner": "①②", "show_dev_link": not deps.settings.is_production, "fakes": deps.fakes,
            "locked": "locked" in request.query_params,  # 他の端末で拠点を開いて、ここへ来た
        },
    )


@router.get("/setup", response_class=HTMLResponse)
def setup(request: Request):
    return templates.TemplateResponse(request, "setup.html", {"fakes": _deps(request).fakes})


@router.get("/link", response_class=HTMLResponse)
def link(request: Request):
    """別の端末から、引き継ぎコードで続ける。"""
    return templates.TemplateResponse(request, "link.html", {"fakes": _deps(request).fakes})


@router.get("/dev/new-user")
def dev_new_user(request: Request):
    """ローカル確認用。A担当の初期設定がなくてもホームを開けるようにする。"""
    deps = _deps(request)
    if deps.settings.is_production:
        raise HTTPException(status_code=404)
    user = missions.create_user(deps, "テスト隊員", *DEV_HOME, "18:00")
    return RedirectResponse(f"/home/{user.id}", status_code=303)


@router.get("/dev/base/{user_id}")
def dev_set_base(request: Request, user_id: IdPath, m: int = Query(ge=0, le=1_000_000)):
    """ローカル確認用。拠点の見た目を段階ごとに確かめる。本番では使えない（デモの偽装になるため）。"""
    deps = _deps(request)
    if deps.settings.is_production or deps.repo.get_user(user_id) is None:
        raise HTTPException(status_code=404)

    def apply(user, _mission):
        user.base_escaped_m = m
        user.best_base_rank = max(user.best_base_rank, game.base_rank(m))
        return user, None

    deps.repo.transact(user_id, None, apply)
    return RedirectResponse(f"/home/{user_id}", status_code=303)


@router.get("/home/{user_id}", response_class=HTMLResponse)
def home(request: Request, user_id: IdPath):
    deps = _deps(request)
    if (locked := _locked_page(request, user_id)) is not None:
        return locked
    try:
        user = missions.settle_user(deps, user_id)
    except missions.GameError:
        raise HTTPException(status_code=404) from None
    all_missions = deps.repo.list_missions(user_id)
    # 毎日のミッション（予約中か逃走中）と、いま逃走中のミッション（毎日かフリーラン）
    daily = next((m for m in reversed(all_missions) if m.kind == "daily" and m.status in ("reserved", "escaped")), None)
    running = next((m for m in reversed(all_missions) if m.status == "escaped"), None)
    next_mission = mission_public(request, daily) if daily else None
    running_mission = mission_public(request, running) if running else None
    public = user_public(request, user)
    settings = deps.settings
    waiting = next_mission if next_mission and next_mission.status == "reserved" else None
    base_data = {
        "user_id": user_id,
        "km": public.zombie_km,
        "max_km": game.ZOMBIE_START_KM,
        "speed": public.zombie_speed_km_per_hour,
        "capture_count": public.capture_count,
        "arrival_at": public.arrival_at.isoformat(),
        "chasing": public.chasing,
        # フリーラン中は脱出できないので、脱出の画面へ自動で移らない
        "depart_at": waiting.depart_at.isoformat() if waiting and not running_mission else None,
        "escape_url": next_mission.escape_url if next_mission else None,
        "rank": public.base_rank,
        "max_rank": len(game.BASE_RANKS),
        "lost_rank": public.lost_base_rank,
        "avatar": {"outfit": game.AVATAR_OUTFITS[user.avatar_outfit], "skin": game.AVATAR_SKINS[user.avatar_skin]},
    }
    push_config = (
        {"user_id": user_id, "vapid_key": settings.firebase_vapid_key, "firebase": _firebase_config(settings)}
        if deps.push
        else None
    )
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "user": public,
            "user_id": user_id,
            "mission": next_mission,
            "running": running_mission,
            "outfits": {name: f"#{color:06x}" for name, color in game.AVATAR_OUTFITS.items()},
            "skins": {name: f"#{color:06x}" for name, color in game.AVATAR_SKINS.items()},
            "zombie_max_km": game.ZOMBIE_START_KM,
            "freeze_max": game.FREEZE_MAX,
            "rank_start_m": game.BASE_RANKS[public.base_rank - 1][0],
            "lost_rank_name": game.base_rank_name(public.lost_base_rank) if public.lost_base_rank else None,
            "base_data": base_data,
            "push_config": push_config,
            "fakes": deps.fakes,
        },
    )


@router.get("/run/{user_id}", response_class=HTMLResponse)
def run(request: Request, user_id: IdPath):
    """フリーラン。今いる場所の近くの公園とモードを選ぶ。"""
    deps = _deps(request)
    if (locked := _locked_page(request, user_id)) is not None:
        return locked
    try:
        user = missions.settle_user(deps, user_id)
    except missions.GameError:
        raise HTTPException(status_code=404) from None
    running = next((m for m in reversed(deps.repo.list_missions(user_id)) if m.status == "escaped"), None)
    return templates.TemplateResponse(
        request,
        "run.html",
        {
            "user_id": user_id,
            "chasing": user.chasing,
            "running_url": mission_public(request, running).escape_url if running else None,
            "modes": game.MODES,
            "free_run_m": game.FREE_RUN_ZOMBIE_KM * 1000,
            "maps_key": deps.settings.maps_browser_key,
            "map_id": deps.settings.maps_map_id,
            "fakes": deps.fakes,
        },
    )


@router.get("/results/{user_id}", response_class=HTMLResponse)
def results(request: Request, user_id: IdPath):
    deps = _deps(request)
    if (locked := _locked_page(request, user_id)) is not None:
        return locked
    try:
        stats = missions.user_stats(deps, user_id)
    except missions.GameError:
        raise HTTPException(status_code=404) from None
    return templates.TemplateResponse(
        request,
        "results.html",
        {
            "user_id": user_id,
            "stats": stats,
            "best_rank_name": game.base_rank_name(stats.best_base_rank),
            "rank_name": game.base_rank_name(stats.base_rank),
            "fakes": deps.fakes,
        },
    )


@router.get("/reserve/{user_id}", response_class=HTMLResponse)
def reserve(request: Request, user_id: IdPath):
    deps = _deps(request)
    if (locked := _locked_page(request, user_id)) is not None:
        return locked
    try:
        user = missions.settle_user(deps, user_id)
    except missions.GameError:
        raise HTTPException(status_code=404) from None
    return templates.TemplateResponse(
        request,
        "reserve.html",
        {
            "user_id": user_id,
            "arrival_at": user.arrival_at,
            "is_today": game.jst_date(user.arrival_at) == game.jst_date(deps.clock()),
            "night_warning": game.is_dark(user.arrival_at),
            "chasing": user.chasing,
            "modes": game.MODES,
            "maps_key": deps.settings.maps_browser_key,
            "map_id": deps.settings.maps_map_id,
            "fakes": deps.fakes,
        },
    )


@router.get("/escape", response_class=HTMLResponse)
def escape(request: Request, mission: str = ""):
    # ⑤⑥⑦はこの画面の中でミッションの状態に応じて切り替える
    deps = _deps(request)
    # 最初の表示に要るミッションとユーザーをページに入れておき、画面が API を2回待たずに描けるようにする
    initial = None
    if MISSION_ID.match(mission):
        found = deps.repo.get_mission(mission)
        if found is not None and (locked := _locked_page(request, found.user_id)) is not None:
            return locked
        try:
            current = missions.get_mission(deps, mission)
            user = deps.repo.get_user(current.user_id)  # get_mission の中で反映済み
            initial = {
                "mission": mission_public(request, current).model_dump(mode="json"),
                "user": user_public(request, user).model_dump(mode="json"),
            }
        except GameError:
            initial = None  # 画面が API を呼んで、見つからない旨を出す
    return templates.TemplateResponse(
        request,
        "escape.html",
        {
            "mission_id": mission,
            "initial": initial,
            "avatar_palette": {"outfits": game.AVATAR_OUTFITS, "skins": game.AVATAR_SKINS},
            "maps_key": deps.settings.maps_browser_key,
            "map_id": deps.settings.maps_map_id,
            "fakes": deps.fakes,
        },
    )
