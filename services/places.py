"""Places API (New) で近くの公園を探す。"""
import hashlib
import logging
import random
import time
from typing import Protocol

import httpx

from . import game
from .location import destination, direction_label, distance_m, round_coord
from .models import Shelter, ShelterKind, User

logger = logging.getLogger(__name__)

NEARBY_URL = "https://places.googleapis.com/v1/places:searchNearby"
# 住所（短い形）は、名前や位置と同じ料金の区分に入るので、足しても費用は変わらない
FIELD_MASK = "places.id,places.displayName,places.location,places.viewport,places.shortFormattedAddress"
CACHE_SECONDS = 600
CACHE_MAX_ENTRIES = 1000
MAX_RESULT_COUNT = 20  # Nearby Search が1回で返せる上限
_SEARCHES = {"park": "search_parks", "popular": "search_popular_parks", "landmark": "search_landmarks"}
# 公園がない地域の代わりの候補。人がいて場所が分かりやすく、夜でも入りやすい所
LANDMARK_TYPES = [
    "convenience_store", "train_station", "subway_station", "post_office", "library", "community_center", "supermarket",
]


class PlacesError(Exception):
    pass


def _viewport_size(viewport: dict | None) -> tuple[float | None, float | None]:
    try:
        low, high = viewport["low"], viewport["high"]
        height = distance_m(low["latitude"], low["longitude"], high["latitude"], low["longitude"])
        width = distance_m(low["latitude"], low["longitude"], low["latitude"], high["longitude"])
    except (KeyError, TypeError):
        return None, None
    return height, width


class PlacesBackend(Protocol):
    def search_parks(self, lat: float, lng: float, radius_m: int) -> list[dict]: ...
    def search_popular_parks(self, lat: float, lng: float, radius_m: int) -> list[dict]: ...
    def search_landmarks(self, lat: float, lng: float, radius_m: int) -> list[dict]: ...


class GooglePlacesBackend:
    def __init__(self, api_key: str, http: httpx.Client | None = None) -> None:
        self._api_key = api_key
        self._http = http or httpx.Client(timeout=10)

    def search_parks(self, lat: float, lng: float, radius_m: int) -> list[dict]:
        return self._search(lat, lng, radius_m, ["park"])

    def search_popular_parks(self, lat: float, lng: float, radius_m: int) -> list[dict]:
        return self._search(lat, lng, radius_m, ["park"], "POPULARITY")

    def search_landmarks(self, lat: float, lng: float, radius_m: int) -> list[dict]:
        return self._search(lat, lng, radius_m, LANDMARK_TYPES)

    def _search(self, lat: float, lng: float, radius_m: int, types: list[str], rank: str = "DISTANCE") -> list[dict]:
        body = {
            "includedTypes": types,
            "maxResultCount": MAX_RESULT_COUNT,
            "rankPreference": rank,
            "languageCode": "ja",
            "regionCode": "JP",
            "locationRestriction": {
                "circle": {"center": {"latitude": lat, "longitude": lng}, "radius": float(radius_m)}
            },
        }
        headers = {"X-Goog-Api-Key": self._api_key, "X-Goog-FieldMask": FIELD_MASK}
        try:
            response = self._http.post(NEARBY_URL, json=body, headers=headers)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            # 例外の内容にキーが混ざらないよう、種類だけ記録する
            status = getattr(getattr(exc, "response", None), "status_code", "network")
            logger.error("Places API error: %s", status)
            raise PlacesError("公園の検索に失敗しました") from None
        return response.json().get("places", [])


