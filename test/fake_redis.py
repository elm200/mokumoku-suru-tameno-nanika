"""テスト用の最小インメモリRedis。

lib.redis_client の redis_command / redis_pipeline を差し替えて、Upstashに繋がずに
ルートを通しで動かせるようにする。認可まわりは実際に動かさないと確認できないため。
"""

STORE = {}
PUBLISHED = []


def reset():
    STORE.clear()
    PUBLISHED.clear()


def _run(cmd):
    op = cmd[0].upper()
    args = [str(a) for a in cmd[1:]]
    if op == "GET":
        return STORE.get(args[0])
    if op == "SET":
        STORE[args[0]] = args[1]
        return "OK"
    if op == "DEL":
        return int(STORE.pop(args[0], None) is not None)
    if op == "EXPIRE":
        return 1
    if op == "INCR":
        STORE[args[0]] = str(int(STORE.get(args[0], 0)) + 1)
        return int(STORE[args[0]])
    if op == "HSET":
        STORE.setdefault(args[0], {})[args[1]] = args[2]
        return 1
    if op == "HDEL":
        return int(STORE.get(args[0], {}).pop(args[1], None) is not None)
    if op == "HGETALL":
        flat = []
        for k, v in STORE.get(args[0], {}).items():
            flat += [k, v]
        return flat
    if op == "RPUSH":
        STORE.setdefault(args[0], []).append(args[1])
        return len(STORE[args[0]])
    if op == "LRANGE":
        return list(STORE.get(args[0], []))
    if op == "LTRIM":
        lst = STORE.get(args[0], [])
        start, end = int(args[1]), int(args[2])
        STORE[args[0]] = lst[start:] if end == -1 else lst[start:end + 1]
        return "OK"
    if op == "PUBLISH":
        PUBLISHED.append(args[1])
        return 1
    raise AssertionError(f"未対応のコマンド: {op}")


async def redis_command(command):
    return _run(command)


async def redis_pipeline(commands):
    return [_run(c) for c in commands]


def install():
    import lib.redis_client as rc
    import lib.board, lib.snapshot, lib.discord, lib.admin_auth, lib.rate_limit
    rc.redis_command = redis_command
    rc.redis_pipeline = redis_pipeline
    for mod in (lib.board, lib.snapshot, lib.discord, lib.admin_auth, lib.rate_limit):
        if hasattr(mod, "redis_command"):
            mod.redis_command = redis_command
        if hasattr(mod, "redis_pipeline"):
            mod.redis_pipeline = redis_pipeline
