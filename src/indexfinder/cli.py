from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import date

from . import paths
from .config import load_etfs
from .db import DB
from .models import HoldingsSnapshot
from .query import QueryEngine
from .sources import REGISTRY, FetchContext, FetchError, SkipFetch
from .sources.base import prune_raw

log = logging.getLogger("indexfinder")


# 各來源可接受的資料新鮮度（天）。SITCA 是季資料、且隔季才更新，本來就會很舊。
_MAX_AGE_DAYS = {"sitca": 200}
_DEFAULT_MAX_AGE = 14


def _validate(snap: HoldingsSnapshot) -> list[str]:
    warns: list[str] = []
    n = len(snap.holdings)
    tw = snap.total_weight
    # SITCA 只揭露占淨值 ≥1% 的持股，加總本來就會低於 100%
    min_sum = 40.0 if snap.source == "sitca" else 85.0
    if n < 5:
        warns.append(f"只有 {n} 檔持股")
    if not (min_sum <= tw <= 103.0):
        warns.append(f"權重加總 {tw:.1f}%（預期 {min_sum:.0f}~100%）")
    age = (date.today() - snap.as_of_date).days
    if age > _MAX_AGE_DAYS.get(snap.source, _DEFAULT_MAX_AGE):
        warns.append(f"資料為 {age} 天前（{snap.as_of_date}）")
    if age < 0:
        warns.append(f"資料日期在未來（{snap.as_of_date}）")
    return warns