class FakePlacesBackend:
    """開発用。実在しない公園を返す。本番では起動時に弾かれる。"""

    _OFFSETS = [(0.003, 0.001), (0.001, 0.004), (-0.004, 0.002), (0.005, -0.004), (-0.002, -0.007), (0.012, 0.01)]

    def search_parks(self, lat: float, lng: float, radius_m: int) -> list[dict]:
        return self._places(lat, lng, radius_m, "park", "テスト公園")

    def search_popular_parks(self, lat: float, lng: float, radius_m: int) -> list[dict]:
        return self.search_parks(lat, lng, radius_m)

    def search_landmarks(self, lat: float, lng: float, radius_m: int) -> list[dict]:
        return self._places(lat, lng, radius_m, "landmark", "テスト施設")

    def _places(self, lat: float, lng: float, radius_m: int, kind: str, label: str) -> list[dict]:
        places = []
        for index, (dlat, dlng) in enumerate(self._OFFSETS, start=1):
            plat, plng = lat + dlat, lng + dlng
            if distance_m(lat, lng, plat, plng) > radius_m:
                continue
            places.append(
                {
                    "id": f"fake-{kind}-{index}",
                    "displayName": {"text": f"【開発用】{label}{index}"},
                    "shortFormattedAddress": f"開発用の町{index}丁目",
                    "location": {"latitude": plat, "longitude": plng},
                    "viewport": {
                        "low": {"latitude": plat - 0.001, "longitude": plng - 0.001},
                        "high": {"latitude": plat + 0.001, "longitude": plng + 0.001},
                    },
                }
            )
        return places


def _short_address(address: object) -> str:
    """一覧で場所の見当がつくよう、短い住所を付ける。「日本、」や郵便番号が付いていれば外す。"""
    if not isinstance(address, str):
        return ""
    text = address.strip()
    for prefix in ("日本、", "日本 "):
        if text.startswith(prefix):
            text = text[len(prefix):]
    if text.startswith("〒"):
        text = text.split(" ", 1)[-1] if " " in text else ""
    return text[:60]


def _waypoints(lat: float, lng: float, radius_m: int) -> list[Shelter]:
    """周りに公園も施設も見つからないときの、地図上の目印。これで、どこに住んでいても遊べる。
    中心の座標から同じものを作れるので、予約のときに選び直せる。
    地図の真ん中が家だと分からないよう、方角と距離をばらつかせる。"""
    center = f"{lat:.3f},{lng:.3f}"
    rng = random.Random(center)
    # IDは画面に出る。中心（家のことがある）の座標をそのまま入れない
    center_id = hashlib.sha256(center.encode()).hexdigest()[:12]
    step = 360 / game.WAYPOINT_COUNT
    start = rng.uniform(0, step)
    near_m = game.MIN_SHELTER_DISTANCE_M + game.CHECKIN_DEFAULT_M  # 判定の範囲の端まで、最低の距離を歩く
    far_m = min(radius_m, game.WAYPOINT_MAX_M)
    points = sorted(
        (rng.uniform(near_m, far_m), (start + step * index + rng.uniform(-20, 20)) % 360)
        for index in range(game.WAYPOINT_COUNT)
    )
    shelters = []
    for index, (meters, bearing) in enumerate(points):
        point_lat, point_lng = destination(lat, lng, bearing, meters)
        direction = direction_label(lat, lng, point_lat, point_lng)
        shelters.append(
            Shelter(
                place_id=f"waypoint-{center_id}-{index + 1}",
                name=f"目印{'ABCDE'[index]}（{direction}）",
                lat=point_lat,
                lng=point_lng,
                distance_m=round(meters),
                checkin_radius_m=game.CHECKIN_DEFAULT_M,
                direction=direction,
                kind="waypoint",
            )
        )
    return shelters


def _spread(shelters: list[Shelter], radius_m: int) -> list[Shelter]:
    """近すぎる候補を1つにまとめ（同じ公園が2回あっても消える）、近くから遠くまで偏らないように選ぶ。"""
    spacing_m = radius_m * game.SHELTER_SPACING_RATIO
    kept: list[Shelter] = []
    for shelter in sorted(shelters, key=lambda s: s.distance_m):
        if all(distance_m(shelter.lat, shelter.lng, other.lat, other.lng) >= spacing_m for other in kept):
            kept.append(shelter)
    limit = game.MAX_SHELTER_CANDIDATES
    if len(kept) <= limit:
        return kept
    last = len(kept) - 1
    return [kept[round(index * last / (limit - 1))] for index in range(limit)]


