#!/usr/bin/env python3
import csv
import http.client
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


ROOT = os.path.dirname(os.path.abspath(__file__))
HTML_FILE = os.path.join(ROOT, "quant-mini-app.html")
TOKEN = "D43BF722C8E33BFB4CCDD645719E5169"
CACHE_FILE = os.path.join(ROOT, "quant_universe_cache.json")
UNIVERSE_CACHE = {"time": 0, "rows": []}
STAGE_CACHE = {}
EASTMONEY_CLIST_HOSTS = [
    "https://push2delay.eastmoney.com/api/qt/clist/get",
    "https://push2.eastmoney.com/webguest/api/qt/clist/get",
    "https://82.push2.eastmoney.com/webguest/api/qt/clist/get",
    "http://80.push2.eastmoney.com/api/qt/clist/get",
    "http://40.push2.eastmoney.com/api/qt/clist/get",
    "https://82.push2.eastmoney.com/api/qt/clist/get",
    "https://push2.eastmoney.com/api/qt/clist/get",
]


def compact_date(days_ago):
    return time.strftime("%Y%m%d", time.localtime(time.time() - days_ago * 86400))


def get_json(url, timeout=15, retries=3):
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
        "Connection": "close",
        "Referer": "https://data.eastmoney.com/",
    }
    last_error = None
    for attempt in range(max(1, retries)):
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, http.client.RemoteDisconnected, ConnectionResetError) as exc:
            last_error = exc
            if attempt < retries - 1:
                time.sleep(0.45 * (attempt + 1))
                continue
            if "api/qt/clist/get" in url and "eastmoney.com" in url:
                try:
                    result = subprocess.run(
                        [
                            "curl",
                            "-fsSL",
                            "--max-time",
                            str(timeout),
                            "-A",
                            headers["User-Agent"],
                            "-H",
                            f"Accept: {headers['Accept']}",
                            "-H",
                            f"Accept-Language: {headers['Accept-Language']}",
                            "-H",
                            f"Referer: {headers['Referer']}",
                            url,
                        ],
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    return json.loads(result.stdout)
                except Exception:
                    pass
            raise
    raise last_error


def get_text(url, timeout=15):
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def load_universe_cache():
    memory_rows = UNIVERSE_CACHE.get("rows") or []
    if memory_rows:
        return memory_rows
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f)
        rows = payload.get("rows") or []
        if rows:
            UNIVERSE_CACHE.update({"time": payload.get("time") or 0, "rows": rows})
            return rows
    except Exception:
        return []
    return []


def save_universe_cache(rows):
    if not rows:
        return
    payload = {"time": time.time(), "rows": rows}
    UNIVERSE_CACHE.update(payload)
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception:
        pass


def normalize_symbol(symbol, market):
    clean = (symbol or "").strip().upper()
    if market == "cn":
        return clean.replace(".SH", "").replace(".SZ", "").replace(".SS", "")
    if market == "hk":
        return clean.replace(".HK", "").zfill(4)
    if market == "crypto":
        return clean.replace("-", "").replace("_", "").replace("/", "")
    return clean.replace(".US", "").replace(".NASDAQ", "").replace(".NYSE", "")


def cn_market_prefix(symbol):
    return "1" if symbol.startswith(("6", "9")) else "0"


def parse_float(value, default=None):
    try:
        if value is None:
            return default
        text = str(value).strip().replace(",", "").replace("%", "")
        if not text or text in ("--", "None", "null"):
            return default
        return float(text)
    except Exception:
        return default


def parse_int(value, default=None):
    try:
        if value is None:
            return default
        text = str(value).strip().replace(",", "")
        if not text or text in ("--", "None", "null"):
            return default
        return int(float(text))
    except Exception:
        return default


def eastmoney_suggest(text):
    url = (
        "https://searchapi.eastmoney.com/api/suggest/get?"
        + urllib.parse.urlencode({"input": text, "type": "14", "token": TOKEN})
    )
    return get_json(url)


def eastmoney_daily(symbol):
    code = normalize_symbol(symbol, "cn")
    if not (len(code) == 6 and code.isdigit()):
        raise RuntimeError(f"未识别A股代码：{symbol}")
    secid = f"{cn_market_prefix(code)}.{code}"
    query = urllib.parse.urlencode(
        {
            "secid": secid,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": "101",
            "fqt": "1",
            "beg": compact_date(1400),
            "end": compact_date(0),
        }
    )
    payload = get_json(f"https://push2his.eastmoney.com/api/qt/stock/kline/get?{query}")
    rows = []
    for line in payload.get("data", {}).get("klines", []) or []:
        cells = str(line).split(",")
        if len(cells) >= 6:
            rows.append({
                "date": cells[0],
                "open": float(cells[1]),
                "close": float(cells[2]),
                "high": float(cells[3]),
                "low": float(cells[4]),
                "volume": float(cells[5]),
            })
    return rows


