#!/usr/bin/env python3
"""GitHub 프로필 카드(assets/card.svg)를 이 맥에서 만든다. GitHub Actions 없이 launchd가 돌린다.

데이터
- 기여 잔디·공개 레포 언어·머지된 OSS PR: `gh` CLI (로그인한 사용자 토큰)
- AI 토큰: 로컬 로그(omp, Claude Code)를 날짜별로 모아 ledger에 쌓는다. 로그가 지워져도 줄지 않게 날짜별 최댓값만 남긴다.
  2026-10-05 이전은 tokscale 캐시 스냅샷(Codex 포함, 지금은 로그가 없음)을 한 번 시드로 넣는다.
"""
import glob
import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "card.svg"
HOME = Path.home()
LEDGER = HOME / "Library" / "Application Support" / "profile-card" / "ledger.json"
TOKSCALE = HOME / ".config" / "tokscale" / "cache" / "tui-data-cache.json"
SEED_UNTIL = "2026-10-05"  # tokscale 스냅샷 마지막 날. 이후는 로컬 로그로 센다.
GH = next((p for p in ("/opt/homebrew/bin/gh", "/usr/local/bin/gh") if os.path.exists(p)), "gh")
EXCLUDE_ORGS = ("revenuemarket",)  # 회사 비공개 조직: OSS 집계에서 뺀다
SUBTITLE = "Product Engineer · React · Rust · Kotlin · Go"


def gh(*args):
    return json.loads(subprocess.run([GH, *args], check=True, capture_output=True, text=True).stdout)


def gql(query):
    return gh("api", "graphql", "-f", f"query={query}")["data"]


# ---------- GitHub ----------
def github_data():
    v = gql("""query{viewer{login name createdAt
      contributionsCollection{contributionCalendar{totalContributions weeks{contributionDays{contributionCount}}}}
      repositories(ownerAffiliations:OWNER,isFork:false,first:100,privacy:PUBLIC){nodes{languages(first:10){edges{size node{name color}}}}}}}""")["viewer"]
    cal = v["contributionsCollection"]["contributionCalendar"]
    langs = {}
    for repo in v["repositories"]["nodes"]:
        for e in repo["languages"]["edges"]:
            name = e["node"]["name"]
            langs.setdefault(name, [e["node"]["color"] or "#8b949e", 0])[1] += e["size"]
    q = f"author:{v['login']} is:pr is:merged is:public -user:{v['login']} " + " ".join(f"-org:{o}" for o in EXCLUDE_ORGS)
    total, counts, page = None, {}, 1
    while True:
        r = gh("api", "-X", "GET", "search/issues", "-f", f"q={q}", "-f", "per_page=100", "-f", f"page={page}")
        total = r["total_count"]
        for it in r["items"]:
            name = it["repository_url"].split("/repos/")[1]
            counts[name] = counts.get(name, 0) + 1
        if len(r["items"]) < 100 or page >= 10:
            break
        page += 1
    stars = {n: gh("api", f"repos/{n}")["stargazers_count"] for n in counts}
    oss = sorted(((n, stars[n], c) for n, c in counts.items()), key=lambda x: -x[1])[:5]
    return {
        "name": v["name"] or v["login"],
        "since_year": int(v["createdAt"][:4]),
        "weeks": [[d["contributionCount"] for d in w["contributionDays"]] for w in cal["weeks"]],
        "total": cal["totalContributions"],
        "langs": sorted(((n, c, s) for n, (c, s) in langs.items()), key=lambda x: -x[2])[:5],
        "oss": oss,
        "oss_total": total,
    }


# ---------- tokens ----------
def day_of(ts):
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts / 1000, timezone.utc).strftime("%Y-%m-%d")
    return str(ts)[:10]


