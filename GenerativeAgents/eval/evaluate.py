"""Evaluate placelab runs: blind human ratings and an LLM judge on the same rubric, by memory condition.

    python eval/evaluate.py sheets --runs 2,3,4      # one blind rating sheet per person, from saved runs
    python eval/evaluate.py judge                    # Gemini rates the same items with the same rubric
    python eval/evaluate.py report                   # human vs LLM, by memory condition -> report.md

Runs come from data/runs/ (saved by serve.py when a run ends); each records its memory condition
(full, limited, none: the "memory" selector in the page). Everything this script writes goes to
data/eval/<set>/ (git-ignored with data/), because the sheets contain your conversations.
Standard library only; the judge uses serve.py's Gemini call and GEMINI_API_KEY from .env.
"""
import argparse
import csv
import json
import pathlib
import random
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import serve  # noqa: E402  (loads .env; gemini(), prompt(), data folders)

PEOPLE = ROOT / "people"
CRITERIA = [  # Maya's rubric, 1 (very low) to 5 (very high)
    ("represents_me", "my agent represented me and my interests well"),
    ("novelty", "the insight or idea is new to me"),
    ("usefulness", "it is useful for my work"),
    ("relevance", "it is relevant to my project and interests"),
    ("follow_up", "I would follow up with this classmate about it"),
]
KEYS = [k for k, _ in CRITERIA]
CONDITIONS = ["full", "limited", "none"]


def set_dir(name):
    return serve.DATA / "eval" / name


def load_people():
    out = {}
    for f in sorted(PEOPLE.glob("*.json")):
        try:
            p = json.loads(f.read_text(encoding="utf-8"))
            out[p["code"]] = p
        except (ValueError, KeyError):
            print(f"  skipping {f.name}: not a valid people file")
    return out


def load_runs(ids):
    runs = []
    for n in serve.run_ids():
        if ids and n not in ids:
            continue
        rec = json.loads((serve.RUNS / f"run-{n}.json").read_text(encoding="utf-8"))
        rec["_n"] = n
        runs.append(rec)
    return runs


def conversations(rec):
    """Each conversation of a run with its lines, insight and idea (chart.convs carries the lines)."""
    chart = rec.get("chart") or {}
    code = {a["id"]: a["code"] for a in chart.get("agents", [])}
    meta = {(c["day"], c["clk"], c["a"], c["b"]): c for c in rec.get("convs", [])}
    ideas = {i["id"]: i for i in rec.get("ideas", [])}
    for cc in chart.get("convs", []):
        a, b = code.get(cc["a"]), code.get(cc["b"])
        rc = meta.get((cc["day"], cc["clk"], a, b), {})
        idea = ideas.get(rc.get("idea") or cc.get("idea"))
        yield {
            "run": rec["_n"], "memory": rec.get("memory", "unknown"), "conv": rc.get("id", cc["id"]),
            "day": cc["day"], "clk": cc["clk"], "place": cc.get("place", ""), "a": a, "b": b,
            "topics": rc.get("topics") or {}, "insight": rc.get("insight", ""), "outcome": rc.get("out", cc.get("out")),
            "followup": bool(rc.get("followup")),
            "idea": {"title": idea["title"], "pitch": idea.get("latest") or idea.get("pitch", "")} if idea else None,
            "lines": [{"who": code.get(l["w"]), "text": l["t"]} for l in cc.get("lines", [])],
        }


def describe(p, full):
    """What a person's own file says, for the judge."""
    out = {"project": p.get("project", ""), "skills": p.get("skills", [])}
    out["interests"] = [i if isinstance(i, str) else (f"{i['term']}: {i.get('description', '')}" if full else i["term"])
                        for i in p.get("interests", [])]
    if full:
        out["bio"] = p.get("bio", "")
        out["weekly_builds"] = [f"{w.get('week')} {w.get('title')}: {w.get('summary', '')}" for w in p.get("weekly_builds", [])]
    return out


