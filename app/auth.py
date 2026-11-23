"""账号与会话：scrypt 口令哈希 + 随机会话令牌（库里只存哈希），HttpOnly Cookie。

管理账号用命令行（没有网页管理界面）：
  uv run python -m app.auth add <用户名> <显示名> [--admin] [--password-stdin]   # 默认交互输入口令
  uv run python -m app.auth passwd <用户名>
  uv run python -m app.auth disable <用户名> | enable <用户名>
  uv run python -m app.auth list
"""
import getpass, hashlib, hmac, secrets, sys, time

from . import db

SESSION_COOKIE = "parleywise_session"
SESSION_TTL = 12 * 3600          # 12 小时，覆盖一个工作日
MAX_FAILS, LOCK_SECONDS = 5, 300  # 同一用户名连续 5 次失败锁 5 分钟
_fails = {}                       # username -> (次数, 首次失败时间)；单进程内存即可


def hash_pw(pw):
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1)
    return f"scrypt${salt.hex()}${h.hex()}"


def check_pw(pw, stored):
    try:
        _, salt, h = stored.split("$")
    except ValueError:
        return False
    calc = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1)
    return hmac.compare_digest(calc.hex(), h)


def _th(token):
    return hashlib.sha256(token.encode()).hexdigest()


def login(username, pw):
    """成功返回 (token, user)；失败返回 (None, 错误信息)。"""
    n, t0 = _fails.get(username, (0, 0))
    if n >= MAX_FAILS and time.time() - t0 < LOCK_SECONDS:
        return None, "失败次数过多，请 5 分钟后再试"
    with db.db() as con:
        u = con.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if not u or u["disabled"] or not check_pw(pw, u["pw_hash"]):
            _fails[username] = (n + 1, t0 if n and time.time() - t0 < LOCK_SECONDS else time.time())
            return None, "用户名或口令不对"
        _fails.pop(username, None)
        token = secrets.token_urlsafe(32)
        con.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
        con.execute("INSERT INTO sessions(token_hash, user_id, expires_at) VALUES (?,?,?)",
                    (_th(token), u["id"], time.time() + SESSION_TTL))
        return token, public(u)


def user_for(token):
    if not token:
        return None
    with db.db() as con:
        u = con.execute("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id"
                        " WHERE s.token_hash=? AND s.expires_at>? AND u.disabled=0",
                        (_th(token), time.time())).fetchone()
    return public(u) if u else None


def logout(token):
    with db.db() as con:
        con.execute("DELETE FROM sessions WHERE token_hash=?", (_th(token or ""),))


def public(u):
    return {"id": u["id"], "username": u["username"], "name": u["display_name"], "role": u["role"]}


def has_users():
    with db.db() as con:
        return con.execute("SELECT COUNT(*) FROM users WHERE disabled=0").fetchone()[0] > 0


# ---------- 命令行 ----------
def _ask_pw():
    if "--password-stdin" in sys.argv:  # 给脚本用：echo 口令 | ... --password-stdin
        pw = sys.stdin.readline().rstrip("\n")
        if len(pw) < 8:
            sys.exit("口令至少 8 位")
        return pw
    pw = getpass.getpass("口令（至少 8 位）：")
    if len(pw) < 8 or pw != getpass.getpass("再输一次："):
        sys.exit("口令太短或两次不一致")
    return pw


def main(argv):
    db.init()
    cmd, args = (argv[0] if argv else "list"), argv[1:]
    with db.db() as con:
        if cmd == "add" and len(args) >= 2:
            if con.execute("SELECT 1 FROM users WHERE username=?", (args[0],)).fetchone():
                sys.exit("用户名已存在")
            con.execute("INSERT INTO users(username, display_name, pw_hash, role, created_at) VALUES (?,?,?,?,?)",
                        (args[0], args[1], hash_pw(_ask_pw()), "admin" if "--admin" in args else "reviewer",
                         time.time()))
        elif cmd == "passwd" and args:
            if not con.execute("UPDATE users SET pw_hash=? WHERE username=?", (hash_pw(_ask_pw()), args[0])).rowcount:
                sys.exit("没有这个用户")
            con.execute("DELETE FROM sessions WHERE user_id=(SELECT id FROM users WHERE username=?)", (args[0],))
        elif cmd in ("disable", "enable") and args:
            con.execute("UPDATE users SET disabled=? WHERE username=?", (cmd == "disable", args[0]))
            con.execute("DELETE FROM sessions WHERE user_id=(SELECT id FROM users WHERE username=?)", (args[0],))
        elif cmd == "list":
            for u in con.execute("SELECT username, display_name, role, disabled FROM users ORDER BY id"):
                print("\t".join([u["username"], u["display_name"], u["role"]] + (["已停用"] if u["disabled"] else [])))
            return
        else:
            sys.exit(__doc__)
    print("完成")


if __name__ == "__main__":
    main(sys.argv[1:])
