
import os
import json
import csv
import datetime
from pathlib import Path
from dateutil import tz
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
    return (x or "").replace("
", " ").strip()


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
    return "
".join(build_card(it) for it in items)


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

    for pack_name in enabled_packs:
        pack = cfg_news["topic_packs"][pack_name]
        rules = pack.get("rules", {})
        tag_keywords = rules.get("tag_keywords", {})

        for src in pack.get("sources", []):
            st = {"name": src.get("name"), "type": src.get("type"), "ok": True, "error": ""}
            try:
                if src["type"] == "rss":
                    feed = fetch_rss(src["url"])
                    for e in feed.entries[:50]:
                        published = safe_text(getattr(e, "published", "") or getattr(e, "updated", ""))
                        title = safe_text(getattr(e, "title", ""))
                        url = safe_text(getattr(e, "link", ""))
                        summary = safe_text(getattr(e, "summary", ""))
                        tags = tag_from_keywords(title, summary, tag_keywords)
                        all_items.append({
                            "id": f"{pack_name}:{src.get('id')}:{url}",
                            "topic_pack": pack_name,
                            "source": src.get("name"),
                            "title": title,
                            "url": url,
                            "published_at": published,
                            "summary_short": (summary[:180] if summary else title),
                            "tags": tags,
                            "importance": 3,
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
                            url = f"https://disclosure.edinet-fsa.go.jp/api/v2/documents/{doc_id}?type=2" if doc_id else ""
                            published = safe_text(r.get("submitDateTime"))
                            all_items.append({
                                "id": f"{pack_name}:{src.get('id')}:{doc_id}",
                                "topic_pack": pack_name,
                                "source": src.get("name"),
                                "title": (f"{filer} {title}" if filer else title),
                                "url": url,
                                "published_at": published,
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

    # sort by importance desc, then published_at desc (rough)
    all_items.sort(key=lambda x: (-(int(x.get("importance", 3))), x.get("published_at", "")), reverse=False)
    top_items = all_items[:30]

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

    sections_html = build_cards(top_items)
    subtitle = "NewsHub Pages Digest（公開: 一般情報のみ / B'はスイッチ）"

    index_inner = render("page_index.html", {"enabled_packs": ", ".join(enabled_packs), "sections": sections_html})
    (OUT_DIR / "index.html").write_text(wrap_base("今日 | NewsHub", subtitle, index_inner, run_at_str), encoding="utf-8")

    inv_inner = render(
        "page_investing.html",
        {
            "llm_mode": cfg_llm.get("llm", {}).get("mode", "manual_preferred"),
            "manual_stale_days": str(cfg_llm.get("llm", {}).get("manual_stale_days", 3)),
            "asset_mix_block": asset_mix_block,
            "sections": sections_html,
        },
    )
    (OUT_DIR / "investing.html").write_text(wrap_base("投資 | NewsHub", subtitle, inv_inner, run_at_str), encoding="utf-8")

    gen_inner = render("page_general.html", {"sections": ""})
    (OUT_DIR / "general.html").write_text(wrap_base("一般 | NewsHub", subtitle, gen_inner, run_at_str), encoding="utf-8")

    # append to csv log (dedupe)
    log_path = DATA_DIR / "news_log.csv"
    existing_ids = set()
    if log_path.exists():
        with log_path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                existing_ids.add(row.get("id", ""))

    with log_path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        for it in top_items:
            if it["id"] in existing_ids:
                continue
            writer.writerow(
                [
                    it["id"],
                    it["topic_pack"],
                    it["source"],
                    it["title"],
                    it["url"],
                    it["published_at"],
                    it["summary_short"],
                    " ".join(it["tags"]),
                    it["importance"],
                    it["impact"],
                    it["llm_mode"],
                    it["llm_draft"],
                    it["llm_confidence"],
                ]
            )

    # log page
    rows_html = ""
    for it in top_items[:50]:
        rows_html += (
            "<div class='card'>"
            f"<div class='meta'><span class='badge'>{it.get('topic_pack')}</span><span class='badge'>{safe_text(it.get('published_at'))}</span></div>"
            f"<div class='title'><a class='link' href='{it.get('url')}' target='_blank' rel='noopener'>{safe_text(it.get('title'))}</a></div>"
            f"<div class='summary'>{safe_text(it.get('summary_short'))}</div>"
            "</div>"
        )

    log_inner = render("page_log.html", {"rows": rows_html})
    (OUT_DIR / "log.html").write_text(wrap_base("ログ | NewsHub", subtitle, log_inner, run_at_str), encoding="utf-8")

    # settings page
    sources_html = ""
    for s in sources_status_lines:
        status = "OK" if s.get("ok") else "NG"
        err = safe_text(s.get("error"))
        sources_html += f"<div class='summary'>[{status}] {safe_text(s.get('name'))} ({safe_text(s.get('type'))}){(' - ' + err) if err else ''}</div>"

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