# ---------- sheets ----------
def cmd_sheets(args):
    people = load_people()
    ids = {int(x) for x in args.runs.split(",")} if args.runs else None
    runs = load_runs(ids)
    if not runs:
        sys.exit("no saved runs in data/runs/ (finish a run with serve.py running, or check --runs)")
    rng = random.Random(args.seed)
    out = set_dir(args.set)
    out.mkdir(parents=True, exist_ok=True)
    key, per_owner = {}, {}
    for rec in runs:
        for c in conversations(rec):
            for owner, partner in ((c["a"], c["b"]), (c["b"], c["a"])):
                if owner in people and partner:
                    per_owner.setdefault(owner, {}).setdefault(c["memory"], []).append((c, partner))
    for owner, by_cond in per_owner.items():
        items = []
        for cond, lst in by_cond.items():   # at most --per-person conversations per condition, chosen at random
            items += rng.sample(lst, min(args.per_person, len(lst)))
        rng.shuffle(items)                   # blind: conditions and runs are mixed
        pname = lambda code: people[code]["name"] if code in people else code
        rows, md = [], [f"# Rating sheet for {people[owner]['name']} ({owner})", "",
                        "Rate each conversation your agent had, 1 (very low) to 5 (very high), in "
                        f"ratings_{owner}.csv. The memory condition is hidden on purpose.", "",
                        *[f"- **{k}**: {d}" for k, d in CRITERIA], ""]
        for n, (c, partner) in enumerate(items, 1):
            iid = f"{owner}-{n:02d}"
            mine = c["topics"].get("a" if c["a"] == owner else "b", "")
            theirs = c["topics"].get("b" if c["a"] == owner else "a", "")
            talk = [f"{'your agent' if l['who'] == owner else pname(partner) + chr(8217) + 's agent'}: {l['text']}" for l in c["lines"]]
            idea = f"{c['idea']['title']}: {c['idea']['pitch']}" if c["idea"] else "none"
            key[iid] = {"owner": owner, "partner": partner, "run": c["run"], "memory": c["memory"], "conv": c["conv"],
                        "you_brought": mine, "they_brought": theirs, "lines": talk, "insight": c["insight"], "idea": idea}
            rows.append([iid, pname(partner), mine, theirs, "\n".join(talk), c["insight"], idea, *[""] * len(KEYS), ""])
            md += [f"## {iid} · with {pname(partner)}", "", f"You brought: {mine}  ", f"They brought: {theirs}", "",
                   *[f"> {t}  " for t in talk], "", f"**Insight:** {c['insight']}  ", f"**Idea:** {idea}", ""]
        with (out / f"ratings_{owner}.csv").open("w", newline="", encoding="utf-8-sig") as f:   # utf-8-sig: Excel reads it
            w = csv.writer(f)
            w.writerow(["item", "partner", "you_brought", "they_brought", "conversation", "insight", "idea", *KEYS, "comment"])
            w.writerows(rows)
        (out / f"ratings_{owner}.md").write_text("\n".join(md) + "\n", encoding="utf-8")
        print(f"  {owner}: {len(items)} conversations -> ratings_{owner}.csv (+ .md to read them)")
    (out / "key.json").write_text(json.dumps({"runs": [r["_n"] for r in runs], "items": key}, ensure_ascii=False, indent=1), encoding="utf-8")
    conds = {r["_n"]: r.get("memory", "unknown") for r in runs}
    print(f"{len(key)} items from runs {conds} -> {out}")
    print("Send each person their ratings_<code>.csv; put the filled files back in that folder, then run judge and report.")


# ---------- judge ----------
JUDGE_SCHEMA = {"type": "OBJECT", "properties": {
    **{k: {"type": "INTEGER"} for k in KEYS},
    "reasons": {"type": "OBJECT", "properties": {k: {"type": "STRING"} for k in KEYS}, "required": KEYS}},
    "required": [*KEYS, "reasons"]}