def eastmoney_fund_flow_daily(symbol):
    code = normalize_symbol(symbol, "cn")
    if not (len(code) == 6 and code.isdigit()):
        raise RuntimeError(f"未识别A股代码：{symbol}")
    secid = f"{cn_market_prefix(code)}.{code}"
    query = urllib.parse.urlencode(
        {
            "secid": secid,
            "fields1": "f1,f2,f3,f7",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
            "lmt": "20",
        }
    )
    payload = get_json(f"https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?{query}")
    rows = []
    for line in payload.get("data", {}).get("klines", []) or []:
        cells = str(line).split(",")
        if len(cells) >= 6:
            rows.append({
                "date": cells[0],
                "mainNet": parse_float(cells[1]),
                "superNet": parse_float(cells[2]),
                "largeNet": parse_float(cells[3]),
                "midNet": parse_float(cells[4]),
                "smallNet": parse_float(cells[5]),
                "raw": cells,
            })
    return rows


def yahoo_symbol(symbol, market):
    code = normalize_symbol(symbol, market)
    return f"{code}.HK" if market == "hk" else code


def yahoo_daily(symbol, market):
    now = int(time.time())
    start = now - 1400 * 86400
    code = yahoo_symbol(symbol, market)
    query = urllib.parse.urlencode(
        {
            "interval": "1d",
            "period1": start,
            "period2": now,
            "events": "history",
            "includeAdjustedClose": "true",
        }
    )
    payload = get_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(code)}?{query}")
    result = (payload.get("chart", {}).get("result") or [{}])[0]
    stamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    adj = (((result.get("indicators") or {}).get("adjclose") or [{}])[0].get("adjclose") or [])
    close = quote.get("close") or []
    rows = []
    for i, stamp in enumerate(stamps):
        price = adj[i] if i < len(adj) and adj[i] is not None else close[i] if i < len(close) else None
        if price:
            open_price = quote.get("open", [None] * len(stamps))[i] if i < len(quote.get("open", [])) else None
            high_price = quote.get("high", [None] * len(stamps))[i] if i < len(quote.get("high", [])) else None
            low_price = quote.get("low", [None] * len(stamps))[i] if i < len(quote.get("low", [])) else None
            volume = quote.get("volume", [None] * len(stamps))[i] if i < len(quote.get("volume", [])) else None
            rows.append({
                "date": time.strftime("%Y-%m-%d", time.gmtime(stamp)),
                "open": float(open_price) if open_price else float(price),
                "close": float(price),
                "high": float(high_price) if high_price else float(price),
                "low": float(low_price) if low_price else float(price),
                "volume": float(volume) if volume else None,
            })
    return rows


def stooq_daily(symbol, market):
    code = normalize_symbol(symbol, market).lower()
    suffix = ".hk" if market == "hk" else ".us" if market == "us" else ""
    query = urllib.parse.urlencode({"s": f"{code}{suffix}", "i": "d", "d1": compact_date(1400), "d2": compact_date(0)})
    text = get_text(f"https://stooq.com/q/d/l/?{query}")
    rows = []
    for row in csv.DictReader(text.splitlines()):
        date = row.get("Date") or row.get("date")
        close = row.get("Close") or row.get("close")
        if date and close and close != "N/D":
            rows.append({
                "date": date,
                "open": float(row.get("Open") or row.get("open") or close),
                "close": float(close),
                "high": float(row.get("High") or row.get("high") or close),
                "low": float(row.get("Low") or row.get("low") or close),
                "volume": float(row.get("Volume") or row.get("volume") or 0) or None,
            })
    return rows


def crypto_daily(symbol):
    code = normalize_symbol(symbol, "crypto")
    query = urllib.parse.urlencode({"symbol": code, "interval": "1d", "limit": "1000"})
    payload = get_json(f"https://api.binance.com/api/v3/klines?{query}")
    return [{
        "date": time.strftime("%Y-%m-%d", time.gmtime(item[0] / 1000)),
        "open": float(item[1]),
        "close": float(item[4]),
        "high": float(item[2]),
        "low": float(item[3]),
        "volume": float(item[5]),
    } for item in payload]


def tencent_code(symbol, market):
    code = normalize_symbol(symbol, market)
    if market == "cn":
        prefix = "sh" if code.startswith(("6", "9")) else "sz"
        return f"{prefix}{code}"
    if market == "hk":
        return f"hk{code}"
    if market == "us":
        return f"us{code}"
    return code


