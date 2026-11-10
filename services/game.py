"""ゲームの計算。外部サービスに依存しない。数値はここだけで調整する。"""
import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from .location import bearing_deg, destination, distance_m

JST = timezone(timedelta(hours=9))

# ゾンビ: 毎日の時刻に家へ着く速さで近づき、家を出たらモードの速さで追いかける
ZOMBIE_START_KM = 5.0
CATCH_KM = 0.001  # 座標計算の誤差で捕獲を逃さないよう、1m以内は追いついた扱いにする

# 判定
ESCAPE_DISTANCE_M = 200
EARLY_START = timedelta(minutes=10)
# シェルターに着いたとみなす半径。公園の大きさで変えるが、大きい公園でも中心から100mまで
CHECKIN_MIN_M = 50
CHECKIN_MAX_M = 100
CHECKIN_DEFAULT_M = 80  # 大きさが分からない施設と、地図上の目印
CHECKIN_MAX_ACCURACY_M = 50  # チェックインは精度の良い位置でだけ判定する。誤差のぶん手前でゴールにならないように
MAX_GPS_ACCURACY_M = 100
# シェルターの候補は、判定の範囲の端まで家から最低これだけある所に限る。家にいても脱出できるので、これだけは歩く
MIN_SHELTER_DISTANCE_M = ESCAPE_DISTANCE_M + 50
# 時刻に家からこれ以上離れた所で脱出したら外出先とみなし、予約したシェルターが遠ければ近くのものに替える
OUTSIDE_FROM_HOME_M = 1500

# 追跡中に位置が途絶えたときの扱い。ブラウザは画面を消すと位置を取れない
POSITION_FRESH = timedelta(seconds=45)  # これ以内に位置が届いていれば、画面で追えている（static/escape.js と同じ値）
POSITION_GIVE_UP = timedelta(minutes=60)  # これだけ届かなければ、放置したものとして捕獲する
ORPHAN_RUN_GRACE = timedelta(minutes=1)  # 追跡が終わったのに逃走中のまま残ったミッションを閉じるまでの猶予

# 歩いた距離。画面が数えた値を、直線距離から「時速15kmで歩き続けた距離」までに収める
MAX_WALK_SPEED_KM_PER_HOUR = 15.0
MIN_TRACK_ACCURACY_M = 30

# 成果画面
STATS_WEEK_DAYS = 7
STATS_HISTORY_LIMIT = 10

# 拠点。逃げた距離の累計で豪華になり、捕まると振り出しに戻る。
# 毎日1回、ノーマルで範囲の中くらいのシェルターへ逃げると、2日で小屋、2週間で要塞、3か月ほどで10段階目になる。
# 最初は上がりやすく、だんだん必要なポイントが増える
BASE_RANKS = (
    (0, "テント"),
    (1000, "小屋"),
    (3000, "バリケードの家"),
    (7000, "見張り台つきの砦"),
    (13000, "要塞"),
    (23000, "二重壁の要塞"),
    (40000, "発電所つきの要塞"),
    (65000, "監視塔の要塞"),
    (100000, "菜園つきの城塞"),
    (155000, "灯台の城塞都市"),
)

# シェルター検索
BASE_SEARCH_RADIUS_M = 500
SEARCH_RADIUS_STEP_M = 250
MAX_SEARCH_RADIUS_M = 3000
# 公園が見つからない人のための代わりの候補。広い範囲のコンビニや駅など、それもなければ地図上の目印
FALLBACK_SEARCH_RADIUS_M = 1500
WAYPOINT_COUNT = 5
WAYPOINT_MAX_M = 700
# 候補の選び方。近い公園どうしは1つにまとめ、近くから遠くまで偏らないように出す
SHELTER_SPACING_RATIO = 0.2  # 候補どうしの最低の間隔（探索半径に対する割合）。地図のピンが重ならない程度
MAX_SHELTER_CANDIDATES = 12

# 悪用で外部APIの料金が膨らまないようにする回数の上限。普通に遊ぶ人には届かない数にしている
MAX_USERS_PER_IP_PER_HOUR = 10
MAX_SHELTER_SEARCHES_PER_DAY = 100
MAX_ROUTES_PER_DAY = 100
MAX_LINK_TRIES_PER_IP_PER_HOUR = 10  # 引き継ぎコードの総当たりを防ぐ

# 予約
MAX_MISSIONS_PER_DAY = 5
DARK_START_HOUR = 18
DARK_END_HOUR = 5

