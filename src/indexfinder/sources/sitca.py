from __future__ import annotations

import json
import re
from datetime import date

import httpx
from bs4 import BeautifulSoup

from ..models import Holding, HoldingsSnapshot
from .base import (
    DEFAULT_HEADERS,
    FetchContext,
    FetchError,
    SkipFetch,
    Source,
    clean_text,
    parse_number,
    save_raw,
)
from ..normalize import normalize_ticker

# 投信投顧公會「基金投資明細－季占基金淨資產價值 1% 以上」
#   - 頻率：季（每季底揭露，隔季才更新）
#   - 內容：只含「占淨值 ≥ 1%」的持股，長尾抓不到
#   - 涵蓋：全部投信的國內指數股票型(AH11) + 主動式 ETF(AL11)
#   - 台股標的是 4~6 碼代號，國外是 ISIN（本 source 只取台股）
URL = "https://www.sitca.org.tw/ROC/Industry/IN2630.aspx?pid=IN22601_05"
CLASSES = ("AH11", "AL11")
_CODE = re.compile(r"^\d{4,6}[A-Z]?$")


def _quarter_end(ym: str) -> date:
    y, m = int(ym[:4]), int(ym[4:6])
    last = {3: 31, 6: 30, 9: 30, 12: 31}.get(m, 28)
    return date(y, m, last)


class SitcaSource(Source):
    key = "sitca"

    # process 級快取：一次 fetch 只打 SITCA 3 個 request（1 GET + 2 POST），所有 sitca ETF 共用
    _cache: dict[str, dict[str, list[Holding]]] = {}
    _latest_ym: str | None = None

    def fetch(self, cfg, ctx: FetchContext) -> HoldingsSnapshot:
        needle = cfg.params.get("sitca")
        if not needle:
            raise FetchError(f"{cfg.ticker}: config 缺 params.sitca（SITCA 基金名稱片段）")

        ym, funds = self._bulk(ctx)
        as_of = _quarter_end(ym)

        if (
            not ctx.force
            and ctx.existing_as_of.get(cfg.ticker) == as_of.isoformat()
        ):
            raise SkipFetch(f"SITCA 已是最新一季（{ym}）")

        hits = [name for name in funds if needle in name]
        if not hits:
            raise FetchError(
                f"{cfg.ticker}: SITCA {ym} 找不到基金名稱含「{needle}」的基金"
            )
        if len(hits) > 1:
            raise FetchError(
                f"{cfg.ticker}: 「{needle}」對到 {len(hits)} 檔，請改精確一點："
                + " / ".join(hits)
            )
        fund_name = hits[0]
        holdings = funds[fund_name]
        raw = save_raw(
            ctx.raw_dir,
            cfg.ticker,
            json.dumps(
                {"ym": ym, "fund": fund_name, "holdings": [h.__dict__ for h in holdings]},
                ensure_ascii=False,
                indent=1,
            ).encode("utf-8"),
            "json",
            as_of,
        )
        return HoldingsSnapshot(cfg.ticker, as_of, self.key, holdings, raw)

    # ------------------------------------------------------------------
    @classmethod
    def _bulk(cls, ctx: FetchContext) -> tuple[str, dict[str, list[Holding]]]:
        if cls._latest_ym and cls._latest_ym in cls._cache:
            return cls._latest_ym, cls._cache[cls._latest_ym]

        client = httpx.Client(
            headers={**DEFAULT_HEADERS, "Accept-Language": "zh-TW,zh;q=0.9"},
            timeout=90,
            follow_redirects=True,
        )
        try:
            soup = BeautifulSoup(client.get(URL).text, "lxml")
            ym_opts = [
                o.get("value")
                for o in soup.select('select[name="ctl00$ContentPlaceHolder1$ddlQ_YM"] option')
                if o.get("value")
            ]
            if not ym_opts:
                raise FetchError("SITCA: 抓不到年月選項（頁面結構可能已改）")
            ym = ym_opts[-1]

            # 磁碟快取：同一季只查一次 SITCA，之後每天只花這個 GET
            cache_file = ctx.raw_dir / "_sitca" / f"{ym}.json"
            cached = _load_cache(cache_file)
            if cached is not None:
                cls._latest_ym, cls._cache[ym] = ym, cached
                return ym, cached

            funds: dict[str, list[Holding]] = {}
            for klass in CLASSES:
                html = _post_query(client, soup, ym, klass)
                funds.update(_parse(html))
                soup = BeautifulSoup(html, "lxml")  # 新的 viewstate 給下一次 POST

            if not funds:
                raise FetchError("SITCA: 查詢結果解析不到任何基金")
            _save_cache(cache_file, funds)
            cls._latest_ym = ym
            cls._cache[ym] = funds
            return ym, funds
        finally:
            client.close()