def tencent_quote(symbol, market):
    code = tencent_code(symbol, market)
    url = f"https://qt.gtimg.cn/q={urllib.parse.quote(code)}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://gu.qq.com/",
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        text = response.read().decode("gbk", "replace")
    if "=\"" not in text:
        raise RuntimeError("腾讯财经没有返回报价")
    payload = text.split("=\"", 1)[1].rsplit("\"", 1)[0]
    cells = payload.split("~")
    if len(cells) < 6:
        raise RuntimeError("腾讯财经报价格式异常")
    name = cells[1] if len(cells) > 1 else symbol
    price = float(cells[3]) if cells[3] else None
    prev_close = float(cells[4]) if len(cells) > 4 and cells[4] else None
    open_price = float(cells[5]) if len(cells) > 5 and cells[5] else None
    change = price - prev_close if price is not None and prev_close else None
    pct = change / prev_close * 100 if change is not None and prev_close else None
    return {
        "symbol": normalize_symbol(symbol, market),
        "market": market,
        "name": name,
        "price": price,
        "prevClose": prev_close,
        "open": open_price,
        "change": change,
        "changePct": pct,
        "source": "腾讯财经",
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "rawCode": code,
    }


def tencent_quote_raw(code, name):
    url = f"https://qt.gtimg.cn/q={urllib.parse.quote(code)}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://gu.qq.com/",
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        text = response.read().decode("gbk", "replace")
    payload = text.split("=\"", 1)[1].rsplit("\"", 1)[0] if "=\"" in text else ""
    cells = payload.split("~")
    price = float(cells[3]) if len(cells) > 3 and cells[3] else None
    prev_close = float(cells[4]) if len(cells) > 4 and cells[4] else None
    change = price - prev_close if price is not None and prev_close else None
    pct = change / prev_close * 100 if change is not None and prev_close else None
    return {
        "name": name,
        "code": code,
        "price": price,
        "change": change,
        "changePct": pct,
        "source": "腾讯财经",
    }


def market_overview():
    indexes = [
        ("sh000001", "上证指数"),
        ("sz399001", "深证成指"),
        ("sz399006", "创业板指"),
        ("sh000688", "科创50"),
    ]
    rows = []
    for code, name in indexes:
        try:
            rows.append(tencent_quote_raw(code, name))
        except Exception as exc:
            rows.append({"name": name, "code": code, "error": str(exc), "source": "腾讯财经"})
    valid = [row for row in rows if isinstance(row.get("changePct"), (int, float))]
    avg_pct = sum(row["changePct"] for row in valid) / len(valid) if valid else None
    if avg_pct is None:
        mood = "未知"
        risk = 0
    elif avg_pct >= 1:
        mood = "偏强"
        risk = 8
    elif avg_pct <= -1:
        mood = "偏弱"
        risk = -10
    else:
        mood = "震荡"
        risk = 0
    return {
        "source": "腾讯财经指数报价",
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "indexes": rows,
        "avgChangePct": avg_pct,
        "mood": mood,
        "scoreBias": risk,
    }


def is_cn_main_board(code):
    return code.startswith(("000", "001", "002", "003", "600", "601", "603", "605"))


def is_cn_all_board(code):
    return code.startswith((
        "000", "001", "002", "003", "004", "005",
        "300", "301", "302",
        "600", "601", "603", "605",
        "688", "689",
        "8", "43", "83", "87", "88", "92"
    ))


def parse_universe_items(items, target, market="cn"):
    for item in items or []:
        code = str(item.get("f12") or "").strip()
        name = str(item.get("f14") or "").strip()
        if not (len(code) == 6 and code.isdigit() and name):
            continue
        if market == "cn" and not is_cn_main_board(code):
            continue
        if market == "all" and not is_cn_all_board(code):
            continue
        if "ST" in name.upper() or "退" in name:
            continue
        target[f"cn:{code}"] = {
            "market": "cn",
            "symbol": code,
            "name": name,
            "price": item.get("f2"),
            "changePct": item.get("f3"),
            "volume": item.get("f5"),
            "amount": item.get("f6"),
            "marketValue": item.get("f20"),
            "source": "东方财富A股股票池",
        }


def clist_payload(fs, page=1, page_size=5000):
    query = urllib.parse.urlencode(
        {
            "pn": str(page),
            "pz": str(page_size),
            "po": "1",
            "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2",
            "invt": "2",
            "fid": "f6",
            "fs": fs,
            "fields": "f12,f14,f2,f3,f5,f6,f20",
        }
    )
    last_error = None
    for base in EASTMONEY_CLIST_HOSTS:
        try:
            return get_json(f"{base}?{query}", timeout=12, retries=2)
        except Exception as exc:
            last_error = exc
            continue
    raise last_error


def sector_clist_payload(fs, sort_field="f3", desc=True, page_size=160):
    query = urllib.parse.urlencode(
        {
            "pn": "1",
            "pz": str(page_size),
            "po": "1" if desc else "0",
            "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2",
            "invt": "2",
            "fid": sort_field,
            "fs": fs,
            "fields": "f12,f14,f3,f62,f128,f140",
        }
    )
    last_error = None
    for base in EASTMONEY_CLIST_HOSTS:
        try:
            return get_json(f"{base}?{query}", timeout=12, retries=2)
        except Exception as exc:
            last_error = exc
            continue
    raise last_error