# 状況に応じた通知。深夜は送らない
NOTIFY_THRESHOLDS_KM = (3.0, 1.0, 0.3)
QUIET_START_HOUR = 22
QUIET_END_HOUR = 7


@dataclass(frozen=True)
class Mode:
    label: str
    speed_km_per_hour: float  # 追いかけてくる速さ。家へ近づく段階は時刻に合わせるので関係しない
    zombies: int
    point_rate: float  # 拠点ポイントの倍率


# 徒歩は時速5km前後。ノーマルは普通に歩くとじわじわ追いつかれ、早歩きで逃げ切れる。ハードは走らないと逃げ切れない
MODES = {
    "normal": Mode("ノーマル", 6.0, 1, 1.0),
    "hard": Mode("ハード", 9.0, 3, 1.5),
}
# ハードの追加のゾンビは、本体と同じ方面から群れで追ってくる。本体の左右に少しずらし、少し後ろに置く
EXTRA_ZOMBIE_OFFSETS = ((-15.0, 0.08), (15.0, 0.16))  # （本体の方角からずらす角度、本体より後ろの距離 km）
# 追跡が始まるときのゾンビの置き場所。シェルターと反対側から、左右にこの角度までずらす（家の位置を推測させないため）
CHASE_SPREAD_DEG = 70.0

# フリーラン。何回でも走れる。ゾンビは背後から追ってくる
FREE_RUN_ZOMBIE_KM = 0.4
MAX_FREE_RUNS_PER_DAY = 20  # 悪用で Places の利用料が膨らまないようにする上限

# アバターの色。服と肌を選べる
AVATAR_OUTFITS = {
    "lime": 0xC6F135, "orange": 0xFF8A2A, "blue": 0x2F7FD6, "red": 0xE5484D, "violet": 0x8E6CEF, "white": 0xE8ECE4,
}
AVATAR_SKINS = {"light": 0xF3D3B5, "fair": 0xE3B48F, "tan": 0xC08A5E, "brown": 0x8D5A3B, "dark": 0x5C3A26}
DEFAULT_OUTFIT = "lime"
DEFAULT_SKIN = "fair"

# アイテム。脱出した地点からシェルターへの直線上に並べ、近づくと自動で拾う
# 資材だけを置く。撃退アイテム（ゾンビを遠ざける）はやめた
ITEM_SPACING_M = 300
ITEM_MIN = 1
ITEM_MAX = 3
ITEM_LATERAL_M = 30
ITEM_COLLECT_RADIUS_M = 40
ITEM_MAX_ACCURACY_M = 50
ITEM_HOME_EXCLUSION_M = ESCAPE_DISTANCE_M  # 家の位置を推測させないよう、家の近くには置かない
MATERIAL_BONUS_M = 200
# フリーズ。毎日の逃走の道に、2〜3回に1回落ちている。シェルターまで持ち帰ると1つたまり、
# 拠点で使うと、次の毎日の時刻にゾンビが来なくなる（1日休める。連続日数も切れない）
FREEZE_ITEM_CHANCE = 0.4
FREEZE_MAX = 3  # ためておける数


@dataclass(frozen=True)
class ZombieState:
    lat: float
    lng: float
    km: float


def zombie_state(
    lat: float, lng: float, anchor_at: datetime, target_lat: float, target_lng: float, now: datetime,
    speed_km_per_hour: float,
) -> ZombieState:
    """基準時刻の位置から、目標（プレイヤーの最後の位置）へ直進した現在の状態。"""
    total_m = distance_m(lat, lng, target_lat, target_lng)
    hours = max(0.0, (now - anchor_at).total_seconds() / 3600)
    moved_m = speed_km_per_hour * hours * 1000
    if moved_m >= total_m:
        return ZombieState(target_lat, target_lng, 0.0)
    fraction = moved_m / total_m
    return ZombieState(
        lat + (target_lat - lat) * fraction,
        lng + (target_lng - lng) * fraction,
        (total_m - moved_m) / 1000,
    )