def _sync_tw(quiet: bool = False) -> int:
    """自動抓 SITCA 全部國內股票型 ETF，對應 TWSE 代號，寫進 config/tw_etfs.generated.yaml。"""
    import yaml

    from .config import GENERATED_NAME
    from .sources.base import FetchContext
    from .sources.sitca import discover

    hand = load_etfs(paths.CONFIG_PATH, include_generated=False)
    hand_mapped = {c.ticker for c in hand}
    hand_needles = {c.params.get("sitca") for c in hand if c.source == "sitca"}
    ctx = FetchContext(raw_dir=paths.RAW_DIR, manual_dir=paths.MANUAL_DIR)
    rows, missed = discover(ctx, skip=hand_mapped, skip_needles={n for n in hand_needles if n})
    out = paths.CONFIG_PATH.parent / GENERATED_NAME
    out.write_text(
        "# 自動產生，勿手動編輯。用 `indexfinder sync-tw` 重新產生。\n"
        "# 手動對應 / 覆蓋請改 config/etfs.yaml（同 ticker 以 etfs.yaml 為準）。\n"
        + yaml.safe_dump({"etfs": rows}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    if not quiet:
        print(f"sync-tw：新增 {len(rows)} 檔台股 ETF → {out.name}")
        if missed:
            print(f"  對不上 TWSE 代號（{len(missed)}，需在 sitca.py 的 _ALIAS 補）：")
            for m in missed:
                print(f"    - {m}")
    return 0


def cmd_sync_tw(args) -> int:
    return _sync_tw()


def cmd_fetch(args) -> int:
    from .config import GENERATED_NAME

    gen = paths.CONFIG_PATH.parent / GENERATED_NAME
    stale = not gen.exists() or (time.time() - gen.stat().st_mtime) > 30 * 86400
    if args.sync_tw or (stale and not args.only and not args.market):
        _sync_tw(quiet=True)

    cfgs = load_etfs(paths.CONFIG_PATH)
    if args.only:
        want = {t.upper() for t in args.only}
        cfgs = [c for c in cfgs if c.ticker in want]
    if args.market:
        cfgs = [c for c in cfgs if c.market == args.market.upper()]
    if not cfgs:
        print("沒有符合條件的 ETF")
        return 1

    db = DB()
    ctx = FetchContext(
        raw_dir=paths.RAW_DIR,
        manual_dir=paths.MANUAL_DIR,
        allow_browser=args.browser,
        force=args.refresh,
        existing_as_of={t: r["as_of_date"] for t, r in db.latest_snapshots().items()},
    )
    ok: list[str] = []
    failed: list[tuple[str, str]] = []

    explicit = {t.upper() for t in (args.only or [])}
    skipped: list[str] = []
    uptodate: list[str] = []
    for c in cfgs:
        src = REGISTRY.get(c.source)
        if src is None:
            failed.append((c.ticker, f"未知的 source '{c.source}'"))
            print(f"[FAIL] {c.ticker:7} 未知的 source '{c.source}'")
            continue
        # manual-source ETF 沒有手動檔又沒被 --only 指名 -> 安靜跳過（避免每次 fetch 都噴 FAIL）
        if (
            c.source == "manual"
            and c.ticker not in explicit
            and not (paths.MANUAL_DIR / f"{c.ticker}.csv").exists()
        ):
            skipped.append(c.ticker)
            continue
        try:
            snap = src.fetch(c, ctx)
            if not snap.holdings:
                raise FetchError("解析到 0 檔持股（來源檔可能暫時是空的），保留舊資料")
            warns = _validate(snap)
            db.upsert_snapshot(snap)
            prune_raw(paths.RAW_DIR, c.ticker, keep=args.keep_raw)
            tag = "OK  " if not warns else "WARN"
            print(
                f"[{tag}] {c.ticker:7} {c.market}  {len(snap.holdings):4d} 檔  "
                f"Σ={snap.total_weight:6.1f}%  {snap.as_of_date}  {c.index_name}"
            )
            for w in warns:
                print(f"         ! {w}")
            ok.append(c.ticker)
        except SkipFetch as e:
            uptodate.append(c.ticker)
            if c.ticker in explicit:
                print(f"[--  ] {c.ticker:7} {e}")
        except FetchError as e:
            print(f"[FAIL] {c.ticker:7} {e}")
            failed.append((c.ticker, str(e)))
        except Exception as e:  # noqa: BLE001
            log.exception("unexpected error for %s", c.ticker)
            print(f"[ERR ] {c.ticker:7} {e!r}")
            failed.append((c.ticker, repr(e)))
        time.sleep(args.sleep)

    # 清掉不在 config 的 ETF 舊資料 + 壓縮 DB
    pruned = db.prune({c.ticker for c in load_etfs(paths.CONFIG_PATH)})
    _prune_empty_raw_dirs()

    tail = ""
    if uptodate:
        tail += f"、{len(uptodate)} 已最新"
    if skipped:
        tail += f"、{len(skipped)} 跳過"
    print(f"\n完成：{len(ok)} 成功、{len(failed)} 失敗{tail}")
    if uptodate:
        print(f"  已是最新（來源無新資料）：{', '.join(uptodate)}")
    if skipped:
        print(f"  跳過（manual source 無手動檔）：{', '.join(skipped)}")
    if pruned["removed_etfs"]:
        print(f"  清除已移除的 ETF：{', '.join(pruned['removed_etfs'])}")
    for t, e in failed:
        print(f"  - {t}: {e}")
    n_snap, n_hold = db.row_counts()
    print(f"  SQL Server（{db.schema}）：{n_snap} snapshots、{n_hold} holdings；raw 資料夾 {_dirsize(paths.RAW_DIR)}")
    return 0 if not failed else 1


def _prune_empty_raw_dirs() -> None:
    if not paths.RAW_DIR.is_dir():
        return
    for d in paths.RAW_DIR.iterdir():
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()


def _dirsize(p) -> str:
    total = sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() else 0
    return _human(total)


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def cmd_prune(args) -> int:
    db = DB()
    cfgs = load_etfs(paths.CONFIG_PATH)
    known = {c.ticker for c in cfgs}
    before = db.row_counts()
    removed_raw = 0
    for c in cfgs:
        removed_raw += prune_raw(paths.RAW_DIR, c.ticker, keep=args.keep_raw)
    # 不在 config 的 raw 資料夾整個刪（_ 開頭是內部快取，如 _sitca，保留）
    if paths.RAW_DIR.is_dir():
        for d in list(paths.RAW_DIR.iterdir()):
            if d.is_dir() and not d.name.startswith("_") and d.name not in known:
                for f in d.iterdir():
                    f.unlink()
                    removed_raw += 1
                d.rmdir()
        # _sitca 只留最新一季
        prune_raw(paths.RAW_DIR, "_sitca", keep=1)
    pruned = db.prune(known)
    _prune_empty_raw_dirs()
    after = db.row_counts()
    print(f"刪除 raw 檔 {removed_raw} 個")
    if pruned["removed_etfs"]:
        print(f"清除已移除的 ETF：{', '.join(pruned['removed_etfs'])}")
    print(f"SQL Server（{db.schema}）：{before[0]} → {after[0]} snapshots、{before[1]} → {after[1]} holdings")
    print(f"raw 資料夾：{_dirsize(paths.RAW_DIR)}")
    return 0


def cmd_query(args) -> int:
    db = DB()
    engine = QueryEngine(db, load_etfs(paths.CONFIG_PATH))
    result = engine.lookup(args.tickers, args.market)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _tailscale_ip() -> str | None:
    import ipaddress
    import os
    import shutil
    import subprocess

    candidates = [
        shutil.which("tailscale"),
        "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
        "/usr/local/bin/tailscale",
    ]
    for exe in candidates:
        if exe and os.path.exists(exe):
            try:
                out = subprocess.run(
                    [exe, "ip", "-4"], capture_output=True, text=True, timeout=5
                ).stdout.strip().splitlines()
                if out:
                    return out[0].strip()
            except (OSError, subprocess.SubprocessError):
                pass
    # 後備：從網路介面找 100.64.0.0/10 (Tailscale CGNAT) 位址
    try:
        import subprocess

        cgnat = ipaddress.ip_network("100.64.0.0/10")
        out = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=5).stdout
        for tok in re.findall(r"inet (100\.\d+\.\d+\.\d+)", out):
            if ipaddress.ip_address(tok) in cgnat:
                return tok
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return None


def cmd_serve(args) -> int:
    import socket
    import uvicorn

    if args.tailscale:
        host = _tailscale_ip()
        if not host:
            print("找不到 Tailscale IP。請確認 Tailscale app 已登入並連線。")
            return 1
        print(f"\n  Tailscale： http://{host}:{args.port}   ← 任何裝置連上 Tailscale 後用這個\n")
    elif args.lan:
        host = "0.0.0.0"
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            lan_ip = s.getsockname()[0]
            s.close()
        except OSError:
            lan_ip = "<本機 IP>"
        print(f"\n  本機：   http://127.0.0.1:{args.port}")
        print(f"  同網路： http://{lan_ip}:{args.port}   ← 手機／其他電腦用這個\n")
    else:
        host = args.host

    uvicorn.run(
        "indexfinder.server:app",
        host=host,
        port=args.port,
        reload=args.reload,
    )
    return 0


def cmd_status(args) -> int:
    db = DB()
    cfgs = load_etfs(paths.CONFIG_PATH)
    latest = db.latest_snapshots()
    print(f"{'TICKER':8} {'MKT':4} {'AS OF':12} {'#':>5}  INDEX")
    for c in cfgs:
        s = latest.get(c.ticker)
        if s:
            print(
                f"{c.ticker:8} {c.market:4} {s['as_of_date']:12} {s['holding_count']:5d}  {c.index_name}"
            )
        else:
            print(f"{c.ticker:8} {c.market:4} {'(無資料)':12} {'-':>5}  {c.index_name}")
    print(f"\n{len(latest)}/{len(cfgs)} 檔 ETF 有資料")
    return 0


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="indexfinder", description="股票 → 指數 / ETF / 權重")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="抓取並更新 ETF 持股資料")
    f.add_argument("--only", nargs="*", metavar="TICKER", help="只抓指定的 ETF")
    f.add_argument("--market", choices=["US", "TW", "us", "tw"], help="只抓某市場")
    f.add_argument("--browser", action="store_true", help="被擋時用 headless 瀏覽器 fallback")
    f.add_argument("--sleep", type=float, default=1.0, help="每檔之間的間隔秒數")
    f.add_argument("--keep-raw", type=int, default=2, help="每檔 ETF 保留幾份原始下載檔（預設 2）")
    f.add_argument(
        "--refresh",
        action="store_true",
        help="即使來源沒有更新的資料也強制重抓（預設 SITCA 等季更來源會自動跳過）",
    )
    f.add_argument("--sync-tw", action="store_true", help="重新產生台股 ETF 自動清單再抓")
    f.set_defaults(func=cmd_fetch)

    sy = sub.add_parser("sync-tw", help="重新產生台股 ETF 自動對應清單（SITCA ↔ TWSE 代號）")
    sy.set_defaults(func=cmd_sync_tw)

    q = sub.add_parser("query", help="命令列查詢（輸出 JSON）")
    q.add_argument("tickers", nargs="+", metavar="TICKER")
    q.add_argument("--market", choices=["US", "TW", "us", "tw"])
    q.set_defaults(func=cmd_query)

    s = sub.add_parser("serve", help="啟動網頁介面")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--lan", action="store_true", help="開放同網路其他裝置連（手機等）")
    s.add_argument("--tailscale", action="store_true", help="只綁 Tailscale IP（任何地方可連、不對外曝露）")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--reload", action="store_true")
    s.set_defaults(func=cmd_serve)

    st = sub.add_parser("status", help="列出每檔 ETF 的資料狀態")
    st.set_defaults(func=cmd_status)

    pr = sub.add_parser("prune", help="清掉過時 / 已移除 ETF 的資料並壓縮 DB")
    pr.add_argument("--keep-raw", type=int, default=2, help="每檔 ETF 保留幾份原始下載檔（預設 2）")
    pr.set_defaults(func=cmd_prune)

    args = p.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    raise SystemExit(main())
