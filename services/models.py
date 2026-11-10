"""users / missions のデータ形式。A担当との共通の約束は docs/api.md を参照。"""
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, field_validator

from . import game


MissionStatus = Literal["reserved", "escaped", "cleared", "failed"]
LastResult = Literal["cleared", "failed", "captured", "run_captured"]
ModeName = Literal["normal", "hard"]
MissionKind = Literal["daily", "free"]  # 毎日の時刻のミッションと、今すぐ始めるフリーラン
OutfitColor = Literal["lime", "orange", "blue", "red", "violet", "white"]
SkinColor = Literal["light", "fair", "tan", "brown", "dark"]
ItemKind = Literal["repel", "material", "freeze"]  # repel（撃退）はやめた。前に作ったミッションを読めるよう残す
ShelterKind = Literal["park", "landmark", "waypoint"]  # 公園、公園がないときのコンビニや駅など、それもないときの地図上の目印


class GeoPoint(BaseModel):
    lat: float
    lng: float


class Item(BaseModel):
    """逃げる途中で拾うアイテム。資材は拠点ポイントに、フリーズはゾンビを1日止める券になる。"""

    id: str
    kind: ItemKind
    lat: float
    lng: float
    collected: bool = False


class SavedApproach(BaseModel):
    """フリーランの間だけ預かる、毎日のゾンビが家へ近づく動きの基準。"""

    lat: float
    lng: float
    anchor_at: datetime


class User(BaseModel):
    id: str
    nickname: str
    home_lat: float  # 丸め済み。画面やAPIの応答には絶対に含めない
    home_lng: float
    daily_notify_time: str
    avatar_outfit: OutfitColor = "lime"
    avatar_skin: SkinColor = "fair"
    base_escaped_m: int = 0  # 拠点のランクを決める逃げた距離の累計。捕まると0に戻る
    total_walked_m: int = 0  # 減らない生涯の累計。成果画面で使う
    best_base_rank: int = 1
    lost_base_rank: int | None = None  # 捕まって崩れたランク。次にクリアするまで画面に出す
    streak: int = 0
    last_clear_date: date | None = None
    freeze_count: int = 0  # ためたフリーズ。使うと次の毎日の時刻にゾンビが来ない
    zombie_lat: float
    zombie_lng: float
    zombie_anchor_at: datetime
    target_lat: float  # プレイヤーの最後の位置（丸め済み）。画面やAPIの応答には含めない
    target_lng: float
    arrival_at: datetime  # ゾンビが家に着く時刻（次の毎日の時刻）
    chasing: bool = False  # 家を出たあとはモードの速さで追いかける
    chase_mode: ModeName | None = None
    extra_zombies: list[GeoPoint] = []  # ハードで追加される2体。本体と基準時刻・目標を共有する
    saved_approach: SavedApproach | None = None  # フリーランが終わったら、毎日のゾンビはこの動きの続きから近づく
    capture_count: int = 0
    last_result: LastResult | None = None
    push_enabled: bool = False
    push_fid: str | None = None
    notified_level: int = 0
    notified_capture_count: int = 0
    notified_arrival_at: datetime | None = None  # 時刻の通知をどの到着まで送ったか
    notified_arrival_stage: int = 0
    device_hashes: list[str] = []  # このユーザーを開ける端末の鍵のハッシュ。URLだけでは開けない
    transfer_code_hash: str | None = None  # 別の端末へ移るコードのハッシュ
    created_at: datetime


class Shelter(BaseModel):
    place_id: str
    name: str
    lat: float
    lng: float
    distance_m: int
    checkin_radius_m: int
    direction: str
    address: str = ""  # 短い住所（町名など）。前に作ったミッションには入っていない
    kind: ShelterKind = "park"

    @field_validator("checkin_radius_m")
    @classmethod
    def _cap_checkin_radius(cls, value: int) -> int:
        # 上限を下げる前（最大200m）に予約したシェルターも、今の上限で判定し、画面にも同じ値を出す
        return min(value, game.CHECKIN_MAX_M)


class ClearResult(BaseModel):
    walked_m: int  # 実際に計上した歩いた距離
    mode: ModeName = "normal"
    mode_bonus_m: int = 0  # ハードの上乗せ
    material_bonus_m: int = 0
    freeze_gained: bool = False  # フリーズを持ち帰った
    base_points_gained: int = 0  # 拠点ポイントに入った合計
    base_rank_before: int
    base_rank_after: int
    base_escaped_m_after: int
    base_rank_name_after: str
    streak: int
    covered_by_free_run: bool = False  # 毎日の時刻にフリーランで外にいたので達成扱い


class Mission(BaseModel):
    id: str
    user_id: str
    status: MissionStatus = "reserved"
    depart_at: datetime
    shelter: Shelter
    kind: MissionKind = "daily"
    mode: ModeName = "normal"
    items: list[Item] = []
    material_bonus_m: int = 0  # 拾った資材。シェルターに着いたときに拠点ポイントへ入る
    freeze_found: bool = False  # フリーズを拾った。シェルターに着いたときに1つたまる
    escaped_at: datetime | None = None
    cleared_at: datetime | None = None
    ended_at: datetime | None = None  # クリアか捕獲で終わった時刻
    result: ClearResult | None = None
    created_at: datetime


class ZombiePublic(BaseModel):
    lat: float | None  # 追跡中だけ。家へ近づいている間は、座標から家の位置が分かるので出さない
    lng: float | None
    km: float


class UserPublic(BaseModel):
    """APIで返す形。家の座標を含めないよう、項目を明示している。"""

    nickname: str
    avatar_outfit: OutfitColor
    avatar_skin: SkinColor
    base_rank: int
    base_rank_name: str
    base_escaped_m: int
    next_rank_m: int | None
    lost_base_rank: int | None
    lost_base_rank_name: str | None
    total_walked_m: int
    streak: int
    freeze_count: int
    zombie_km: float
    zombie_lat: float | None
    zombie_lng: float | None
    zombie_speed_km_per_hour: float
    zombies: list[ZombiePublic]  # 全員。zombie_* は最も近い1体
    arrival_at: datetime
    chasing: bool
    chase_mode: ModeName | None
    capture_count: int
    last_result: LastResult | None
    daily_notify_time: str
    home_url: str


class MissionPublic(BaseModel):
    id: str
    status: MissionStatus
    depart_at: datetime
    shelter: Shelter
    kind: MissionKind
    mode: ModeName
    items: list[Item]
    material_bonus_m: int
    freeze_found: bool
    escape_url: str
    home_url: str
    night_warning: bool
    result: ClearResult | None


class WalkRecord(BaseModel):
    cleared_at: datetime
    shelter_name: str
    walked_m: int


class UserStats(BaseModel):
    """成果画面の集計。"""

    total_walked_m: int
    week_walked_m: int
    clear_count: int
    capture_count: int
    streak: int
    base_rank: int
    best_base_rank: int
    history: list[WalkRecord]
