# 定数時間文字列比較。合言葉・管理者トークンのような低リスクな共有シークレット向け。
import hmac


def timing_safe_string_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
