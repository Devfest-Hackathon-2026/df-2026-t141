from datetime import datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from app import create_app
from services.config import Settings, validate_for_production
from services.deps import build_deps
from services import game
from services.firestore import FirestoreRepository
from services.game import JST
from services.models import Shelter, User
from services.places import FIELD_MASK, NEARBY_URL, FakePlacesBackend, GooglePlacesBackend, PlacesError, ShelterFinder, _spread
from services.routes import FIELD_MASK as ROUTES_FIELD_MASK, FakeRoutesBackend, GoogleRoutesBackend, RouteFinder, RoutesError, decode_polyline

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=JST)


def _user(**kwargs) -> User:
    base = dict(
        id="u" * 32, nickname="隊員", home_lat=35.681, home_lng=139.767, daily_notify_time="18:00",
        zombie_lat=35.7, zombie_lng=139.8, zombie_anchor_at=NOW, target_lat=35.681, target_lng=139.767, arrival_at=NOW, created_at=NOW,
    )
    return User(**(base | kwargs))


# --- Places ---

def _park(place_id="p1", dlat=0.005, half=0.001):
    lat, lng = 35.681 + dlat, 139.767
    return {
        "id": place_id,
        "displayName": {"text": "日比谷公園", "languageCode": "ja"},
        "location": {"latitude": lat, "longitude": lng},
        "viewport": {"low": {"latitude": lat - half, "longitude": lng - half}, "high": {"latitude": lat + half, "longitude": lng + half}},
    }


def test_places_request_shape():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"places": [_park()]})

    backend = GooglePlacesBackend("secret-key", httpx.Client(transport=httpx.MockTransport(handler)))
    assert backend.search_parks(35.681, 139.767, 500)[0]["id"] == "p1"
    assert seen["url"] == NEARBY_URL
    assert seen["headers"]["x-goog-api-key"] == "secret-key"
    assert seen["headers"]["x-goog-fieldmask"] == FIELD_MASK
    assert '"includedTypes":["park"]' in seen["body"].replace(" ", "")
    assert '"radius":500.0' in seen["body"].replace(" ", "")


def test_places_empty_response():
    backend = GooglePlacesBackend("k", httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))))
    assert backend.search_parks(35.0, 139.0, 500) == []


def test_places_error_does_not_leak_key(caplog):
    backend = GooglePlacesBackend("secret-key", httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403, text="denied"))))
    with pytest.raises(PlacesError) as info:
        backend.search_parks(35.0, 139.0, 500)
    assert "secret-key" not in str(info.value) and "secret-key" not in caplog.text


def test_places_failure_returns_502(client, deps, user_id):
    def boom(*_args):
        raise PlacesError("公園の検索に失敗しました")

    deps.shelters._backend.search_parks = boom
    response = client.get(f"/api/users/{user_id}/shelters")
    assert response.status_code == 502 and response.json()["error"]["code"] == "places_unavailable"


class _CountingBackend:
    def __init__(self, places):
        self.places, self.calls = places, 0

    def search_parks(self, lat, lng, radius):
        self.calls += 1
        return self.places


def test_shelter_finder_uses_viewport_for_radius_and_caches():
    backend = _CountingBackend([_park(dlat=0.004, half=0.0005), _park("big", dlat=-0.004, half=0.01), _park("near", dlat=0.0005), {"id": "broken"}])
    ticks = [0.0]
    finder = ShelterFinder(backend, clock=lambda: ticks[0])
    found = {s.place_id: s for s in finder.candidates(_user())}
    assert set(found) == {"p1", "big"}  # 近すぎる公園と壊れたデータは除外
    assert found["p1"].checkin_radius_m == 56
    assert found["big"].checkin_radius_m == 100  # 大きい公園でも中心から100mまで
    assert found["p1"].direction == "北" and found["big"].direction == "南"
    finder.candidates(_user())
    assert backend.calls == 1
    ticks[0] = 601.0
    finder.candidates(_user())
    assert backend.calls == 2