def zombie_on_trail(
    lat: float, lng: float, anchor_at: datetime, via_lat: float, via_lng: float, to_lat: float, to_lng: float,
    now: datetime, speed_km_per_hour: float,
) -> ZombieState:
    """最後に見えた位置（via）まで直進し、余った分だけ今の位置（to）へ進めた状態。km は今の位置までの距離。
    画面を消して位置が途絶えていた間も、ゾンビは足取りを追い続けていたものとして計算する。"""
    at_via = zombie_state(lat, lng, anchor_at, via_lat, via_lng, now, speed_km_per_hour)
    if at_via.km > 0:
        return ZombieState(at_via.lat, at_via.lng, distance_m(at_via.lat, at_via.lng, to_lat, to_lng) / 1000)
    hours = max(0.0, (now - anchor_at).total_seconds() / 3600)
    spare_m = speed_km_per_hour * hours * 1000 - distance_m(lat, lng, via_lat, via_lng)
    total_m = distance_m(via_lat, via_lng, to_lat, to_lng)
    if spare_m >= total_m:
        return ZombieState(to_lat, to_lng, 0.0)
    fraction = spare_m / total_m
    return ZombieState(
        via_lat + (to_lat - via_lat) * fraction,
        via_lng + (to_lng - via_lng) * fraction,
        (total_m - spare_m) / 1000,
    )


def approach_state(
    lat: float, lng: float, anchor_at: datetime, home_lat: float, home_lng: float, arrival_at: datetime, now: datetime
) -> ZombieState:
    """基準時刻の位置から家へ向かい、arrival_at にちょうど着く状態。"""
    span = (arrival_at - anchor_at).total_seconds()
    progress = 1.0 if span <= 0 else min(1.0, max(0.0, (now - anchor_at).total_seconds() / span))
    total_m = distance_m(lat, lng, home_lat, home_lng)
    return ZombieState(
        lat + (home_lat - lat) * progress,
        lng + (home_lng - lng) * progress,
        total_m * (1 - progress) / 1000,
    )


def _parse_time(daily_time: str) -> time:
    hour, minute = (int(part) for part in daily_time.split(":"))
    return time(hour, minute, tzinfo=JST)


def daily_at_on(daily_time: str, day: date) -> datetime:
    """その日（日本時間）の毎日の時刻。"""
    return datetime.combine(day, _parse_time(daily_time))


def next_daily_at(daily_time: str, now: datetime) -> datetime:
    """次に毎日の時刻が来る日時。"""
    at = daily_at_on(daily_time, jst_date(now))
    return at if at > now else at + timedelta(days=1)


def first_arrival_at(daily_time: str, now: datetime) -> datetime:
    """登録して最初にゾンビが来る時刻。ぎりぎりに登録しても、ゾンビがフリーランと同じ距離から歩いて来るだけの時間は残す。"""
    soonest = now + timedelta(hours=FREE_RUN_ZOMBIE_KM / MODES["normal"].speed_km_per_hour)
    return max(next_daily_at(daily_time, now), soonest)


def first_zombie_km(arrival_at: datetime, now: datetime) -> float:
    """登録したときのゾンビの距離。ノーマルの速さでちょうど着く距離にする（フリーランと同じ近さから、いつもの遠さまで）。"""
    hours = (arrival_at - now).total_seconds() / 3600
    return max(FREE_RUN_ZOMBIE_KM, min(ZOMBIE_START_KM, hours * MODES["normal"].speed_km_per_hour))


def next_daily_after(daily_time: str, after_day: date, now: datetime) -> datetime:
    """after_day より後の日で、今より後に来る毎日の時刻。1日に2回来させないために使う。"""
    return max(daily_at_on(daily_time, after_day + timedelta(days=1)), next_daily_at(daily_time, now))