def a_share_universe(limit=10000, market="cn", use_cache=True):
    now = time.time()
    cached_rows = UNIVERSE_CACHE.get("rows") or []
    if use_cache and market == "cn" and len(cached_rows) >= 1000 and now - (UNIVERSE_CACHE.get("time") or 0) < 15 * 60:
        return cached_rows[:limit]

    rows_by_code = {}
    errors = []
    max_rows = max(10, min(limit, 10000))
    page_size = 200
    boards = [
        ("深市主板", "m:0+t:6,m:0+t:80"),
        ("沪市主板", "m:1+t:2,m:1+t:23"),
    ]
    if market == "all":
        boards = [
            ("深市主板", "m:0+t:6,m:0+t:80"),
            ("沪市主板", "m:1+t:2,m:1+t:23"),
            ("创业板", "m:0+t:80"),
            ("科创板", "m:1+t:23"),
            ("北交所", "m:0+t:81"),
        ]
    for board_name, fs in boards:
        total_pages = None
        for page in range(1, 101):
            try:
                payload = clist_payload(fs, page, page_size)
            except Exception as exc:
                label = f"{board_name}第{page}页" if page > 1 else f"{board_name}首页"
                errors.append(f"{label}：{exc}")
                break
            data = payload.get("data", {}) or {}
            items = data.get("diff", []) or []
            if page == 1:
                total_pages = parse_int(data.get("pages")) or parse_int(data.get("pagecount")) or parse_int(data.get("pageCount"))
                total_count = parse_int(data.get("total")) or parse_int(data.get("count"))
                if not total_pages and total_count:
                    total_pages = max(1, (total_count + page_size - 1) // page_size)
            if not items:
                break
            before = len(rows_by_code)
            parse_universe_items(items, rows_by_code, market=market)
            if len(rows_by_code) >= max_rows:
                break
            if total_pages and page >= total_pages:
                break
            if page > 1 and len(rows_by_code) == before:
                break

    rows = list(rows_by_code.values())[:max_rows]
    if rows:
        if market == "cn" and len(rows) >= 1000:
            save_universe_cache(rows)
        return rows
    disk_rows = load_universe_cache()
    if market == "cn" and len(disk_rows) >= 1000:
        return disk_rows[:limit]
    message = "；".join(errors[:4]) or "远端没有返回股票列表"
    scope_text = "A股全市场" if market == "all" else "A股主板"
    raise RuntimeError(f"{scope_text}股票池读取失败：{message}")


def parse_sector_rows(items):
    rows = []
    for item in items or []:
        code = str(item.get("f12") or "").strip()
        name = str(item.get("f14") or "").strip()
        if not code or not name or name == "-":
            continue
        change_pct = parse_float(item.get("f3"))
        main_net = parse_float(item.get("f62"))
        leader = str(item.get("f128") or item.get("f140") or "").strip()
        reason_bits = []
        if leader and leader != "-":
            reason_bits.append(f"代表股：{leader}")
        if isinstance(main_net, (int, float)):
            reason_bits.append("资金净流入" if main_net >= 0 else "资金净流出")
        rows.append({
            "code": code,
            "name": name,
            "changePct": change_pct,
            "mainNet": main_net,
            "leader": leader if leader != "-" else "",
            "reason": "，".join(reason_bits) or "按板块涨跌幅排序",
        })
    return rows


def sector_rotation_board(limit=10):
    sector_sources = [
        "m:90+s:4",
        "m:90+t:3",
    ]
    errors = []
    rows = []
    for fs in sector_sources:
        for desc in (True, False):
            try:
                payload = sector_clist_payload(fs, "f3", desc=desc)
                data = payload.get("data", {}) or {}
                rows.extend(parse_sector_rows(data.get("diff", []) or []))
            except Exception as exc:
                errors.append(str(exc))
    by_code = {}
    for row in rows:
        key = row.get("code")
        if not key:
            continue
        old = by_code.get(key)
        if not old or abs(row.get("changePct") or 0) > abs(old.get("changePct") or 0):
            by_code[key] = row
    merged = list(by_code.values())
    gainers = sorted(
        [row for row in merged if isinstance(row.get("changePct"), (int, float))],
        key=lambda row: (row.get("changePct") or 0, row.get("mainNet") or 0),
        reverse=True,
    )[:limit]
    losers = sorted(
        [row for row in merged if isinstance(row.get("changePct"), (int, float))],
        key=lambda row: (row.get("changePct") or 0, row.get("mainNet") or 0),
    )[:limit]
    return {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "东方财富行业/概念板块",
        "count": len(merged),
        "gainers": gainers,
        "losers": losers,
        "errors": errors[:6],
    }


def parse_news_rss(text, limit):
    root = ET.fromstring(text)
    items = []
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        source = ""
        for child in item:
            if child.tag.endswith("source") and child.text:
                source = child.text.strip()
                break
        if title:
            items.append({"title": title, "url": link, "time": pub_date, "source": source or "Bing News"})
        if len(items) >= limit:
            break
    return items


def is_related_news(item, name="", symbol=""):
    title = (item.get("title") or "").upper()
    clean_symbol = normalize_symbol(symbol, "cn")
    clean_name = (name or "").strip()
    if clean_name and clean_name.upper() in title:
        return True
    if clean_symbol and clean_symbol in title:
        return True
    compact_name = clean_name.replace("股份", "").replace("集团", "").replace("控股", "").replace("有限", "")
    return bool(compact_name and len(compact_name) >= 2 and compact_name.upper() in title)


def news_search(query, limit=6, name="", symbol="", market="cn"):
    clean = (query or "").strip()
    clean_name = (name or "").strip()
    clean_symbol = normalize_symbol(symbol, market) if symbol else ""
    queries = []
    if clean_name:
        queries.append(f'"{clean_name}" 股票')
        if clean_symbol:
            queries.append(f"{clean_name} {clean_symbol}")
        queries.append(f"{clean_name} 公告 业绩")
    if clean:
        queries.append(clean)
    if clean_symbol and clean_symbol not in clean:
        queries.append(f"{clean_symbol} 股票")
    if not queries:
        return []

    seen = set()
    collected = []
    for text_query in queries:
        params = urllib.parse.urlencode({"q": text_query, "format": "rss", "setmkt": "zh-CN"})
        try:
            text = get_text(f"https://www.bing.com/news/search?{params}", timeout=8)
            for item in parse_news_rss(text, limit * 2):
                key = item.get("url") or item.get("title")
                if not key or key in seen:
                    continue
                seen.add(key)
                item["related"] = is_related_news(item, clean_name, clean_symbol)
                collected.append(item)
        except Exception:
            continue
        related_count = len([item for item in collected if item.get("related")])
        if related_count >= limit:
            break

    related = [item for item in collected if item.get("related")]
    if clean_name or clean_symbol:
        if not related:
            keyword = clean_name or clean_symbol
            encoded = urllib.parse.quote(keyword)
            return [
                {
                    "title": f"未抓到直接新闻，打开东方财富资讯搜索：{keyword}",
                    "url": f"https://so.eastmoney.com/news/s?keyword={encoded}",
                    "time": "",
                    "source": "东方财富搜索",
                    "related": True,
                    "fallback": True,
                },
                {
                    "title": f"未抓到直接公告，打开巨潮公告搜索：{keyword}",
                    "url": f"https://www.cninfo.com.cn/new/fulltextSearch?notautosubmit=&keyWord={encoded}",
                    "time": "",
                    "source": "巨潮资讯搜索",
                    "related": True,
                    "fallback": True,
                },
            ][:limit]
        return related[:limit]
    return collected[:limit]


def tencent_daily(symbol, market):
    code = tencent_code(symbol, market)
    candidates = [
        f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={urllib.parse.quote(code)},day,,,800,qfq",
        f"https://web.ifzq.gtimg.cn/appstock/app/kline/kline?param={urllib.parse.quote(code)},day,,,800",
    ]
    last_error = None
    for url in candidates:
        try:
            payload = get_json(url)
            data = payload.get("data", {})
            block = data.get(code) or data.get(normalize_symbol(symbol, market)) or next(iter(data.values()), {})
            klines = block.get("qfqday") or block.get("day") or []
            rows = []
            for item in klines:
                if isinstance(item, list) and len(item) >= 3:
                    volume = float(item[5]) if len(item) > 5 and item[5] not in ("", None) else None
                    rows.append({
                        "date": item[0],
                        "open": float(item[1]),
                        "close": float(item[2]),
                        "high": float(item[3]) if len(item) > 3 and item[3] not in ("", None) else float(item[2]),
                        "low": float(item[4]) if len(item) > 4 and item[4] not in ("", None) else float(item[2]),
                        "volume": volume,
                    })
            if len(rows) >= 30:
                return rows
        except Exception as exc:
            last_error = exc
    if last_error:
        raise last_error
    raise RuntimeError("腾讯日K返回的有效行情太少")


def crypto_quote(symbol):
    code = normalize_symbol(symbol, "crypto")
    payload = get_json(f"https://api.binance.com/api/v3/ticker/24hr?{urllib.parse.urlencode({'symbol': code})}")
    return {
        "symbol": code,
        "market": "crypto",
        "name": code,
        "price": float(payload["lastPrice"]),
        "prevClose": float(payload["prevClosePrice"]) if payload.get("prevClosePrice") else None,
        "open": float(payload["openPrice"]) if payload.get("openPrice") else None,
        "change": float(payload["priceChange"]) if payload.get("priceChange") else None,
        "changePct": float(payload["priceChangePercent"]) if payload.get("priceChangePercent") else None,
        "source": "Binance",
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "rawCode": code,
    }


def fund_flow(symbol, market):
    if market != "cn":
        clean = normalize_symbol(symbol, market)
        return {
            "symbol": clean,
            "market": market,
            "name": clean,
            "source": "仅支持A股",
            "updatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
            "daily": [],
            "recent": {},
            "darkProxy": {"label": "仅支持A股", "score": 0},
        }

    code = normalize_symbol(symbol, "cn")
    errors = []
    try:
        daily_rows = eastmoney_fund_flow_daily(code)
    except Exception as exc:
        daily_rows = []
        errors.append(f"资金流日线暂时抓不到：{exc}")
    try:
        prices = eastmoney_daily(code)
    except Exception as exc:
        prices = []
        errors.append(f"行情K线暂时抓不到：{exc}")
    try:
        quote_data = tencent_quote(code, "cn")
    except Exception as exc:
        quote_data = {}
        errors.append(f"实时报价暂时抓不到：{exc}")
    latest_flow = daily_rows[-1] if daily_rows else {}
    recent_rows = daily_rows[-5:]
    five_main = sum((row.get("mainNet") or 0) for row in recent_rows)
    five_super = sum((row.get("superNet") or 0) for row in recent_rows)
    twenty_main = sum((row.get("mainNet") or 0) for row in daily_rows[-20:])

    tail_proxy = {"label": "尾盘中性", "score": 0}
    if prices:
        last = prices[-1]
        last_range = max(0.0001, (last.get("high") or 0) - (last.get("low") or 0))
        close_strength = ((last.get("close") or 0) - (last.get("low") or 0)) / last_range
        quote_price = quote_data.get("price") or last.get("close") or 0
        quote_bias = (quote_price - (last.get("close") or quote_price)) / max(0.01, last.get("close") or quote_price) * 100
        tail_score = round((close_strength - 0.5) * 120 + quote_bias * 3)
        if close_strength >= 0.72 and quote_bias >= 0.15:
            tail_proxy["label"] = "尾盘抢筹"
        elif close_strength <= 0.38 and quote_bias <= -0.15:
            tail_proxy["label"] = "尾盘抛压"
        tail_proxy["score"] = max(-100, min(100, tail_score))
        tail_proxy["closeStrength"] = close_strength
        tail_proxy["quoteBias"] = quote_bias

    main_direction = "主力流入" if five_main > 0 else "主力流出" if five_main < 0 else "主力中性"
    if five_main > 0 and five_super > 0:
        signal = "资金偏强"
    elif five_main < 0 and five_super < 0:
        signal = "资金偏弱"
    else:
        signal = "资金分歧"

    return {
        "symbol": code,
        "market": "cn",
        "name": quote_data.get("name") or code,
        "source": "东方财富资金流向 + 尾盘代理",
        "updatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "error": "；".join(errors) if errors else "",
        "latest": latest_flow,
        "recent": {
            "fiveMain": five_main,
            "fiveSuper": five_super,
            "twentyMain": twenty_main,
            "mainDirection": main_direction,
            "signal": signal,
        },
        "darkProxy": tail_proxy,
        "quote": quote_data,
        "dailyCount": len(daily_rows),
    }


def classify_money_type(change_pct, amount, volume, market_value, tail_proxy):
    institution = 0.0
    hot_money = 0.0
    reasons = []

    if isinstance(change_pct, (int, float)):
        if 0 <= change_pct <= 4.5:
            institution += 1.6
            reasons.append("涨幅不极端")
        if change_pct >= 7:
            hot_money += 2.2
            reasons.append("涨幅偏强")
        if change_pct >= 9.5:
            hot_money += 2.0
            reasons.append("接近涨停")
        if change_pct <= -3:
            hot_money += 0.8
            reasons.append("抛压明显")

    if isinstance(amount, (int, float)) and amount:
        if amount >= 300000000:
            institution += 1.4
            reasons.append("成交额较大")
        if amount >= 800000000:
            institution += 0.8
            hot_money += 0.8

    if isinstance(volume, (int, float)) and volume:
        if volume >= 100000000:
            hot_money += 0.6
        else:
            institution += 0.4

    if isinstance(market_value, (int, float)) and market_value:
        if market_value >= 50000000000:
            institution += 1.2
            reasons.append("大市值")
        elif market_value >= 15000000000:
            institution += 0.6
        else:
            hot_money += 0.5

    if tail_proxy == "尾盘抢筹":
        hot_money += 1.8
        reasons.append("尾盘抢筹")
    elif tail_proxy == "尾盘抛压":
        hot_money += 0.9
        reasons.append("尾盘抛压")
    else:
        institution += 0.2

    delta = institution - hot_money
    if delta >= 1.2:
        label = "机构偏向"
        confidence = min(95, int(round(60 + delta * 10)))
    elif delta <= -1.2:
        label = "游资偏向"
        confidence = min(95, int(round(60 + abs(delta) * 10)))
    else:
        label = "混合/中性"
        confidence = min(85, int(round(55 + abs(delta) * 8)))

    if not reasons:
        reasons.append("仅作特征推测")

    return label, confidence, "；".join(reasons[:3])


def stage_from_daily(rows):
    closes = [parse_float(row.get("close")) for row in rows]
    closes = [value for value in closes if isinstance(value, (int, float)) and value > 0]
    if len(closes) < 160:
        return {"stageNo": 0, "label": "阶段数据不足"}
    weekly = closes[-260:]
    weekly = [weekly[index] for index in range(4, len(weekly), 5)]
    if len(weekly) < 30:
        return {"stageNo": 0, "label": "阶段数据不足"}
    last = weekly[-1]
    wma30 = sum(weekly[-30:]) / 30
    previous_wma30 = sum(weekly[-34:-4]) / 30 if len(weekly) >= 34 else wma30
    slope = wma30 / previous_wma30 - 1 if previous_wma30 else 0
    high20 = max(weekly[-20:]) if len(weekly) >= 20 else last
    low20 = min(weekly[-20:]) if len(weekly) >= 20 else last
    ret13 = last / weekly[-14] - 1 if len(weekly) >= 14 else 0
    drawdown20 = last / high20 - 1 if high20 else 0
    tight_base = high20 > low20 and high20 / low20 - 1 < 0.28
    above_wma = last > wma30
    breakout = last > high20 * 1.01
    if above_wma and slope > 0.012 and (breakout or ret13 > 0.08):
        return {"stageNo": 2, "label": "第二阶段 上升"}
    if not above_wma and abs(slope) <= 0.025 and tight_base:
        return {"stageNo": 1, "label": "第一阶段 筑底"}
    if above_wma and (slope <= 0.012 or drawdown20 < -0.08):
        return {"stageNo": 3, "label": "第三阶段 筑顶"}
    if not above_wma and slope < -0.012:
        return {"stageNo": 4, "label": "第四阶段 下跌"}
    return {"stageNo": 0, "label": "过渡阶段"}


def enrich_stage(item):
    symbol = item.get("symbol") or ""
    cached = STAGE_CACHE.get(symbol)
    if cached and time.time() - cached.get("time", 0) < 300:
        return cached.get("stage") or {"stageNo": 0, "label": "阶段未识别"}
    try:
        stage = stage_from_daily(eastmoney_daily(symbol))
    except Exception:
        stage = {"stageNo": 0, "label": "阶段未识别"}
    STAGE_CACHE[symbol] = {"time": time.time(), "stage": stage}
    return stage


def market_fund_flow_board(limit=20, market="cn"):
    universe = a_share_universe(limit=10000, market=market, use_cache=False)
    rows = []
    errors = []
    for item in universe:
        symbol = item.get("symbol") or ""
        name = item.get("name") or symbol
        if not symbol:
            continue
        # 股票池已经带有最新公开行情，榜单直接复用，避免对上万只股票逐只请求腾讯接口。
        quote_data = {
            "price": parse_float(item.get("price")),
            "changePct": parse_float(item.get("changePct")),
            "source": item.get("source") or "东方财富股票池",
        }
        score = 0
        reasons = []
        change_pct = quote_data.get("changePct")
        amount = parse_float(item.get("amount"), 0) or 0
        volume = parse_float(item.get("volume"), 0) or 0
        market_value = parse_float(item.get("marketValue"), 0) or 0
        if isinstance(change_pct, (int, float)):
            score += max(-10, min(10, int(round(change_pct * 2))))
            reasons.append(f"涨跌幅 {change_pct:+.2f}%")
        if isinstance(amount, (int, float)) and amount:
            score += 2 if amount >= 100000000 else 0
            reasons.append(f"成交额 {amount}")
        if isinstance(volume, (int, float)) and volume:
            score += 1
        tail_proxy = "中性"
        if isinstance(change_pct, (int, float)):
            if change_pct >= 5:
                tail_proxy = "尾盘抢筹"
                score += 4
            elif change_pct <= -5:
                tail_proxy = "尾盘抛压"
                score -= 4
        investor_type, investor_confidence, investor_reason = classify_money_type(
            change_pct, amount, volume, market_value, tail_proxy
        )
        if isinstance(market_value, (int, float)) and market_value:
            reasons.append(f"流通市值 {market_value}")
        rows.append({
            "symbol": symbol,
            "name": name,
            "market": item.get("market") or market,
            "price": quote_data.get("price"),
            "changePct": change_pct,
            "amount": amount,
            "volume": volume,
            "score": score,
            "tailProxy": tail_proxy,
            "investorType": investor_type,
            "investorConfidence": investor_confidence,
            "investorReason": investor_reason,
            "source": quote_data.get("source") or "腾讯财经",
            "reason": "；".join(reasons[:3]),
        })
    rows.sort(key=lambda item: (item.get("score") or 0, item.get("amount") or 0, item.get("changePct") or 0), reverse=True)
    strong = rows[:limit]
    weak = sorted(rows, key=lambda item: (item.get("score") or 0, item.get("amount") or 0, item.get("changePct") or 0))[:limit]
    stage_items = {item.get("symbol"): item for item in strong + weak if item.get("symbol")}
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(enrich_stage, item): symbol for symbol, item in stage_items.items()}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                stage_items[symbol]["stage"] = future.result()
            except Exception:
                stage_items[symbol]["stage"] = {"stageNo": 0, "label": "阶段未识别"}
    return {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scope": "all" if market == "all" else "cn",
        "source": ("全市场股票池" if market == "all" else "主板股票池") + " + 腾讯财经报价",
        "count": len(rows),
        "strong": strong,
        "weak": weak,
        "errors": errors[:20],
    }


