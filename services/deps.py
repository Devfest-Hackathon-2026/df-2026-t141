"""使うサービスの組み立て。設定に応じて本物と開発用を切り替える。"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from .config import Settings, validate_for_production
from .firestore import FirestoreRepository, MemoryRepository, Repository
from .limits import RateLimiter
from .notify import FcmSender, PushSender
from .places import FakePlacesBackend, GooglePlacesBackend, ShelterFinder
from .routes import FakeRoutesBackend, GoogleRoutesBackend, RouteFinder


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Deps:
    settings: Settings
    repo: Repository
    shelters: ShelterFinder
    clock: Callable[[], datetime] = utc_now
    fakes: list[str] = field(default_factory=list)
    push: PushSender | None = None
    routes: RouteFinder = field(default_factory=lambda: RouteFinder(FakeRoutesBackend()))
    limiter: RateLimiter = field(default_factory=RateLimiter)


def build_deps(settings: Settings) -> Deps:
    if settings.is_production:
        validate_for_production(settings)
    fakes: list[str] = []

    if settings.use_firestore:
        from google.cloud import firestore

        repo: Repository = FirestoreRepository(firestore.Client(project=settings.project))
    else:
        repo = MemoryRepository()
        fakes.append("保存（再起動で消えます）")

    if settings.places_api_key:
        backend = GooglePlacesBackend(settings.places_api_key)
    else:
        backend = FakePlacesBackend()
        fakes.append("公園検索")

    if settings.routes_api_key:
        routes = RouteFinder(GoogleRoutesBackend(settings.routes_api_key))
    else:
        routes = RouteFinder(FakeRoutesBackend())
        fakes.append("徒歩ルート")

    if not settings.maps_browser_key:
        fakes.append("地図（簡易表示）")

    push = FcmSender(settings.project) if settings.has_push else None
    return Deps(settings, repo, ShelterFinder(backend), fakes=fakes, push=push, routes=routes)