def plan_items(
    from_lat: float, from_lng: float, to_lat: float, to_lng: float, rng: random.Random
) -> list[tuple[str, float, float]]:
    """脱出した地点からシェルターへの直線上に、資材を等間隔に並べる。左右に少しずらす。"""
    total_m = distance_m(from_lat, from_lng, to_lat, to_lng)
    count = max(ITEM_MIN, min(ITEM_MAX, int(total_m // ITEM_SPACING_M)))
    heading = bearing_deg(from_lat, from_lng, to_lat, to_lng)
    items = []
    for index in range(count):
        lat, lng = destination(from_lat, from_lng, heading, total_m * (index + 1) / (count + 1))
        side_m = rng.uniform(-ITEM_LATERAL_M, ITEM_LATERAL_M)
        lat, lng = destination(lat, lng, (heading + (90 if side_m >= 0 else 270)) % 360, abs(side_m))
        items.append(("material", lat, lng))
    return items


def plan_freeze(
    from_lat: float, from_lng: float, to_lat: float, to_lng: float, rng: random.Random
) -> tuple[float, float] | None:
    """毎日の逃走で、フリーズを置くならその位置。2〜3回に1回、道のりの途中（4〜6割の所）に置く。"""
    if rng.random() >= FREEZE_ITEM_CHANCE:
        return None
    total_m = distance_m(from_lat, from_lng, to_lat, to_lng)
    heading = bearing_deg(from_lat, from_lng, to_lat, to_lng)
    lat, lng = destination(from_lat, from_lng, heading, total_m * rng.uniform(0.4, 0.6))
    side_m = rng.uniform(-ITEM_LATERAL_M, ITEM_LATERAL_M)
    return destination(lat, lng, (heading + (90 if side_m >= 0 else 270)) % 360, abs(side_m))


def place_zombie(
    target_lat: float, target_lng: float, from_lat: float, from_lng: float, km: float
) -> tuple[float, float]:
    """今いる方角を保ったまま、目標から指定の距離へ置き直す。"""
    bearing = bearing_deg(target_lat, target_lng, from_lat, from_lng)
    return destination(target_lat, target_lng, bearing, km * 1000)


def base_rank(escaped_m: int) -> int:
    """逃げた距離の累計から拠点のランク（1から）を出す。"""
    return sum(1 for required_m, _name in BASE_RANKS if escaped_m >= required_m)


def base_rank_name(rank: int) -> str:
    return BASE_RANKS[max(1, min(rank, len(BASE_RANKS))) - 1][1]


def next_rank_m(escaped_m: int) -> int | None:
    """次のランクに必要な累計距離。最大ランクなら None。"""
    rank = base_rank(escaped_m)
    return BASE_RANKS[rank][0] if rank < len(BASE_RANKS) else None


def search_radius_m(rank: int) -> int:
    return min(BASE_SEARCH_RADIUS_M + SEARCH_RADIUS_STEP_M * (rank - 1), MAX_SEARCH_RADIUS_M)


def credited_walk_m(reported_m: float | None, straight_m: float, elapsed: timedelta) -> int:
    """計上する歩いた距離。画面を閉じて歩いた人にも直線距離は認め、乗り物や書き換えは時間で上限をかける。"""
    hours = max(0.0, elapsed.total_seconds() / 3600)
    upper_m = MAX_WALK_SPEED_KM_PER_HOUR * hours * 1000
    walked_m = max(reported_m or 0.0, straight_m)
    return round(max(0.0, min(walked_m, upper_m)))


def jst_date(moment: datetime) -> date:
    return moment.astimezone(JST).date()


def next_streak(streak: int, last_clear: date | None, now: datetime) -> int:
    """クリア時の連続日数。同じ日は増やさず、1日空いたら1に戻す。"""
    today = jst_date(now)
    if last_clear == today:
        return streak
    if last_clear == today - timedelta(days=1):
        return streak + 1
    return 1


def effective_streak(streak: int, last_clear: date | None, now: datetime) -> int:
    """表示用。昨日までにクリアしていなければ連続は切れている。"""
    if last_clear is None or last_clear < jst_date(now) - timedelta(days=1):
        return 0
    return streak


def checkin_radius_m(viewport_height_m: float | None, viewport_width_m: float | None) -> int:
    """公園の大きさに合わせた半径。入口に着いたら判定に入るようにする。"""
    if viewport_height_m is None or viewport_width_m is None:
        return CHECKIN_DEFAULT_M
    half_long_side = max(viewport_height_m, viewport_width_m) / 2
    return round(min(CHECKIN_MAX_M, max(CHECKIN_MIN_M, half_long_side)))


def notify_level(km: float) -> int:
    """何段階目の接近通知まで進んでいるか。"""
    return sum(1 for threshold_km in NOTIFY_THRESHOLDS_KM if km <= threshold_km)


def arrival_stage(arrival_at: datetime, now: datetime) -> int:
    """毎日の時刻に合わせた通知の段階。0: まだ、1: 脱出できるようになった。"""
    return 1 if now >= arrival_at - EARLY_START else 0


def is_quiet_hours(moment: datetime) -> bool:
    hour = moment.astimezone(JST).hour
    return hour >= QUIET_START_HOUR or hour < QUIET_END_HOUR


def is_dark(moment: datetime) -> bool:
    hour = moment.astimezone(JST).hour
    return hour >= DARK_START_HOUR or hour < DARK_END_HOUR
