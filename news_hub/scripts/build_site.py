import os
import json
import csv
import datetime
import time
import re
from pathlib import Path
from dateutil import tz
from dateutil import parser as dateparser
import requests
import feedparser
import yaml

ROOT = Path(__file__).resolve().parents[2]
NEWS_DIR = ROOT / "news_hub"
CONFIG_DIR = NEWS_DIR / "config"
DATA_DIR = NEWS_DIR / "data"
OUT_DIR = NEWS_DIR / "outputs" / "site"
TEMPLATE_DIR = NEWS_DIR / "templates"

JST = tz.gettz("Asia/Tokyo")

def load_yaml(p: Path):
    with p.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def now_jst():
    return datetime.datetime.now(tz=JST)

def safe_text(x):
    return (x or "").replace("\n", " ").strip()

def fetch_rss(url: str, timeout=20):
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return feedparser.parse(r.text)

def fetch_edinet_daily(endpoint: str, api_key: str, date_str: str):
    params = {"date": date_str, "type": 2, "Subscription-Key": api_key}
    r = requests.get(endpoint, params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def tag_from_keywords(title: str, summary: str, tag_keywords: dict):
    text = (title + " " + summary)
    tags = []
    for tag, kws in (tag_keywords or {}).items():
        for kw in kws:
            if kw in text:
                tags.append(tag)
                break
    return tags

def entry_timestamp(e) -> int:
    """feedparser entry から日時を数値化（新しい順ソート用）"""
    t = getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
    if t:
        try:
            return int(time.mktime(t))
        except Exception:
            pass
    return 0

def parse_timestamp_fallback(published_str: str, now_dt: datetime.datetime) -> int:
    """published_at の文字列から timestamp を推定（RSSがparsed日時を持たない場合の救済）。

    主に以下のパターンに対応：
    - '11/09 17:49'（年が無い） -> 現在年を基本に、未来日付になる場合は前年に補正
    - ISO/一般パース可能な文字列 -> dateutilでパース
    """
    s = (published_str or "").strip()
    if not s:
        return 0

    m = re.search(r"(?P<m>\d{1,2})/(?P<d>\d{1,2})\s+(?P<h>\d{1,2}):(?P<mi>\d{2})", s)
    if m:
        mon = int(m.group('m'))
        day = int(m.group('d'))
        hh = int(m.group('h'))
        mi = int(m.group('mi'))
        year = now_dt.year
        dt = datetime.datetime(year, mon, day, hh, mi, tzinfo=JST)
        # 未来日付に見える場合は前年に倒す（年跨ぎ対策）
        if dt > now_dt + datetime.timedelta(days=1):
            dt = datetime.datetime(year - 1, mon, day, hh, mi, tzinfo=JST)
        return int(dt.timestamp())

    try:
        dt = dateparser.parse(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return int(dt.timestamp())
    except Exception:
        return 0

def iso_timestamp(s: str) -> int:
    """EDINET submitDateTime (ISO風) を数値化"""
    if not s:
        return 0
    try:
        dt = dateparser.parse(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return int(dt.timestamp())
    except Exception:
        return 0

def render(template_name: str, ctx: dict):
    tpl = (TEMPLATE_DIR / template_name).read_text(encoding="utf-8")
    for k, v in ctx.items():
        tpl = tpl.replace("{{ " + k + " }}", str(v))
    return tpl

def wrap_base(title: str, subtitle: str, content_html: str, generated_at: str):
    base = (TEMPLATE_DIR / "base.html").read_text(encoding="utf-8")
    base = base.replace("{{ title }}", title)
    base = base.replace("{{ subtitle }}", subtitle)
    base = base.replace("{{ content }}", content_html)
    base = base.replace("{{ generated_at }}", generated_at)
    return base

def build_card(it: dict) -> str:
    tags = ", ".join(it.get("tags", []))
    llm_badge = "<span class='badge'>draft</span>" if it.get("llm_draft") else ""
    return (
        "<div class='card'>"
        "<div class='meta'>"
        f"<span class='badge imp'>重要度 {it.get('importance', 3)}</span>"
        f"<span class='badge'>{safe_text(it.get('source'))}</span>"
        f"<span class='badge'>{safe_text(it.get('published_at'))}</span>"
        f"{llm_badge}"
        "</div>"
        f"<div class='title'><a class='link' href='{it.get('url')}' target='_blank' rel='noopener'>{safe_text(it.get('title'))}</a></div>"
        f"<div class='summary'>{safe_text(it.get('summary_short'))}</div>"
        f"<div class='meta'><span class='badge'>{safe_text(tags)}</span></div>"
        "</div>"
    )

def build_cards(items):
    return "\n".join(build_card(it) for it in items)

def dedupe_by_url(items):
    """同一URLの重複除去"""
    seen = set()
    out = []
    for it in items:
        u = it.get("url", "")
        if not u:
            continue
        if u in seen:
            continue
        seen.add(u)
        out.append(it)
    return out

def sort_newest(items):
    """重要度が高い→新しい（published_tsが大きい）順"""
    return sorted(
        items,
        key=lambda x: (-(int(x.get("importance", 3))), -(int(x.get("published_ts", 0)))),
        reverse=False,
    )

def clean_title_for_digest(title: str) -> str:
    t = (title or "").strip()
    # 先頭の [xxx] を落とす
    t = re.sub(r"^\[[^\]]+\]\s*", "", t)
    # 余計な空白
    t = re.sub(r"\s+", " ", t)
    return t

def make_digest_block(label: str, items: list, max_lines: int = 6) -> str:
    # 最新（published_ts）順の上位を使う
    sorted_items = sorted(items, key=lambda x: int(x.get('published_ts', 0)), reverse=True)
    lines = []
    for it in sorted_items:
        title = clean_title_for_digest(it.get('title',''))
        if not title:
            continue
        # 重複っぽいものを避ける
        if title in lines:
            continue
        # 長すぎるのは省略
        if len(title) > 80:
            title = title[:77] + '…'
        lines.append(title)
        if len(lines) >= max_lines:
            break

    if not lines:
        return (
            "<div class='card'>"
            f"<div class='meta'><span class='badge imp'>今日の要約（{label}）</span></div>"
            "<div class='summary'>（該当ニュースなし）</div>"
            "</div>"
        )

    lis = "".join([f"<li>{safe_text(x)}</li>" for x in lines])
    return (
        "<div class='card'>"
        f"<div class='meta'><span class='badge imp'>今日の要約（{label}）</span></div>"
        "<div class='summary'><ul style='margin:6px 0 0 18px;padding:0'>"
        f"{lis}"
        "</ul></div>"
        "</div>"
    )

def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    cfg_news = load_yaml(CONFIG_DIR / "news.yaml")
    cfg_llm = load_yaml(CONFIG_DIR / "llm.yaml")
    cfg_pub = load_yaml(CONFIG_DIR / "public.yaml")

    enabled_packs = [k for k, v in cfg_news.get("topic_packs", {}).items() if v.get("enabled")]

    state_path = DATA_DIR / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}

    run_at = now_jst()
    run_at_str = run_at.strftime("%Y-%m-%d %H:%M:%S %Z")

    all_items = []
    sources_status_lines = []

    # 収集
    for pack_name in enabled_packs:
        pack = cfg_news["topic_packs"][pack_name]
        rules = pack.get("rules", {})

        deny_keywords = rules.get("deny_keywords", []) or []
        allow_keywords = rules.get("allow_keywords", []) or []

        for src in pack.get("sources", []):
            st = {"name": src.get("name"), "type": src.get("type"), "ok": True, "error": ""}
            try:
                if src["type"] == "rss":
                    base_importance = int(src.get("base_importance", 3))
                    feed = fetch_rss(src["url"])
                    for e in feed.entries[:50]:
                        published = safe_text(getattr(e, "published", "") or getattr(e, "updated", ""))
                        title = safe_text(getattr(e, "title", ""))
                        url = safe_text(getattr(e, "link", ""))
                        summary = safe_text(getattr(e, "summary", ""))

                        joined = title + " " + summary
                        allow_hit = any(k in joined for k in allow_keywords)
                        deny_hit = any(k in joined for k in deny_keywords)
                        if deny_hit and not allow_hit:
                            continue

                        published_ts = entry_timestamp(e)
                        if published_ts == 0:
                            published_ts = parse_timestamp_fallback(published, run_at)

                        tags = tag_from_keywords(title, summary, rules.get("tag_keywords", {}))

                        all_items.append({
                            "id": f"{pack_name}:{src.get('id')}:{url}",
                            "topic_pack": pack_name,
                            "source": src.get("name"),
                            "title": title,
                            "url": url,
                            "published_at": published,
                            "published_ts": published_ts,
                            "summary_short": (summary[:180] if summary else title),
                            "tags": tags,
                            "importance": base_importance,
                            "impact": "unclear",
                            "llm_mode": cfg_llm.get("llm", {}).get("mode", "manual_preferred"),
                            "llm_draft": False,
                            "llm_confidence": "low",
                        })

                elif src["type"] == "edinet":
                    api_key = os.getenv("EDINET_API_KEY", "").strip()
                    if not api_key:
                        st["ok"] = False
                        st["error"] = "EDINET_API_KEY not set (skipped)"
                    else:
                        date_str = run_at.strftime("%Y-%m-%d")
                        js = fetch_edinet_daily(src["endpoint"], api_key, date_str)
                        include_codes = set(src.get("include_doc_type_codes", []))
                        for r in js.get("results", [])[:400]:
                            doc_type = str(r.get("docTypeCode") or "")
                            if include_codes and doc_type and doc_type not in include_codes:
                                continue
                            title = safe_text(r.get("docDescription"))
                            filer = safe_text(r.get("filerName"))
                            sec = safe_text(r.get("secCode"))
                            doc_id = safe_text(r.get("docID"))
                            published = safe_text(r.get("submitDateTime"))
                            published_ts = iso_timestamp(published)
                            url = f"https://disclosure.edinet-fsa.go.jp/api/v2/documents/{doc_id}?type=2" if doc_id else ""

                            all_items.append({
                                "id": f"{pack_name}:{src.get('id')}:{doc_id}",
                                "topic_pack": pack_name,
                                "source": src.get("name"),
                                "title": (f"{filer} {title}" if filer else title),
                                "url": url,
                                "published_at": published,
                                "published_ts": published_ts,
                                "summary_short": (f"EDINET提出: {title} / 証券コード: {sec}" if sec else f"EDINET提出: {title}"),
                                "tags": ["法定開示"],
                                "importance": 4,
                                "impact": "unclear",
                                "llm_mode": cfg_llm.get("llm", {}).get("mode", "manual_preferred"),
                                "llm_draft": True,
                                "llm_confidence": "low",
                            })
                else:
                    st["ok"] = False
                    st["error"] = f"Unknown source type: {src['type']}"

            except Exception as e:
                st["ok"] = False
                st["error"] = str(e)

            sources_status_lines.append(st)

    items_by_pack = {}
    for it in all_items:
        items_by_pack.setdefault(it.get("topic_pack", "unknown"), []).append(it)

    inv_items = sort_newest(dedupe_by_url(items_by_pack.get("investing_jp", [])))
    gen_items = sort_newest(dedupe_by_url(items_by_pack.get("world_general", [])))

    # Index: 要約（投資/一般） + ハイライト
    digest_investing = make_digest_block("投資", inv_items)
    digest_general = make_digest_block("一般", gen_items)

    index_sections = digest_investing + digest_general + build_cards(inv_items[:10] + gen_items[:10])
    inv_sections = build_cards(inv_items[:30])
    gen_sections = build_cards(gen_items[:50])

    # B'（資産クラス比率）
    pub = cfg_pub.get("public_site", {})
    asset_mix_block = ""
    if pub.get("show_asset_mix"):
        mix = pub.get("asset_mix") or {}
        step = float(pub.get("asset_mix_rounding", 0.05))

        def rnd(v):
            return round(round(float(v) / step) * step, 4)

        if mix:
            parts = []
            for k in ["equity", "bond", "reit", "cash"]:
                if k in mix:
                    parts.append(f"<span class='badge'>{k}: {int(rnd(mix[k]) * 100)}%</span>")
            asset_mix_block = (
                "<div class='card'><div class='meta'><span class='badge imp'>資産クラス比率（B'）</span></div>"
                "<div class='meta'>" + "".join(parts) + "</div></div>"
            )
        else:
            asset_mix_block = (
                "<div class='card'><div class='meta'><span class='badge imp'>資産クラス比率（B'）</span></div>"
                "<div class='summary'>public.yaml の asset_mix に equity/bond/reit/cash を設定してください</div></div>"
            )

    subtitle = "NewsHub Pages Digest（公開: 一般情報のみ / B'はスイッチ）"

    # index
    index_inner = render("page_index.html", {"enabled_packs": ", ".join(enabled_packs), "sections": index_sections})
    (OUT_DIR / "index.html").write_text(wrap_base("今日 | NewsHub", subtitle, index_inner, run_at_str), encoding="utf-8")

    # investing
    inv_inner = render(
        "page_investing.html",
        {
            "llm_mode": cfg_llm.get("llm", {}).get("mode", "manual_preferred"),
            "manual_stale_days": str(cfg_llm.get("llm", {}).get("manual_stale_days", 3)),
            "asset_mix_block": asset_mix_block,
            "sections": inv_sections,
        },
    )
    (OUT_DIR / "investing.html").write_text(wrap_base("投資 | NewsHub", subtitle, inv_inner, run_at_str), encoding="utf-8")

    # general
    gen_inner = render("page_general.html", {"sections": gen_sections})
    (OUT_DIR / "general.html").write_text(wrap_base("一般 | NewsHub", subtitle, gen_inner, run_at_str), encoding="utf-8")

    # log
    all_sorted = sort_newest(dedupe_by_url(all_items))
    log_path = DATA_DIR / "news_log.csv"
    existing_ids = set()
    if log_path.exists():
        with log_path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                existing_ids.add(row.get("id", ""))

    with log_path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        for it in all_sorted[:80]:
            if it["id"] in existing_ids:
                continue
            writer.writerow([
                it["id"], it["topic_pack"], it["source"], it["title"], it["url"], it["published_at"],
                it["summary_short"], " ".join(it["tags"]), it["importance"], it["impact"],
                it["llm_mode"], it["llm_draft"], it["llm_confidence"],
            ])

    # log page
    rows_html = ""
    for it in all_sorted[:80]:
        rows_html += (
            "<div class='card'>"
            f"<div class='meta'><span class='badge'>{it.get('topic_pack')}</span><span class='badge'>{safe_text(it.get('published_at'))}</span></div>"
            f"<div class='title'><a class='link' href='{it.get('url')}' target='_blank' rel='noopener'>{safe_text(it.get('title'))}</a></div>"
            f"<div class='summary'>{safe_text(it.get('summary_short'))}</div>"
            "</div>"
        )

    log_inner = render("page_log.html", {"rows": rows_html})
    (OUT_DIR / "log.html").write_text(wrap_base("ログ | NewsHub", subtitle, log_inner, run_at_str), encoding="utf-8")

    # settings: count + newest info for debugging
    def newest_info(items):
        if not items:
            return "-"
        it = max(items, key=lambda x: int(x.get('published_ts', 0)))
        return f"{safe_text(it.get('published_at'))} / {safe_text(it.get('title'))[:60]}"

    sources_html = ""
    for s in sources_status_lines:
        status = "OK" if s.get("ok") else "NG"
        err = safe_text(s.get("error"))
        sources_html += f"<div class='summary'>[{status}] {safe_text(s.get('name'))} ({safe_text(s.get('type'))}){(' - ' + err) if err else ''}</div>"

    sources_html += "<div class='summary' style='margin-top:10px'><b>統計</b></div>"
    sources_html += f"<div class='summary'>investing_jp 件数: {len(inv_items)} / 最新: {safe_text(newest_info(inv_items))}</div>"
    sources_html += f"<div class='summary'>world_general 件数: {len(gen_items)} / 最新: {safe_text(newest_info(gen_items))}</div>"

    st_inner = render(
        "page_settings.html",
        {
            "last_run": run_at_str,
            "last_success": run_at_str,
            "show_asset_mix": str(pub.get("show_asset_mix")),
            "sources_status": sources_html,
        },
    )
    (OUT_DIR / "settings.html").write_text(wrap_base("設定 | NewsHub", subtitle, st_inner, run_at_str), encoding="utf-8")

    # update state
    state["last_run"] = run_at_str
    state["last_success"] = run_at_str
    (DATA_DIR / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