def _load_cache(path) -> dict[str, list[Holding]] | None:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {
            fund: [Holding(**h) for h in holds] for fund, holds in raw.items()
        }
    except (json.JSONDecodeError, TypeError, OSError):
        return None


def _save_cache(path, funds: dict[str, list[Holding]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {f: [h.__dict__ for h in hs] for f, hs in funds.items()},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


# SITCA 基金名稱和 TWSE 簡稱差太多、fuzzy 對不上的，手動指定 {sitca 基金全名: 代號}
_ALIAS: dict[str, str] = {
    "元大摩臺基金": "006203",  # 元大MSCI台灣
    "元大台灣金融基金": "0055",  # 元大MSCI金融
    "元大富櫃50基金": "006201",
    "富邦台灣ETF傘型基金之富邦台灣摩根指數股票型基金": "0057",  # 富邦摩台
    "富蘭克林華美臺灣Smart ETF基金": "00905",  # FT臺灣SMART
    "富蘭克林華美臺灣ESG永續高息ETF基金": "00961",  # FT臺灣永續高息
    "永豐台灣ESG永續優質ETF基金": "00888",  # 永豐台灣ESG
    # 對不上、且不確定代號的（略過，不影響其他）：
    #   中國信託上櫃ESG 30 ETF基金 / 新光臺灣全市場半導體精選30ETF基金
}

_ISSUERS = [
    "元大", "富邦", "國泰", "群益", "復華", "凱基", "永豐", "兆豐", "統一", "野村",
    "第一金", "華南永昌", "新光", "台新", "富蘭克林華美", "大華銀", "安聯", "摩根",
    "聯邦", "玉山", "合庫", "中國信託", "中信", "台中銀", "聯博",
]
_STRIP = [
    "證券投資信託基金", "證券投資信託", "ETF基金", "ETF 基金", "傘型基金之",
    "指數股票型基金", "指數股票型", "基金", "ETF", "主動式", "主動", "證券",
    " ", "　", "之",
]


def _issuer(s: str) -> str | None:
    for i in _ISSUERS:
        if i in s:
            return {"中國信託": "中信", "華南永昌": "華南", "富蘭克林華美": "富蘭克林"}.get(i, i)
    return None


def _norm(s: str) -> str:
    s = s.replace("臺", "台")
    s = re.sub(r"[(（].*?[)）]", "", s)
    for w in _STRIP:
        s = s.replace(w, "")
    return s


def _subseq(a: str, b: str) -> bool:
    it = iter(b)
    return all(c in it for c in a)


def match_funds_to_codes(
    fund_names, twse_names: dict[str, str], skip: set[str]
) -> tuple[dict[str, str], list[str]]:
    """{ticker: sitca_fund_name}, 對不上的 fund_name list。"""
    twn = {c: (_issuer(nm), _norm(nm)) for c, nm in twse_names.items()}
    matched: dict[str, str] = {}
    missed: list[str] = []
    for fund in fund_names:
        if fund in _ALIAS:
            matched[_ALIAS[fund]] = fund
            continue
        fi, fn = _issuer(fund), _norm(fund)
        cands: list[tuple[int, str]] = []
        for code, (ci, cn) in twn.items():
            if code in skip or code in matched:
                continue
            if ci and fi and ci != fi:
                continue
            if not cn or not _subseq(cn, fn):
                continue
            cdig = set(re.findall(r"\d+", cn))
            if cdig and not cdig <= set(re.findall(r"\d+", fn)):
                continue
            cands.append((len(cn), code))
        cands.sort(reverse=True)
        if cands and (len(cands) == 1 or cands[0][0] > cands[1][0]):
            matched[cands[0][1]] = fund
        else:
            # 只把「完全找不到候選代號」算真正對不上；候選被 skip 佔用的不算
            any_cand = any(
                (not ci or not fi or ci == fi) and cn and _subseq(cn, fn)
                for ci, cn in twn.values()
            )
            if not any_cand:
                missed.append(fund)
    return matched, missed


def discover(
    ctx: FetchContext, skip: set[str], skip_needles: set[str] | None = None
) -> tuple[list[dict], list[str]]:
    """回傳 (可寫進 config 的 dict list, 對不上代號的基金名 list)。

    skip          ：已在 etfs.yaml 的 ticker，不重複產生
    skip_needles  ：已在 etfs.yaml 手動對應的 SITCA 名稱片段，這些基金整個略過
    """
    from .twlist import fetch_etf_names

    ym, funds = SitcaSource._bulk(ctx)
    twse = fetch_etf_names()
    todo = [
        f
        for f in funds
        if not any(n and n in f for n in (skip_needles or set()))
    ]
    matched, missed = match_funds_to_codes(todo, twse, skip)
    rows = []
    for code, fund in sorted(matched.items()):
        name = twse.get(code, fund)
        rows.append(
            {
                "ticker": code,
                "name": name,
                "market": "TW",
                "issuer": (_issuer(fund) or "") + "投信",
                "index": name,  # 沒有官方指數名，用 ETF 簡稱當分組
                "index_provider": "",
                "source": "sitca",
                "params": {"sitca": fund},
                "primary": True,
            }
        )
    return rows, missed


def _hidden(soup: BeautifulSoup, name: str) -> str:
    el = soup.find("input", {"name": name})
    return el.get("value", "") if el else ""


def _post_query(client: httpx.Client, soup: BeautifulSoup, ym: str, klass: str) -> str:
    pfx = "ctl00$ContentPlaceHolder1$"
    data = {
        "__VIEWSTATE": _hidden(soup, "__VIEWSTATE"),
        "__VIEWSTATEGENERATOR": _hidden(soup, "__VIEWSTATEGENERATOR"),
        "__EVENTVALIDATION": _hidden(soup, "__EVENTVALIDATION"),
        "__EVENTTARGET": "",
        "__EVENTARGUMENT": "",
        f"{pfx}rdo1": "rbClass",
        f"{pfx}ddlQ_YM": ym,
        f"{pfx}ddlQ_Comid": "A0001",
        f"{pfx}ddlQ_Class": klass,
        f"{pfx}ddlQ_Comid1": "A0001",
        f"{pfx}ddlQ_Class1": klass,
        f"{pfx}BtnQuery": "查詢",
    }
    r = client.post(URL, data=data)
    r.raise_for_status()
    return r.text


def _parse(html: str) -> dict[str, list[Holding]]:
    soup = BeautifulSoup(html, "lxml")
    table = None
    for tb in soup.find_all("table"):
        if "合計" in tb.get_text() and ("台積電" in tb.get_text() or "國內上市" in tb.get_text()):
            table = tb
            break
    if table is None:
        return {}

    funds: dict[str, list[Holding]] = {}
    cur_name: str | None = None
    cur: list[Holding] = []

    def flush():
        nonlocal cur_name, cur
        if cur_name and cur:
            funds[cur_name] = cur
        cur_name, cur = None, []

    for tr in table.find_all("tr"):
        cells = [clean_text(td.get_text(" ", strip=True)) for td in tr.find_all(["td", "th"])]
        if not cells or (len(cells) == 1 and not cells[0]):
            continue
        # 巨大的篩選列（把所有下拉選項 render 成文字）
        if sum(len(c) for c in cells) > 400:
            continue
        if cells[0] == "合計" or (len(cells) == 2 and cells[0].startswith("合計")):
            flush()
            continue
        if len(cells) >= 9 and cells[0] and cells[0] != "基金名稱":
            # 基金標頭列：cell[0]=基金全名，cell[1:] 是第一筆持股
            flush()
            cur_name = cells[0]
            _add_holding(cur, cells[1:])
        elif len(cells) >= 8:
            _add_holding(cur, cells)
    flush()
    return funds


def _add_holding(bucket: list[Holding], cells: list[str]) -> None:
    # [市場別, 代號, 名稱, 金額, '', '', '0', 權重%]
    if len(cells) < 8:
        return
    market, code, name = cells[0], cells[1], cells[2]
    weight = parse_number(cells[-1])
    if "國外" in market:  # 只收台股；國外持股是 ISIN
        return
    if not _CODE.match(code) or weight is None or weight <= 0:
        return
    bucket.append(Holding(normalize_ticker(code, "TW"), code, name, weight))