def test_shelter_finder_skips_parks_whose_checkin_area_is_too_close():
    # どちらも家から約333m。大きい公園は判定の範囲（100m）の端まで約233mしかなく、少し歩くだけで着いてしまう
    backend = _CountingBackend([_park("small", dlat=0.003, half=0.0002), _park("big", dlat=-0.003, half=0.01)])
    found = {s.place_id: s for s in ShelterFinder(backend, clock=lambda: 0.0).candidates(_user())}
    assert set(found) == {"small"}
    assert found["small"].distance_m - found["small"].checkin_radius_m >= game.MIN_SHELTER_DISTANCE_M


def test_saved_shelter_radius_is_capped():
    # 上限が200mだったころに予約したシェルターも、今の上限で判定する
    saved = {"place_id": "p", "name": "公園", "lat": 35.0, "lng": 139.0, "distance_m": 400, "checkin_radius_m": 200, "direction": "北"}
    assert Shelter.model_validate(saved).checkin_radius_m == game.CHECKIN_MAX_M
    assert Shelter.model_validate(saved | {"checkin_radius_m": 60}).checkin_radius_m == 60


class _DenseBackend:
    """公園の多い地域。近い順では、近所の公園だけで検索の上限に届く。"""

    def __init__(self, nearby=20):
        # 小さい公園（判定の半径50m）。約333mからなので、判定の範囲の端まで250m以上ある
        self.near = [_park(f"n{i}", dlat=0.003 + i * 0.0001, half=0.0003) for i in range(nearby)]
        self.far = [_park(f"f{i}", dlat=0.008 + i * 0.005) for i in range(4)]  # 889m, 1445m, 2001m, 2557m（最後は範囲外）
        self.popular_calls = 0

    def search_parks(self, lat, lng, radius):
        return self.near

    def search_popular_parks(self, lat, lng, radius):
        self.popular_calls += 1
        return self.far + [self.near[0]]  # 人気順にも近所の公園が混ざる


RANK_9_RADIUS_M = 2500


def test_dense_area_shows_far_parks_and_merges_close_ones():
    backend = _DenseBackend()
    found = ShelterFinder(backend).candidates_near(35.681, 139.767, 9)
    # 近所の20件は1つにまとまり、遠くの公園が出る。範囲外と重複は出ない
    assert [s.place_id for s in found] == ["n0", "f0", "f1", "f2"]
    assert all(s.distance_m <= RANK_9_RADIUS_M for s in found)
    assert backend.popular_calls == 1


def test_popular_search_is_cached():
    backend = _DenseBackend()
    finder = ShelterFinder(backend)
    finder.candidates_near(35.681, 139.767, 9)
    finder.candidates_near(35.681, 139.767, 9)
    assert backend.popular_calls == 1


def test_popular_search_is_skipped_when_all_parks_fit():
    backend = _DenseBackend(nearby=19)
    ShelterFinder(backend).candidates_near(35.681, 139.767, 9)
    assert backend.popular_calls == 0


def test_popular_search_failure_keeps_nearby_parks():
    class Broken(_DenseBackend):
        def search_popular_parks(self, lat, lng, radius):
            raise PlacesError("公園の検索に失敗しました")

    assert [s.place_id for s in ShelterFinder(Broken()).candidates_near(35.681, 139.767, 9)] == ["n0"]


def test_popular_request_ranks_by_popularity():
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.read().decode().replace(" ", ""))
        return httpx.Response(200, json={"places": []})

    backend = GooglePlacesBackend("k", httpx.Client(transport=httpx.MockTransport(handler)))
    backend.search_parks(35.0, 139.0, 500)
    backend.search_popular_parks(35.0, 139.0, 500)
    assert '"rankPreference":"DISTANCE"' in bodies[0] and '"rankPreference":"POPULARITY"' in bodies[1]


def test_spread_limits_candidates_evenly_from_near_to_far():
    shelters = [
        Shelter(place_id=f"p{i}", name="公園", lat=35.0 + i * 0.001, lng=139.0, distance_m=300 + i * 100, checkin_radius_m=50, direction="北")
        for i in range(30)
    ]
    picked = _spread(shelters, 100)
    assert len(picked) == game.MAX_SHELTER_CANDIDATES
    assert picked[0].place_id == "p0" and picked[-1].place_id == "p29"
    gaps = [b.distance_m - a.distance_m for a, b in zip(picked, picked[1:])]
    assert max(gaps) - min(gaps) <= 100  # 1か所に偏らない


