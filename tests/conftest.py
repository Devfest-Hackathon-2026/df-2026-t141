from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import create_app
from services.config import Settings
from services.deps import Deps
from services.firestore import MemoryRepository
from services.game import JST
from services.places import FakePlacesBackend, ShelterFinder

HOME = (35.681, 139.767)


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 6, 12, 0, tzinfo=JST)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def deps(clock) -> Deps:
    return Deps(
        settings=Settings(),
        repo=MemoryRepository(),
        shelters=ShelterFinder(FakePlacesBackend()),
        clock=clock,
    )


@pytest.fixture
def client(deps) -> TestClient:
    return TestClient(create_app(deps))


@pytest.fixture
def user_id(client) -> str:
    response = client.post(
        "/api/users",
        json={"nickname": "隊員", "lat": 35.68123, "lng": 139.76712, "daily_notify_time": "18:00"},
    )
    assert response.status_code == 201
    return response.json()["user_id"]


@pytest.fixture
def mission(client, user_id) -> dict:
    # 時刻は選べない。次に来る毎日の時刻（今日の18時）に向けた予約になる
    shelter = client.get(f"/api/users/{user_id}/shelters").json()[0]
    response = client.post(f"/api/users/{user_id}/missions", json={"place_id": shelter["place_id"]})
    assert response.status_code == 201
    return response.json()
