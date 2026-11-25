"""环境变量与日期上下文。"""
import datetime
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path=ROOT / ".env"):
    """读取同目录 .env（不覆盖已存在的环境变量）。.env 已在 .gitignore 中。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if v.strip():
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def today():
    return os.environ.get("PARLEYWISE_TODAY") or datetime.date.today().isoformat()
