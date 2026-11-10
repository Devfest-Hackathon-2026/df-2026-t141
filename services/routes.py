"""Routes API でシェルターまでの徒歩ルートを取る。"""
import logging
import time
from dataclasses import dataclass
from typing import Protocol

import httpx

from .location import round_coord

logger = logging.getLogger(__name__)

ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
FIELD_MASK = "routes.distanceMeters,routes.duration,routes.polyline.encodedPolyline"
CACHE_SECONDS = 600
CACHE_MAX_ENTRIES = 1000


class RoutesError(Exception):
    pass


@dataclass(frozen=True)
class WalkingRoute:
    points: list[tuple[float, float]]
    distance_m: int
    duration_s: int


def decode_polyline(encoded: str) -> list[tuple[float, float]]:
    """Google のエンコード済みポリラインを緯度経度の一覧に戻す。"""
    points: list[tuple[float, float]] = []
    index = lat = lng = 0
    while index < len(encoded):
        deltas = []
        for _ in range(2):
            shift = result = 0
            while True:
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            deltas.append(~(result >> 1) if result & 1 else result >> 1)
        lat += deltas[0]
        lng += deltas[1]
        points.append((lat / 1e5, lng / 1e5))
    return points


class RoutesBackend(Protocol):
    def walking(self, from_lat: float, from_lng: float, to_lat: float, to_lng: float) -> WalkingRoute: ...


class GoogleRoutesBackend:
    def __init__(self, api_key: str, http: httpx.Client | None = None) -> None:
        self._api_key = api_key
        self._http = http or httpx.Client(timeout=10)

    def walking(self, from_lat: float, from_lng: float, to_lat: float, to_lng: float) -> WalkingRoute:
        body = {
            "origin": {"location": {"latLng": {"latitude": from_lat, "longitude": from_lng}}},
            "destination": {"location": {"latLng": {"latitude": to_lat, "longitude": to_lng}}},
            "travelMode": "WALK",
            "languageCode": "ja",
            "regionCode": "JP",
        }
        headers = {"X-Goog-Api-Key": self._api_key, "X-Goog-FieldMask": FIELD_MASK}
        try:
            response = self._http.post(ROUTES_URL, json=body, headers=headers)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            # 例外の内容にキーが混ざらないよう、種類だけ記録する
            status = getattr(getattr(exc, "response", None), "status_code", "network")
            logger.error("Routes API error: %s", status)
            raise RoutesError("徒歩ルートを取得できませんでした") from None
        routes = response.json().get("routes") or []
        if not routes:
            raise RoutesError("徒歩ルートが見つかりませんでした")
        route = routes[0]
        try:
            points = decode_polyline(route["polyline"]["encodedPolyline"])
            seconds = int(str(route.get("duration", "0s")).rstrip("s") or 0)
        except (KeyError, TypeError, ValueError, IndexError):
            raise RoutesError("徒歩ルートを取得できませんでした") from None
        return WalkingRoute(points, int(route.get("distanceMeters", 0)), seconds)


class FakeRoutesBackend:
    """開発用。道路を無視して、曲がり角が1つある道を返す。"""

    def walking(self, from_lat: float, from_lng: float, to_lat: float, to_lng: float) -> WalkingRoute:
        points = [(from_lat, from_lng), (from_lat, to_lng), (to_lat, to_lng)]
        distance = round((abs(to_lat - from_lat) * 111000) + (abs(to_lng - from_lng) * 91000))
        return WalkingRoute(points, distance, round(distance / 1.3))


class RouteFinder:
    """同じ場所からの取り直しで API を呼ばないよう、短時間キャッシュする。位置は保存しない。"""

    def __init__(self, backend: RoutesBackend, clock=time.monotonic) -> None:
        self._backend = backend
        self._clock = clock
        self._cache: dict[tuple, tuple[float, WalkingRoute]] = {}

    def walking(self, from_lat: float, from_lng: float, to_lat: float, to_lng: float) -> WalkingRoute:
        key = (round_coord(from_lat), round_coord(from_lng), to_lat, to_lng)
        hit = self._cache.get(key)
        if hit and self._clock() - hit[0] < CACHE_SECONDS:
            return hit[1]
        route = self._backend.walking(from_lat, from_lng, to_lat, to_lng)
        if len(self._cache) >= CACHE_MAX_ENTRIES:
            self._cache.clear()
        self._cache[key] = (self._clock(), route)
        return route