class ShelterFinder:
    """検索結果を短時間キャッシュして、予約までの API 呼び出しを減らす。"""

    def __init__(self, backend: PlacesBackend, clock=time.monotonic) -> None:
        self._backend = backend
        self._clock = clock
        self._cache: dict[tuple[float, float, int, str], tuple[float, list[dict]]] = {}

    def _raw(self, lat: float, lng: float, radius_m: int, kind: str = "park") -> list[dict]:
        key = (lat, lng, radius_m, kind)
        hit = self._cache.get(key)
        if hit and self._clock() - hit[0] < CACHE_SECONDS:
            return hit[1]
        places = getattr(self._backend, _SEARCHES[kind])(lat, lng, radius_m)
        if len(self._cache) >= CACHE_MAX_ENTRIES:
            self._cache.clear()
        self._cache[key] = (self._clock(), places)
        return places

    def candidates(self, user: User) -> list[Shelter]:
        """毎日のミッション用。家の周りを探す。"""
        return self.candidates_near(user.home_lat, user.home_lng, game.base_rank(user.base_escaped_m))

    def candidates_near(self, lat: float, lng: float, rank: int, allow_waypoints: bool = True) -> list[Shelter]:
        """指定の地点の周りのシェルター。フリーランでは今いる場所を中心にする。
        近くで開き直しても API を呼ばないよう、中心は約100m単位に丸める。
        公園がなければ、広い範囲のコンビニや駅など、それもなければ地図上の目印を候補にする（候補が0だと遊べないため）。"""
        lat, lng = round_coord(lat), round_coord(lng)
        radius = game.search_radius_m(rank)
        places = self._raw(lat, lng, radius)
        if len(places) >= MAX_RESULT_COUNT:
            # 公園の多い地域では、近い順の上限に届いて遠くの公園が出てこない。人気順でも取り、範囲全体から集める
            try:
                places = places + self._raw(lat, lng, radius, "popular")
            except PlacesError:
                pass  # 近い順の結果だけで続ける
        shelters = _spread(self._to_shelters(places, lat, lng, radius, "park"), radius)
        if shelters:
            return shelters
        wide = max(radius, game.FALLBACK_SEARCH_RADIUS_M)
        try:
            shelters = _spread(self._to_shelters(self._raw(lat, lng, wide, "landmark"), lat, lng, wide, "landmark"), wide)
        except PlacesError:
            shelters = []  # 代わりの検索に失敗しても、目印で遊べるようにする
        if shelters or not allow_waypoints:
            return shelters
        return _waypoints(lat, lng, radius)

    def _to_shelters(self, places: list[dict], lat: float, lng: float, radius: int, kind: ShelterKind) -> list[Shelter]:
        shelters = []
        for place in places:
            try:
                place_lat = place["location"]["latitude"]
                place_lng = place["location"]["longitude"]
                name = place["displayName"]["text"]
                place_id = place["id"]
            except (KeyError, TypeError):
                continue
            distance = distance_m(lat, lng, place_lat, place_lng)
            checkin_radius = game.checkin_radius_m(*_viewport_size(place.get("viewport")))
            # 大きい公園は判定の範囲が広いので、近いと少し歩くだけで着いてしまう。範囲の端までの距離で選ぶ
            if distance - checkin_radius < game.MIN_SHELTER_DISTANCE_M or distance > radius:
                continue
            shelters.append(
                Shelter(
                    place_id=place_id,
                    name=name,
                    lat=place_lat,
                    lng=place_lng,
                    distance_m=round(distance),
                    checkin_radius_m=checkin_radius,
                    direction=direction_label(lat, lng, place_lat, place_lng),
                    address=_short_address(place.get("shortFormattedAddress")),
                    kind=kind,
                )
            )
        return sorted(shelters, key=lambda s: s.distance_m)