def scan_omp():
    days, seen = {}, set()
    for p in glob.glob(str(HOME / ".omp/agent/sessions/**/*.jsonl"), recursive=True):
        with open(p, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if '"totalTokens"' not in line:
                    continue
                try:
                    x = json.loads(line)
                except ValueError:
                    continue
                m = x.get("message") if x.get("type") == "message" else x
                u = (m or {}).get("usage") or {}
                key = (m or {}).get("responseId") or x.get("id")
                if not u or key in seen:
                    continue
                seen.add(key)
                d = day_of(x.get("timestamp") or (m or {}).get("timestamp"))
                days[d] = days.get(d, 0) + int(u.get("totalTokens") or 0)
    return days


def scan_claude():
    days, seen = {}, set()
    for p in glob.glob(str(HOME / ".claude/projects/**/*.jsonl"), recursive=True):
        with open(p, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if '"usage"' not in line:
                    continue
                try:
                    x = json.loads(line)
                except ValueError:
                    continue
                m = x.get("message")
                u = m.get("usage") if isinstance(m, dict) else None
                key = (m.get("id"), x.get("requestId")) if u else None
                if not u or key in seen:
                    continue
                seen.add(key)
                d = day_of(x.get("timestamp"))
                days[d] = days.get(d, 0) + sum(int(u.get(f) or 0) for f in
                                               ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    return days


def tokscale_seed():
    if not TOKSCALE.exists():
        return {}
    data = json.load(open(TOKSCALE))["data"]
    return {x["date"]: sum(int(x["tokens"].get(k) or 0) for k in ("input", "output", "cacheRead", "cacheWrite", "reasoning"))
            for x in data["daily"] if x["date"] <= SEED_UNTIL}


def tokens():
    ledger = json.load(open(LEDGER)) if LEDGER.exists() else {}
    if "tokscale" not in ledger:
        ledger["tokscale"] = tokscale_seed()
    for src, days in (("omp", scan_omp()), ("claude", scan_claude())):
        book = ledger.setdefault(src, {})
        for d, n in days.items():
            if d > SEED_UNTIL:  # 이전 날짜는 tokscale 시드가 이미 셌다
                book[d] = max(book.get(d, 0), n)
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER.with_suffix(".tmp")
    json.dump(ledger, open(tmp, "w"), indent=0, sort_keys=True)
    tmp.replace(LEDGER)
    by_src = {s: sum(v.values()) for s, v in ledger.items()}
    first = min((d for v in ledger.values() for d, n in v.items() if n), default=date.today().isoformat())
    return {"total": sum(by_src.values()), "by_src": by_src, "since": first}


# ---------- render (design B · bento) ----------
SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI','Pretendard',Helvetica,Arial,sans-serif"
HEAT = ["#1f1f23", "#3b2f6b", "#5b45b5", "#8b6cff", "#c4b5ff"]


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def human(n):
    for v, s in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= v:
            return f"{n / v:.1f}".rstrip("0").rstrip(".") + s
    return str(n)


def text(x, y, s, size=13, fill="#a1a1aa", w=500, anchor="start"):
    return f'<text x="{x}" y="{y}" font-family="{SANS}" font-size="{size}" font-weight="{w}" fill="{fill}" text-anchor="{anchor}">{esc(s)}</text>'


def tile(x, y, w, h):
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="#121214" stroke="#27272a"/>'


def render(g, t):
    flat = sorted(c for w in g["weeks"] for c in w)
    cap = flat[int(len(flat) * 0.97)] or 1  # 상위 3%는 최고 단계로 묶어 잔디가 고르게 보이게
    lvl = lambda c: 0 if c == 0 else min(4, 1 + int(4 * c / (cap + 1)))
    years = date.today().year - g["since_year"]
    b = [
        '<defs><linearGradient id="gl" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#8b6cff"/>'
        '<stop offset="1" stop-color="#22d3ee"/></linearGradient></defs>',
        '<rect width="880" height="440" rx="20" fill="#09090b"/>',
        tile(16, 16, 560, 200), text(36, 50, g["name"], 26, "#fafafa", 700),
        text(36, 74, f"{SUBTITLE} · {years} years on GitHub", 13),
        text(36, 110, "CONTRIBUTIONS · LAST 12 MONTHS", 10, "#71717a", 600), text(556, 110, f"{g['total']:,}", 13, "#fafafa", 700, "end"),
    ]
    for i, w in enumerate(g["weeks"]):
        for j, c in enumerate(w):
            b.append(f'<rect x="{36 + i * 10:.1f}" y="{120 + j * 10:.1f}" width="8.4" height="8.4" rx="2" fill="{HEAT[lvl(c)]}"/>')
    src = t["by_src"]
    detail = f"omp {human(src.get('omp', 0))} · Claude Code {human(src.get('claude', 0))} · earlier {human(src.get('tokscale', 0))}"
    b += [
        tile(592, 16, 272, 200), text(612, 50, "AI TOKENS BURNED", 10, "#71717a", 600),
        f'<text x="612" y="128" font-family="{SANS}" font-size="64" font-weight="800" fill="url(#gl)">{human(t["total"])}</text>',
        text(612, 156, detail, 11), text(612, 196, f"since {t['since']}", 11, "#52525b"),
        tile(16, 232, 400, 192), text(36, 264, "MERGED OPEN SOURCE", 10, "#71717a", 600),
        text(396, 264, f"{g['oss_total']} PRs", 13, "#fafafa", 700, "end"),
    ]
    y = 296
    for repo, stars, n in g["oss"]:
        b += [text(36, y, repo, 13, "#e4e4e7", 600), text(320, y, f"★ {human(stars)}", 12, "#a1a1aa", 500, "end"),
              text(396, y, n, 13, "#fafafa", 700, "end")]
        y += 26
    tot = sum(s for _, _, s in g["langs"]) or 1
    b += [tile(432, 232, 432, 192), text(452, 264, "TOP LANGUAGES", 10, "#71717a", 600),
          '<clipPath id="lb"><rect x="452" y="280" width="392" height="10" rx="5"/></clipPath><g clip-path="url(#lb)">']
    cx = 452.0
    for _, col, s in g["langs"]:
        b.append(f'<rect x="{cx:.1f}" y="280" width="{392 * s / tot + 0.5:.1f}" height="10" fill="{col}"/>')
        cx += 392 * s / tot
    b.append("</g>")
    y = 320
    for name, col, s in g["langs"]:
        b += [f'<circle cx="458" cy="{y - 4}" r="5" fill="{col}"/>', text(472, y, name, 13, "#e4e4e7"),
              text(844, y, f"{100 * s / tot:.0f}%", 13, "#a1a1aa", 600, "end")]
        y += 22
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="880" height="440" viewBox="0 0 880 440">{"".join(b)}</svg>\n'


def main():
    g, t = github_data(), tokens()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    svg = render(g, t)
    old = OUT.read_text() if OUT.exists() else ""
    OUT.write_text(svg)
    print(f"tokens {t['total']:,} since {t['since']} · contributions {g['total']:,} · oss {g['oss_total']} · {'changed' if svg != old else 'same'}")


if __name__ == "__main__":
    sys.exit(main())
