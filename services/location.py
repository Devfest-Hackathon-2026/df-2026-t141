"""距離、方角、座標の計算。A担当の location.py ができたら統合する。"""
from math import asin, atan2, cos, degrees, radians, sin, sqrt

EARTH_RADIUS_M = 6_371_000
_DIRECTIONS = ["北", "北東", "東", "南東", "南", "南西", "西", "北西"]


def distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = radians(lat1), radians(lat2)
    dp, dl = p2 - p1, radians(lng2 - lng1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * asin(sqrt(a))


def bearing_deg(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """地点1から見た地点2の方位（北が0度、時計回り）。"""
    p1, p2 = radians(lat1), radians(lat2)
    dl = radians(lng2 - lng1)
    y = sin(dl) * cos(p2)
    x = cos(p1) * sin(p2) - sin(p1) * cos(p2) * cos(dl)
    return (degrees(atan2(y, x)) + 360) % 360


def direction_label(lat1: float, lng1: float, lat2: float, lng2: float) -> str:
    return _DIRECTIONS[round(bearing_deg(lat1, lng1, lat2, lng2) / 45) % 8]


def destination(lat: float, lng: float, bearing: float, meters: float) -> tuple[float, float]:
    """地点から指定の方位へ進んだ先の座標。"""
    angular = meters / EARTH_RADIUS_M
    p1, b = radians(lat), radians(bearing)
    p2 = asin(sin(p1) * cos(angular) + cos(p1) * sin(angular) * cos(b))
    l2 = radians(lng) + atan2(sin(b) * sin(angular) * cos(p1), cos(angular) - sin(p1) * sin(p2))
    return degrees(p2), (degrees(l2) + 540) % 360 - 180


def round_coord(value: float) -> float:
    """保存する位置を小数第3位に丸める（誤差は最大約70m）。"""
    return round(value, 3)


def round_chase_coord(value: float) -> float:
    """追跡中の最後の位置は小数第4位に丸める（誤差は最大約7m）。
    粗く丸めると、ゾンビが本人から数十m離れた点で止まり、画面の動きと捕獲の判定がずれるため。"""
    return round(value, 4)