def judge_one(item, people, system):
    owner, partner = item["owner"], item["partner"]
    user = {"owner": describe(people[owner], True),
            "partner": describe(people[partner], False) if partner in people else {"project": "(not in people/)"},
            "owner_agent_brought": item["you_brought"], "partner_agent_brought": item["they_brought"],
            "conversation": [l.replace("your agent:", "OWNER'S AGENT:", 1) if l.startswith("your agent:") else "PARTNER'S AGENT: " + l.split(": ", 1)[-1]
                             for l in item["lines"]],
            "insight": item["insight"], "idea": item["idea"]}
    out = serve.gemini(system, json.dumps(user, ensure_ascii=False, indent=1), JUDGE_SCHEMA, temperature=0)
    scores = {k: min(5, max(1, int(out.get(k) or 0))) if str(out.get(k, "")).strip().lstrip("-").isdigit() else None for k in KEYS}
    reasons = {k: serve.clip((out.get("reasons") or {}).get(k), 200) for k in KEYS}
    return {"scores": scores, "reasons": reasons, "model": serve.MODEL}


def cmd_judge(args):
    if not serve.KEY:
        sys.exit("no GEMINI_API_KEY in .env")
    d = set_dir(args.set)
    key = json.loads((d / "key.json").read_text(encoding="utf-8"))["items"]
    path = d / "judge.json"
    done = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    people = load_people()
    system = serve.prompt("judge", language="English")   # blind: the judge never sees the memory condition
    todo = [i for i in key if i not in done]
    print(f"judging {len(todo)} items with {serve.MODEL} ({len(done)} already done)")

    def work(iid):
        try:
            return iid, judge_one(key[iid], people, system)
        except Exception as e:   # one failed item should not lose the others; rerun judge to retry it
            print(f"  {iid}: {e}")
            return iid, None
    with ThreadPoolExecutor(max_workers=2) as pool:   # serve.GATE allows two calls at once
        for iid, res in pool.map(work, todo):
            if res:
                done[iid] = res
                path.write_text(json.dumps(done, ensure_ascii=False, indent=1), encoding="utf-8")
                print(f"  {iid}: " + " ".join(f"{k}={res['scores'][k]}" for k in KEYS))
    print(f"{len(done)}/{len(key)} judged -> {path}")


# ---------- report ----------
def ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(x, y):
    if len(x) < 3:
        return None
    rx, ry = ranks(x), ranks(y)
    try:
        return statistics.correlation(rx, ry)
    except statistics.StatisticsError:   # one side constant
        return None


def fmt(x, d=2):
    return "–" if x is None else f"{x:.{d}f}"


