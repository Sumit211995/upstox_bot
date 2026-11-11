"""Settings from environment / .env. PAPER TRADING ONLY: this bot never sends orders."""
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load_env(path=HERE / ".env"):
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# The paper "books": one per index × strategy. Settings come from the walk-forward research
# (research/out/best.json, chosen on 2022–2024 and checked on 2025–2026). Override with BOOKS_FILE.
DEFAULT_BOOKS_FILE = HERE / "books.json"


@dataclass(frozen=True)
class Config:
    indices: tuple = ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY")
    capital: float = 200000.0                 # paper capital per book (₹)
    lots: int = 1                             # lots per trade
    poll_seconds: int = 20
    telegram_token: str = ""
    telegram_chat_id: str = ""
    books: tuple = field(default_factory=tuple)

    def book(self, book_id):
        return next((b for b in self.books if b["id"] == book_id), None)


def load_books(path=None):
    p = Path(path or os.getenv("BOOKS_FILE") or DEFAULT_BOOKS_FILE)
    return tuple(json.loads(p.read_text())) if p.exists() else ()


def load_config():
    _load_env()
    g = os.getenv
    idx = tuple(x.strip().upper() for x in g("INDICES", "NIFTY,BANKNIFTY,SENSEX,MIDCPNIFTY").split(",") if x.strip())
    books = tuple(b for b in load_books() if b["index"] in idx and b.get("enabled", True))
    return Config(indices=idx, capital=float(g("PAPER_CAPITAL", "200000")), lots=int(g("LOTS", "1")),
                  poll_seconds=int(g("POLL_SECONDS", "20")), telegram_token=g("TELEGRAM_TOKEN", ""),
                  telegram_chat_id=g("TELEGRAM_CHAT_ID", ""), books=books)
