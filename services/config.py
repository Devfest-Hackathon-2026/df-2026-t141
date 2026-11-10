"""環境変数の読み込み。秘密情報はここ以外で参照しない。"""
import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    app_env: str = "local"
    project: str = ""
    use_firestore: bool = False
    places_api_key: str = ""
    routes_api_key: str = ""
    public_base_url: str = ""
    maps_browser_key: str = ""
    maps_map_id: str = "DEMO_MAP_ID"
    notify_secret: str = ""
    firebase_api_key: str = ""
    firebase_app_id: str = ""
    firebase_sender_id: str = ""
    firebase_vapid_key: str = ""

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def has_push(self) -> bool:
        return all(
            (self.project, self.notify_secret, self.firebase_api_key, self.firebase_app_id,
             self.firebase_sender_id, self.firebase_vapid_key)
        )


def load_settings() -> Settings:
    load_dotenv()
    env = os.environ.get
    return Settings(
        app_env=env("APP_ENV") or "local",
        project=env("GOOGLE_CLOUD_PROJECT") or "",
        use_firestore=(env("USE_FIRESTORE") or "").lower() == "true",
        places_api_key=env("PLACES_API_KEY") or "",
        # 別のキーを作らなければ、Places のキーで Routes API を呼ぶ（キーの制限で両方を許可する）
        routes_api_key=env("ROUTES_API_KEY") or env("PLACES_API_KEY") or "",
        public_base_url=(env("PUBLIC_BASE_URL") or "").rstrip("/"),
        maps_browser_key=env("MAPS_BROWSER_KEY") or "",
        maps_map_id=env("MAPS_MAP_ID") or "DEMO_MAP_ID",
        notify_secret=env("NOTIFY_SECRET") or "",
        firebase_api_key=env("FIREBASE_API_KEY") or "",
        firebase_app_id=env("FIREBASE_APP_ID") or "",
        firebase_sender_id=env("FIREBASE_SENDER_ID") or "",
        firebase_vapid_key=env("FIREBASE_VAPID_KEY") or "",
    )


def validate_for_production(settings: Settings) -> None:
    """本番で偽物のデータが動くとデモの偽装になるので、起動を止める。"""
    missing = []
    if not settings.project:
        missing.append("GOOGLE_CLOUD_PROJECT")
    if not settings.use_firestore:
        missing.append("USE_FIRESTORE=true")
    if not settings.places_api_key:
        missing.append("PLACES_API_KEY")
    if not settings.maps_browser_key:
        missing.append("MAPS_BROWSER_KEY")
    if missing:
        raise RuntimeError("本番設定が足りません: " + ", ".join(missing))
