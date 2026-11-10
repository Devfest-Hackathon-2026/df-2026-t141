"""アプリの入口。画面とAPIの登録だけを行う。"""
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from routes import api, internal, pages
from services import devices
from services.config import load_settings
from services.deps import Deps, build_deps
from services.missions import GameError
from services.places import PlacesError
from services.routes import RoutesError

BASE_DIR = Path(__file__).parent


def create_app(deps: Deps) -> FastAPI:
    # 本番では自動生成のAPI説明ページを公開しない
    docs_url = None if deps.settings.is_production else "/docs"
    application = FastAPI(title="Dead Hour", docs_url=docs_url, redoc_url=None, openapi_url=docs_url and "/openapi.json")
    application.state.deps = deps
    # 3D表示の three.js（約2MB）をスマホへ小さく送る
    application.add_middleware(GZipMiddleware, minimum_size=1000)

    @application.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        # 端末の鍵は、新しく作ったときだけ渡す。画面の応答にも付くよう、ここ1か所で渡す
        device_key = getattr(request.state, "device_key", None)
        if device_key:
            response.set_cookie(
                devices.COOKIE_NAME, device_key, max_age=devices.COOKIE_MAX_AGE_S, path="/",
                httponly=True, samesite="lax", secure=deps.settings.is_production,
            )
        # URLにユーザーIDが入るので、外部へはオリジンだけを送る。
        # no-referrer にすると、参照元で制限した Maps のキーが使えなくなる
        response.headers["Referrer-Policy"] = "strict-origin"
        response.headers["X-Robots-Tag"] = "noindex"
        response.headers["X-Content-Type-Options"] = "nosniff"
        # URLに版（?v=）が付いたものと three.js は中身が変わらないので、ブラウザに長く保存させる。
        # three.js は版を上げるときにフォルダを変える。それ以外は更新が古いまま使われないよう毎回確認させる
        path = request.url.path
        if path.startswith("/static/"):
            fixed = response.status_code == 200 and (path.startswith("/static/vendor/") or "v" in request.query_params)
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable" if fixed else "no-cache"
        return response

    @application.exception_handler(GameError)
    async def on_game_error(_request: Request, exc: GameError):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.http_status)

    @application.exception_handler(PlacesError)
    async def on_places_error(_request: Request, exc: PlacesError):
        return JSONResponse({"error": {"code": "places_unavailable", "message": str(exc)}}, status_code=502)

    @application.exception_handler(RoutesError)
    async def on_routes_error(_request: Request, exc: RoutesError):
        return JSONResponse({"error": {"code": "routes_unavailable", "message": str(exc)}}, status_code=502)

    application.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
    application.include_router(api.router)
    application.include_router(internal.router)
    application.include_router(pages.router)
    return application


app = create_app(build_deps(load_settings()))