def cmd_report(args):
    d = set_dir(args.set)
    key = json.loads((d / "key.json").read_text(encoding="utf-8"))
    items = key["items"]
    human = {}
    for f in sorted(d.glob("ratings_*.csv")):
        with f.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                iid = (row.get("item") or "").strip()
                if iid not in items:
                    continue
                vals = {}
                for k in KEYS:
                    v = (row.get(k) or "").strip()
                    if v.isdigit() and 1 <= int(v) <= 5:
                        vals[k] = int(v)
                if vals:
                    human[iid] = vals
    jpath = d / "judge.json"
    llm = {i: r["scores"] for i, r in (json.loads(jpath.read_text(encoding="utf-8")).items() if jpath.exists() else [])}
    conds = [c for c in CONDITIONS if any(it["memory"] == c for it in items.values())] + \
            sorted({it["memory"] for it in items.values()} - set(CONDITIONS))

    def mean_n(src, cond, k):
        xs = [src[i][k] for i, it in items.items() if it["memory"] == cond and i in src and src[i].get(k) is not None]
        return (statistics.mean(xs), len(xs)) if xs else (None, 0)

    runs = {r["_n"]: r for r in load_runs(set(key["runs"]))}
    run_list = ", ".join(f"{n} ({r.get('memory', '?')})" for n, r in runs.items())
    judge_model = f" ({next(iter(json.loads(jpath.read_text(encoding='utf-8')).values()))['model']})" if llm else ""
    L = [f"# placelab evaluation · {args.set}", "",
         f"Runs {run_list}. {len(items)} rated conversations; {len(human)} rated by people, {len(llm)} by the LLM judge{judge_model}. "
         "Scores are 1 to 5, from the point of view of the student whose agent talked; people and the judge "
         "did not see the memory condition.", "",
         "## The runs", "", "| run | memory | conversations | ideas | follow-ups |", "| --- | --- | --- | --- | --- |"]
    for n, r in runs.items():
        s = r.get("stats", {})
        L.append(f"| {n} | {r.get('memory', '?')} | {s.get('convs', len(r.get('convs', [])))} | {s.get('ideas', len(r.get('ideas', [])))} | {s.get('followups', '–')} |")
    L += ["", "## Ratings by memory condition", "", "Mean (number of ratings). People first, then the LLM judge.", "",
          "| criterion | " + " | ".join(f"{c}: people | {c}: LLM" for c in conds) + " |",
          "| --- | " + " | ".join("--- | ---" for _ in conds) + " |"]
    for k, desc in CRITERIA:
        cells = []
        for c in conds:
            for src in (human, llm):
                m, n = mean_n(src, c, k)
                cells.append(f"{fmt(m)} ({n})" if n else "–")
        L.append(f"| {k} | " + " | ".join(cells) + " |")
    L += ["", "## Do people and the LLM judge agree?", "",
          "On conversations rated by both. Gap = LLM − people (positive: the judge is more generous). "
          "Spearman ρ compares the order in which they rank conversations.", "",
          "| criterion | n | people | LLM | gap | Spearman ρ | same score | within 1 |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for k, _ in CRITERIA:
        both = [i for i in items if i in human and i in llm and k in human[i] and llm[i].get(k) is not None]
        if not both:
            L.append(f"| {k} | 0 | – | – | – | – | – | – |")
            continue
        h, j = [human[i][k] for i in both], [llm[i][k] for i in both]
        L.append(f"| {k} | {len(both)} | {fmt(statistics.mean(h))} | {fmt(statistics.mean(j))} | {statistics.mean(j) - statistics.mean(h):+.2f} | "
                 f"{fmt(spearman(h, j))} | {round(100 * sum(a == b for a, b in zip(h, j)) / len(both))}% | {round(100 * sum(abs(a - b) <= 1 for a, b in zip(h, j)) / len(both))}% |")
    owners = sorted({it["owner"] for it in items.values()})
    L += ["", "## By person", "", "Mean over all criteria; gap = LLM − person on the conversations both rated.", "",
          "| person | rated | person mean | LLM mean | gap |", "| --- | --- | --- | --- | --- |"]
    people = load_people()
    for o in owners:
        mine = [i for i, it in items.items() if it["owner"] == o]
        hv = [v for i in mine if i in human for v in human[i].values()]
        both = [(human[i][k], llm[i][k]) for i in mine if i in human and i in llm for k in KEYS if k in human[i] and llm[i].get(k) is not None]
        L.append(f"| {people.get(o, {}).get('name', o)} | {sum(i in human for i in mine)}/{len(mine)} | {fmt(statistics.mean(hv) if hv else None)} | "
                 f"{fmt(statistics.mean([b for _, b in both]) if both else None)} | {fmt(statistics.mean([b - a for a, b in both]) if both else None)} |")
    disagree = sorted(((abs(human[i][k] - llm[i][k]), i, k) for i in items if i in human and i in llm for k in KEYS
                       if k in human[i] and llm[i].get(k) is not None), reverse=True)[:5]
    if disagree and jpath.exists():
        J = json.loads(jpath.read_text(encoding="utf-8"))
        L += ["", "## Largest disagreements", "", "| item | criterion | person | LLM | the judge's reason |", "| --- | --- | --- | --- | --- |"]
        for gap, i, k in disagree:
            if gap:
                L.append(f"| {i} | {k} | {human[i][k]} | {llm[i][k]} | {J[i]['reasons'].get(k, '').replace('|', '/')} |")
    (d / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\n-> {d / 'report.md'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sheets", help="one blind rating sheet per person from saved runs")
    s.add_argument("--runs", help="run numbers, e.g. 2,3,4 (default: all saved runs)")
    s.add_argument("--per-person", type=int, default=4, help="conversations per person per memory condition (default 4)")
    s.add_argument("--seed", type=int, default=2026)
    for name in ("judge", "report"):
        sub.add_parser(name)
    for p in sub.choices.values():
        p.add_argument("--set", default="latest", help="folder name under data/eval/ (default: latest)")
    args = ap.parse_args()
    for st in (sys.stdout, sys.stderr):
        st.reconfigure(encoding="utf-8")
    {"sheets": cmd_sheets, "judge": cmd_judge, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