def quote(symbol, market):
    if market == "crypto":
        return crypto_quote(symbol)
    return tencent_quote(symbol, market)


def daily(symbol, market):
    if market == "cn":
        try:
            rows = eastmoney_daily(symbol)
        except Exception:
            rows = tencent_daily(symbol, market)
    elif market == "crypto":
        rows = crypto_daily(symbol)
    else:
        try:
            rows = yahoo_daily(symbol, market)
        except Exception:
            rows = stooq_daily(symbol, market)
    if len(rows) < 30:
        raise RuntimeError("有效行情太少")
    return rows


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, content_type):
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        try:
            if parsed.path in ("/", "/quant-mini-app.html"):
                self.send_file(HTML_FILE, "text/html; charset=utf-8")
            elif parsed.path == "/api/suggest":
                text = (params.get("input") or [""])[0]
                self.send_json(eastmoney_suggest(text))
            elif parsed.path == "/api/daily":
                symbol = (params.get("symbol") or [""])[0]
                market = (params.get("market") or ["cn"])[0]
                self.send_json({"data": daily(symbol, market)})
            elif parsed.path == "/api/quote":
                symbol = (params.get("symbol") or [""])[0]
                market = (params.get("market") or ["cn"])[0]
                self.send_json({"data": quote(symbol, market)})
            elif parsed.path == "/api/fundflow":
                symbol = (params.get("symbol") or [""])[0]
                market = (params.get("market") or ["cn"])[0]
                self.send_json({"data": fund_flow(symbol, market)})
            elif parsed.path == "/api/fundboard":
                limit = int((params.get("limit") or ["20"])[0] or 20)
                market = (params.get("market") or ["cn"])[0]
                if market not in ("cn", "all"):
                    market = "cn"
                self.send_json({"data": market_fund_flow_board(limit, market)})
            elif parsed.path == "/api/sector-rotation":
                limit = int((params.get("limit") or ["10"])[0] or 10)
                self.send_json({"data": sector_rotation_board(max(5, min(limit, 20)))})
            elif parsed.path == "/api/market":
                self.send_json({"data": market_overview()})
            elif parsed.path == "/api/universe":
                market = (params.get("market") or ["cn"])[0]
                limit = int((params.get("limit") or ["5000"])[0] or 5000)
                if market not in ("cn", "all"):
                    self.send_json({"data": [], "meta": {"count": 0, "source": "unsupported"}})
                else:
                    try:
                        cache_time_before = UNIVERSE_CACHE.get("time") or 0
                        rows = a_share_universe(limit, market=market)
                        cached = bool(rows) and cache_time_before == (UNIVERSE_CACHE.get("time") or 0)
                        source = "东方财富A股全市场股票池" if market == "all" else "东方财富主板股票池"
                        self.send_json({"data": rows, "meta": {"count": len(rows), "source": source, "cached": cached}})
                    except Exception as exc:
                        self.send_json({
                            "data": [],
                            "error": str(exc),
                            "meta": {"count": 0, "source": "东方财富A股全市场股票池" if market == "all" else "东方财富主板股票池", "cached": False},
                        })
            elif parsed.path == "/api/news":
                query = (params.get("q") or [""])[0]
                name = (params.get("name") or [""])[0]
                symbol = (params.get("symbol") or [""])[0]
                market = (params.get("market") or ["cn"])[0]
                limit = int((params.get("limit") or ["6"])[0] or 6)
                self.send_json({"data": news_search(query, max(1, min(limit, 10)), name, symbol, market)})
            else:
                self.send_json({"error": "not found"}, 404)
        except urllib.error.HTTPError as exc:
            self.send_json({"error": f"上游行情源返回 {exc.code}，已尝试备用源仍失败"}, 502)
        except urllib.error.URLError as exc:
            self.send_json({"error": f"无法连接上游行情源：{exc.reason}"}, 502)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 502)


def choose_port():
    cloud_port = os.environ.get("PORT")
    if cloud_port:
        try:
            return int(cloud_port)
        except ValueError:
            pass
    for port in (8765, 8787, 8899, 9000):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return 0


def main():
    port = choose_port()
    host = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"
    server = ThreadingHTTPServer((host, port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"量化小程序已启动：{url}")
    if not os.environ.get("PORT"):
        print("关闭这个窗口即可停止服务。")
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass
    server.serve_forever()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