# --- 回数の制限 ---

def test_rate_limiter_counts_per_key_and_resets_after_window():
    from services.limits import RateLimiter

    ticks = [0.0]
    limiter = RateLimiter(clock=lambda: ticks[0])
    assert [limiter.allow("a", 2, 60) for _ in range(3)] == [True, True, False]
    assert limiter.allow("b", 2, 60) is True
    ticks[0] = 61.0
    assert limiter.allow("a", 2, 60) is True


# --- 本番設定 ---

def test_production_refuses_to_start_with_fakes():
    with pytest.raises(RuntimeError) as info:
        build_deps(Settings(app_env="production"))
    for name in ("GOOGLE_CLOUD_PROJECT", "USE_FIRESTORE", "PLACES_API_KEY", "MAPS_BROWSER_KEY"):
        assert name in str(info.value)


def test_complete_production_settings_pass():
    validate_for_production(
        Settings(app_env="production", project="p", use_firestore=True, places_api_key="k", maps_browser_key="m")
    )


def test_production_hides_dev_routes(deps):
    from dataclasses import replace

    prod = replace(deps, settings=Settings(app_env="production"))
    client = TestClient(create_app(prod))
    assert client.get("/dev/new-user").status_code == 404
    assert client.get("/dev/base/" + "a" * 32 + "?m=5000").status_code == 404
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


# --- Firestore の変換（本物の Firestore には接続しない）---

class _Snap:
    def __init__(self, data):
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return self._data


class _Doc:
    def __init__(self, store, key):
        self.store, self.key = store, key

    def set(self, data):
        self.store[self.key] = data

    def get(self):
        return _Snap(self.store.get(self.key))


class _Collection:
    def __init__(self):
        self.store = {}

    def document(self, key):
        return _Doc(self.store, key)


class _Client:
    def __init__(self):
        self.collections = {}

    def collection(self, name):
        return self.collections.setdefault(name, _Collection())


def test_firestore_user_round_trip_with_date():
    client = _Client()
    repo = FirestoreRepository(client)
    user = _user(last_clear_date=NOW.date(), base_escaped_m=1500, total_walked_m=2000, lost_base_rank=3)
    repo.create_user(user)
    assert client.collections["users"].store[user.id]["last_clear_date"] == "2026-11-06"
    assert repo.get_user(user.id) == user
    assert repo.get_user("missing") is None


def test_decode_polyline():
    assert decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@") == [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]


def _routes_http(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_google_routes_backend_parses_route_and_sends_field_mask():
    seen = {}

    def handler(request):
        seen.update(request.headers)
        return httpx.Response(200, json={"routes": [{"distanceMeters": 640, "duration": "480s", "polyline": {"encodedPolyline": "_p~iF~ps|U_ulLnnqC"}}]})

    route = GoogleRoutesBackend("k", _routes_http(handler)).walking(35.0, 139.0, 35.1, 139.1)
    assert route.distance_m == 640 and route.duration_s == 480 and len(route.points) == 2
    assert seen["x-goog-fieldmask"] == ROUTES_FIELD_MASK and seen["x-goog-api-key"] == "k"


def test_google_routes_backend_hides_key_on_error():
    backend = GoogleRoutesBackend("secret-key", _routes_http(lambda _r: httpx.Response(403, json={})))
    with pytest.raises(RoutesError) as info:
        backend.walking(35.0, 139.0, 35.1, 139.1)
    assert "secret-key" not in str(info.value)


def test_google_routes_backend_reports_no_route():
    backend = GoogleRoutesBackend("k", _routes_http(lambda _r: httpx.Response(200, json={})))
    with pytest.raises(RoutesError):
        backend.walking(35.0, 139.0, 35.1, 139.1)


def test_route_finder_caches_nearby_requests():
    class Counting(FakeRoutesBackend):
        calls = 0

        def walking(self, *args):
            Counting.calls += 1
            return super().walking(*args)

    finder = RouteFinder(Counting())
    finder.walking(35.6811, 139.7671, 35.69, 139.77)
    finder.walking(35.6812, 139.7672, 35.69, 139.77)
    assert Counting.calls == 1
