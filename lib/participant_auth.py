# 参加者向け(合言葉)の認証。管理者認証(lib/admin_auth.py)とは別系統。
from lib.compare import timing_safe_string_equal


def check_passphrase(config: dict, body: dict | None) -> bool:
    """合言葉が不要、または一致していればTrue"""
    if config.get("securityMode") != "very_easy":
        return True
    expected = (config.get("passphrase") or "").strip()
    if not expected:
        return True
    supplied = str((body or {}).get("passphrase") or "").strip()
    return timing_safe_string_equal(supplied, expected)
