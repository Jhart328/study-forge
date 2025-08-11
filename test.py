# StudyForge v5.1 — Single-file Streamlit app
# Paste this into GitHub as app.py, then deploy on Streamlit Community Cloud.
# Run locally: streamlit run app.py

import streamlit as st
import pandas as pd
import json, uuid, io, re, random
from pathlib import Path
from datetime import datetime, timedelta
from dateutil import parser as dtparser
import pytz
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

APP_NAME = "StudyForge"
APP_VER  = "v5.1"
DEFAULT_TZ = "America/Chicago"
DATA_FILE = Path(".studyforge_data.json")

st.set_page_config(page_title=f"{APP_NAME} {APP_VER}", page_icon="📚", layout="wide")

ss = st.session_state

# ---------------- Defaults & State ----------------
def default_data():
    return {
        "prefs": {
            "timezone": DEFAULT_TZ, "horizon_days": 21,
            "day_start": "06:00", "day_end": "22:00",
            "chunk_minutes": 60, "max_daily_minutes": 300,
            "due_buffer_hours": 36, "quotes": True
        },
        "courses": {},          # "CHEM":{"credits":3,"weights":{"homework":20,"exam":50,...}}
        "assignments": [],      # list of {id,course,title,type,due_at,est_minutes,score,possible,completed}
        "study_targets": {},    # {aid: minutes_target}
        "study_log": {},        # {aid: minutes_logged}
        "streak": {"current":0,"longest":0,"last_date":""},
        "badges": [],           # ["bronze","silver","gold","diamond"]
    }

def ensure_state():
    if "loaded" not in ss: ss.loaded = False
    if "dark" not in ss: ss.dark = False  # light default
    if "quotes_on" not in ss: ss.quotes_on = True
    if "demo_mode" not in ss: ss.demo_mode = False
    if "taglines" not in ss:
        ss.taglines = [
            "Forge your path to success.",
            "A GPA is just XP for real life.",
            "One assignment at a time.",
            "Study like Sun Tzu planned your semester.",
            "Your future self will thank you.",
            "Coffee is the fuel, discipline is the engine.",
            "Small steps → big wins.",
            "Consistency beats intensity.",
            "Make future-you proud.",
            "Plan smart, study calm."
        ]
    if "data" not in ss: ss.data = default_data()
    # backfill any missing keys
    for k,v in default_data().items():
        if k not in ss.data: ss.data[k]=v

def save():
    # Safe for cloud: ignore write errors
    try:
        DATA_FILE.write_text(json.dumps(ss.data, indent=2, default=str), encoding="utf-8")
    except Exception:
        pass

def load():
    if DATA_FILE.exists():
        try:
            ss.data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            ss.data = default_data()

# ---------------- Quotes & Theme ----------------
ART_OF_WAR = [
    "Victorious warriors win first and then go to war.",
    "In the midst of chaos, there is also opportunity.",
    "He will win who knows when to fight and when not to fight.",
    "The supreme art of war is to subdue the enemy without fighting.",
    "Ponder and deliberate before you make a move.",
]
STOIC = [
    "You have power over your mind, not outside events. — Marcus Aurelius",
    "We suffer more in imagination than in reality. — Seneca",
    "The impediment to action advances action. — Marcus Aurelius",
]
MUSASHI = [
    "Perceive that which cannot be seen with the eye.",
    "Do nothing that is of no use.",
]
QUOTE_POOL = ART_OF_WAR + STOIC + MUSASHI

def quote_mood(q: str) -> str:
    ql=q.lower()
    return "warm" if any(k in ql for k in ["victorious","opportunity","advance","power","win"]) else "cool"

def inject_css(dark: bool, mood: str):
    if not dark:
        bg1,bg2,bg3 = ("#FFF8F2","#FFE7C7","#D8F5E6") if mood=="warm" else ("#FFF8F2","#E6F0FF","#DDF5EE")
        text="#1f2937"; card="rgba(255,255,255,0.78)"; border="#e5e7eb"
    else:
        bg1,bg2,bg3 = ("#1E1E2E","#2A1F3A","#184D4A") if mood=="warm" else ("#1E1E2E","#1F2B3A","#1B3A3A")
        text="#F5F5F5"; card="rgba(255,255,255,0.06)"; border="rgba(255,255,255,0.12)"
    st.markdown(f"""
    <style>
      .stApp {{
        background: linear-gradient(120deg, {bg1}, {bg2}, {bg3});
        background-size: 200% 200%;
        animation: gradientShift 32s ease-in-out infinite;
        color:{text};
      }}
      @keyframes gradientShift {{0%{{background-position:0% 50%}}50%{{background-position:100% 50%}}100%{{background-position:0% 50%}}}}
      .sf-card {{background:{card};border:1px solid {border};border-radius:18px;padding:16px 18px;box-shadow:0 10px 24px rgba(0,0,0,0.06);backdrop-filter:blur(6px)}}
      .sf-title {{font-size:28px;font-weight:800;letter-spacing:.2px}}
      .sf-subtle {{opacity:.8;font-size:13px}}
      .sf-badge {{padding:4px 8px;border-radius:999px;border:1px solid {border};display:inline-block;margin-right:6px}}
      .sf-hint {{font-size:12px;opacity:.8}}
    </style>
    """, unsafe_allow_html=True)

# ---------------- Time helpers ----------------
def tzone(): return pytz.timezone(ss.data["prefs"].get("timezone", DEFAULT_TZ))
def now_local(): return datetime.now(tzone())

def parse_local(dt_str: str) -> datetime:
    """Parse any datetime string to tz-aware (local timezone)."""
    dt = dtparser.parse(dt_str)
    tz = tzone()
    if dt.tzinfo is None:
        return tz.localize(dt)
    return dt.astimezone(tz)

def parse_hhmm(s): return datetime.strptime(s, "%H:%M").time()

# ---------------- Estimators & GPA ----------------
def type_estimate(typ):
    base={"exam":300,"project":240,"lab":180,"essay":240,"quiz":90,"homework":120,"reading":60}
    return base.get((typ or "").lower(), 60)

def pct_to_gpa(p):
    p=float(p)
    if p>=93: return 4.0
    if p>=90: return 3.7
    if p>=87: return 3.3
    if p>=83: return 3.0
    if p>=80: return 2.7
    if p>=77: return 2.3
    if p>=73: return 2.0
    if p>=70: return 1.7
    if p>=67: return 1.3
    if p>=63: return 1.0
    if p>=60: return 0.7
    return 0.0

def grade_projection(weights, rows):
    bycat={}
    for r in rows:
        sc=float(r.get("score",0) or 0); pos=float(r.get("possible",0) or 0)
        if pos<=0: continue
        d=bycat.get(r.get("type",""),{"sc":0,"pos":0}); d["sc"]+=sc; d["pos"]+=pos; bycat[r.get("type","")]=d
    achieved=0.0; rem=0.0
    for cat,w in weights.items():
        d=bycat.get(cat)
        if d and d["pos"]>0: achieved += (d["sc"]/d["pos"]*100.0)*(w/100.0)
        else: rem += w
    return achieved, rem

def needed_avg_for_target(target, achieved, remaining_weight):
    if remaining_weight<=0: return None
    return max(0.0, min(100.0, (target-achieved)/(remaining_weight/100.0)))

# ---------------- Planner & Exports ----------------
def plan_sessions(assignments, prefs):
    tz=tzone()
    today=tz.localize(datetime.now().replace(hour=9,minute=0,second=0,microsecond=0))
    horizon=today+timedelta(days=int(prefs.get("horizon_days",21)))
    chunk=int(prefs.get("chunk_minutes",60))
    max_daily=int(prefs.get("max_daily_minutes",300))
    buffer_h=int(prefs.get("due_buffer_hours",36))
    caps={}
    d=today
    while d.date()<=horizon.date(): caps[d.date()]=max_daily; d+=timedelta(days=1)
    weight={"exam":3,"project":2,"quiz":2,"lab":2,"essay":2,"homework":1,"reading":1}
    tasks=sorted(assignments, key=lambda a:(parse_local(a["due_at"]), -weight.get(a.get("type","homework"),1)))
    out=[]
    for a in tasks:
        due=parse_local(a["due_at"]); target=due - timedelta(hours=buffer_h)
        need=int(a.get("est_minutes",60))
        day=target.date()
        while need>0:
            if day not in caps: break
            take=min(chunk, need, caps[day])
            if take>0:
                out.append({"aid":a["id"],"title":f"[{a.get('course','')}] {a['title']} — {a.get('type','Study').title()}","date":str(day),"minutes":take})
                caps[day]-=take; need-=take
            day = day - timedelta(days=1)
        if need>0:
            out.append({"aid":a["id"],"title":f"[OVERFLOW] {a.get('course','')}: {a['title']}","date":due.date().strftime("%Y-%m-%d"),"minutes":need})
    return out

def ics_export(sessions, prefs):
    tzid=prefs.get("timezone", DEFAULT_TZ)
    def head(): return f"BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//StudyForge//{APP_VER}//EN\nX-WR-TIMEZONE:{tzid}\n"
    def evt(summary, date_str, minutes):
        day=datetime.strptime(date_str,"%Y-%m-%d"); start=day.replace(hour=7,minute=0,second=0); end=start+timedelta(minutes=int(minutes))
        fmt=lambda d:d.strftime("%Y%m%dT%H%M%S")
        return ("BEGIN:VEVENT\n"
                f"UID:{uuid.uuid4().hex}\nDTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}\n"
                f"DTSTART;TZID={tzid}:{fmt(start)}\nDTEND;TZID={tzid}:{fmt(end)}\nSUMMARY:{summary}\nEND:VEVENT\n")
    return head()+"".join(evt(s["title"], s["date"], s["minutes"]) for s in sessions)+"END:VCALENDAR\n"

def pdf_export_week(week_days, assignments, sessions, path="StudyPlanner_Week.pdf"):
    c = canvas.Canvas(path, pagesize=letter); w,h = letter; y=h-50
    c.setFont("Helvetica-Bold", 16); c.drawString(50,y, f"{APP_NAME} {APP_VER} — Weekly Planner"); y-=24
    c.setFont("Helvetica", 10); c.drawString(50,y, f"Week of {week_days[0]}"); y-=18
    c.setFont("Helvetica-Bold", 12); c.drawString(50,y, "Assignments"); y-=16; c.setFont("Helvetica", 10)
    for a in assignments[:20]:
        c.drawString(60,y, f"- [{a.get('course','')}] {a['title']} ({a.get('type','')}) due {a['due_at'][:10]} {'✓' if a.get('completed') else ''}"); y-=14
        if y<80: c.showPage(); y=h-50
    y-=6; c.setFont("Helvetica-Bold",12); c.drawString(50,y,"Study Sessions"); y-=16; c.setFont("Helvetica",10)
    for s in sessions[:40]:
        c.drawString(60,y, f"- {s['date']} • {s['minutes']} min • {s['title'][:80]}"); y-=14
        if y<80: c.showPage(); y=h-50
    c.showPage(); c.save(); return path

# ---------------- Syllabus parsing ----------------
DATE_PAT = re.compile(r'(\b\d{4}-\d{1,2}-\d{1,2}\b|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+\d{1,2}(?:,\s*\d{4})?)', re.I)
TYPE_WORDS = {"exam":["exam","midterm","final"],"quiz":["quiz"],"homework":["homework","hw","problem set","pset","assignment"],"project":["project","milestone","presentation"],"reading":["reading","chapter","ch."],"lab":["lab","practicum"],"essay":["essay","paper","draft"]}
WEIGHT_LINE = re.compile(r'(?P<cat>exam|exams|final|midterm|quiz|quizzes|homework|assignments|labs?|projects?|readings?|essays?)\s*[:\-]?\s*(?P<pct>\d{1,3})\s*%+', re.I)

def guess_type(line:str):
    low=line.lower()
    for t,keys in TYPE_WORDS.items():
        if any(k in low for k in keys): return t
    return "homework"

def parse_syllabus(text, default_course="COURSE"):
    items=[]; weights={}
    for raw in text.splitlines():
        line=raw.strip()
        if not line: continue
        m=DATE_PAT.search(line)
        if m:
            dstr=m.group(1); typ=guess_type(line)
            title=re.sub(r"\s+"," ", line[:200])
            dt=dtparser.parse(dstr, fuzzy=True, default=datetime(2000,1,1,23,59))
            if dt.year==2000: dt=dt.replace(year=datetime.now().year)
            items.append({"id":uuid.uuid4().hex,"course":default_course.strip().upper(),"title":title,"type":typ,
                          "due_at":dt.strftime("%Y-%m-%dT%H:%M:%S"),"est_minutes":type_estimate(typ),
                          "score":0.0,"possible":0.0,"completed":False})
        for wm in WEIGHT_LINE.finditer(line):
            cat=wm.group("cat").lower(); pct=int(wm.group("pct"))
            if "exam" in cat: key="exam"
            elif "quiz" in cat: key="quiz"
            elif "homework" in cat or "assign" in cat: key="homework"
            elif "lab" in cat: key="lab"
            elif "project" in cat: key="project"
            elif "reading" in cat: key="reading"
            elif "essay" in cat: key="essay"
            else: key=cat
            weights[key]=pct
    s=sum(weights.values()) if weights else 0
    if s and 90<=s<=110:
        for k in list(weights.keys()):
            weights[k]=round(100*weights[k]/s)
    return items, weights

# ---------------- Demo syllabus ----------------
def demo_syllabus_text():
    return """BIO 1401 – Introduction to Biology – Fall 2025 Syllabus

Grading Breakdown:
- Homework: 20%
- Quizzes: 10%
- Labs: 20%
- Projects: 10%
- Exams: 40%

Schedule of Assignments & Exams

Week 2 – Sep 2, 2025
- Homework 1: Scientific Method — Due Sep 2, 2025

Week 3 – Sep 9, 2025
- Quiz 1: Cell Structure — Sep 9, 2025 (in class)

Week 4 – Sep 16, 2025
- Lab Report 1: Microscopy — Due Sep 16, 2025

Week 5 – Sep 23, 2025
- Homework 2: Cellular Respiration — Due Sep 23, 2025

Week 6 – Sep 30, 2025
- Exam 1: Units 1–3 — Sep 30, 2025 at 9:00 AM — Suggested Study Time: 4 hours

Week 7 – Oct 7, 2025
- Project Proposal: Semester Project — Due Oct 7, 2025

Week 8 – Oct 14, 2025
- Homework 3: Photosynthesis — Due Oct 14, 2025

Week 9 – Oct 21, 2025
- Quiz 2: Genetics — Oct 21, 2025 (in class)

Week 10 – Oct 28, 2025
- Lab Report 2: Enzyme Activity — Due Oct 28, 2025

Week 12 – Nov 11, 2025
- Exam 2: Units 4–6 — Nov 11, 2025 at 9:00 AM — Suggested Study Time: 5 hours

Week 13 – Nov 18, 2025
- Project Draft: Semester Project — Due Nov 18, 2025

Week 14 – Nov 25, 2025
- Homework 4: Evolution — Due Nov 25, 2025

Finals Week – Dec 10, 2025
- Final Exam: Comprehensive — Dec 10, 2025 at 8:00 AM — Suggested Study Time: 8 hours
"""

def load_demo():
    # Build from the demo syllabus text
    text = demo_syllabus_text()
    items, weights = parse_syllabus(text, default_course="BIO")
    # set IDs + default scores
    for it in items:
        it["id"]=uuid.uuid4().hex
        it.setdefault("score",0.0); it.setdefault("possible",0.0); it.setdefault("completed",False)
        it.setdefault("est_minutes", type_estimate(it.get("type")))
    ss.data["assignments"]=items
    ss.data["courses"]={"BIO":{"credits":3,"weights":weights or {"homework":20,"quiz":10,"lab":20,"project":10,"exam":40}}}
    # Study targets for tests/quizzes
    for a in ss.data["assignments"]:
        if a["type"] in ("exam","quiz","project"):
            ss.data["study_targets"][a["id"]] = a.get("est_minutes", type_estimate(a["type"]))
            ss.data["study_log"][a["id"]] = random.choice([0,30,60,120])
    # pre-fill some grades so GPA looks alive
    for a in ss.data["assignments"]:
        if a["type"]=="homework": a["score"],a["possible"]=95,100
        if a["type"]=="quiz": a["score"],a["possible"]=8,10
    # streak & badges
    ss.data["streak"]={"current":3,"longest":7,"last_date":now_local().strftime("%Y-%m-%d")}
    ss.data["badges"]=["bronze","silver"]
    save()

# ---------------- Streak logic ----------------
def bump_streak_if_today_activity():
    today=now_local().strftime("%Y-%m-%d")
    stt=ss.data["streak"]; last=stt.get("last_date","")
    if last==today: return
    if last:
        prev=datetime.strptime(last,"%Y-%m-%d").date()
        if prev == now_local().date()-timedelta(days=1): stt["current"]=stt.get("current",0)+1
        else: stt["current"]=1
    else: stt["current"]=1
    stt["last_date"]=today
    if stt["current"]>stt.get("longest",0):
        stt["longest"]=stt["current"]
        if stt["longest"]>=30 and "diamond" not in ss.data["badges"]: ss.data["badges"].append("diamond")
        elif stt["longest"]>=14 and "gold" not in ss.data["badges"]: ss.data["badges"].append("gold")
        elif stt["longest"]>=7 and "silver" not in ss.data["badges"]: ss.data["badges"].append("silver")
        elif stt["longest"]>=3 and "bronze" not in ss.data["badges"]: ss.data["badges"].append("bronze")
    save()

# ---------------- Init ----------------
ensure_state(); load()
random.seed()
quote = random.choice(QUOTE_POOL)
mood = quote_mood(quote)
inject_css(ss.dark, mood)

# Splash once per session (simple + smooth)
if not ss.loaded:
    st.markdown(f"""
    <div style="position:fixed;inset:0;display:flex;align-items:center;justify-content:center;z-index:9999;">
      <div class="sf-card" style="text-align:center;padding:28px 36px;">
        <div class="sf-title">📚 {APP_NAME} {APP_VER}</div>
        <div style="font-style:italic;opacity:0.9; margin-top:6px;">{random.choice(ss.taglines)}</div>
        <div style="margin-top:16px;max-width:520px;">“{quote}”</div>
        <div style="margin-top:16px" class="sf-subtle">Loading…</div>
      </div>
    </div>
    """, unsafe_allow_html=True)
    ss.loaded=True
    st.rerun()

# ---------------- Top bar ----------------
c1,c2 = st.columns([5,1])
with c1: st.markdown(f"### 📚 {APP_NAME} {APP_VER}  \n_Your Personal Academic Command Center_")
with c2:
    if st.toggle("🌙 Dark", value=ss.dark, key="dark_toggle"):
        if not ss.dark: ss.dark=True; st.rerun()
    else:
        if ss.dark: ss.dark=False; st.rerun()

# ---------------- Tabs ----------------
tab_dash, tab_plan, tab_gpa, tab_tasks, tab_settings = st.tabs(
    ["🏠 Dashboard","📅 Weekly Planner","📊 GPA","📋 Assignments","⚙ Settings"]
)

# ---------------- Dashboard ----------------
with tab_dash:
    st.markdown("#### Today at a glance")
    a = ss.data["assignments"]

    # GPA snapshot across courses (weighted)
    rows_by_course={}
    for r in a: rows_by_course.setdefault(r.get("course",""), []).append(r)
    total_qp=0.0; total_cred=0.0
    for code, rows in rows_by_course.items():
        w=ss.data["courses"].get(code,{}).get("weights",{})
        if not w: continue
        by_type={}
        for r in rows:
            sc=float(r.get("score",0)); ps=float(r.get("possible",0))
            if ps<=0: continue
            d=by_type.get(r.get("type",""),{"sc":0,"pos":0}); d["sc"]+=sc; d["pos"]+=ps; by_type[r.get("type","")]=d
        pct=0.0
        for cat,pctw in w.items():
            d=by_type.get(cat)
            if d and d["pos"]>0: pct += (d["sc"]/d["pos"]*100.0)*(pctw/100.0)
        gpa=pct_to_gpa(pct); creds=float(ss.data["courses"].get(code,{}).get("credits",3))
        total_qp+=gpa*creds; total_cred+=creds
    snap_gpa = f"{(total_qp/total_cred):.2f}" if total_cred>0 else "—"

    # Upcoming tests/projects for Study Tracker
    now = now_local()
    upcoming=[x for x in a if x.get("type") in ("exam","quiz","project") and parse_local(x["due_at"])>=now]
    upcoming=sorted(upcoming, key=lambda x: parse_local(x["due_at"]))[:3]

    c1,c2,c3,c4 = st.columns(4)
    with c1: st.markdown(f'<div class="sf-card">📊 <b>Term GPA (est)</b><br><span class="sf-subtle">{snap_gpa}</span></div>', unsafe_allow_html=True)
    s=ss.data["streak"]; badges=" ".join(f'<span class="sf-badge">{b}</span>' for b in ss.data.get("badges",[]))
    with c2: st.markdown(f'<div class="sf-card">🔥 <b>Streak</b><br><span class="sf-subtle">{s.get("current",0)} days (best {s.get("longest",0)})</span><br>{badges}</div>', unsafe_allow_html=True)
    tot=len(a); done=len([x for x in a if x.get("completed")]); pct=(done/tot*100 if tot else 0)
    with c3: st.markdown(f'<div class="sf-card">🎯 <b>Semester Progress</b><br><span class="sf-subtle">{pct:.0f}% complete</span></div>', unsafe_allow_html=True)
    with c4:
        if upcoming:
            x=upcoming[0]; aid=x["id"]
            target=ss.data["study_targets"].get(aid, x.get("est_minutes", type_estimate(x.get("type"))))
            logged=int(ss.data["study_log"].get(aid,0))
            st.markdown(f'<div class="sf-card">⏳ <b>Next: {x.get("type","").title()}</b><br><span class="sf-subtle">[{x.get("course","")}] {x.get("title","")}</span><br><span class="sf-subtle">Study {logged}/{target} min</span></div>', unsafe_allow_html=True)
        else:
            st.markdown('<div class="sf-card">⏳ <b>Next Study</b><br><span class="sf-subtle">No tests soon.</span></div>', unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("#### Study Tracker")
    if not upcoming: st.caption("No upcoming exams/quizzes/projects detected.")
    for x in upcoming:
        aid=x["id"]
        target=ss.data["study_targets"].get(aid, x.get("est_minutes", type_estimate(x.get("type"))))
        logged=int(ss.data["study_log"].get(aid,0))
        col1,col2,col3,col4 = st.columns([3,2,3,2])
        with col1:
            st.write(f"**[{x.get('course','')}] {x.get('title','')}** • {x.get('type','').title()}  • Due {x.get('due_at')[:10]}")
            st.progress(min(1.0, logged/max(1,target)), text=f"{logged}/{target} min")
        with col2:
            new_t = st.number_input("Target (min)", min_value=30, max_value=1000, step=15, value=int(target), key=f"t_{aid}")
            if new_t != target:
                ss.data["study_targets"][aid]=int(new_t); save()
        with col3:
            add_custom = st.number_input("Add minutes", min_value=0, max_value=240, step=5, value=0, key=f"ad_{aid}")
        with col4:
            if st.button("+15", key=f"a15_{aid}"): ss.data["study_log"][aid]=logged+15; bump_streak_if_today_activity(); save(); st.rerun()
            if st.button("+30", key=f"a30_{aid}"): ss.data["study_log"][aid]=logged+30; bump_streak_if_today_activity(); save(); st.rerun()
            if st.button("+60", key=f"a60_{aid}"): ss.data["study_log"][aid]=logged+60; bump_streak_if_today_activity(); save(); st.rerun()
            if st.button("Add",  key=f"aadd_{aid}"): ss.data["study_log"][aid]=logged+int(add_custom); bump_streak_if_today_activity(); save(); st.rerun()

    if ss.quotes_on:
        st.markdown("---")
        st.markdown(f"> ***Daily Strategy Quote***  \n> _{quote}_")

# ---------------- Weekly Planner ----------------
with tab_plan:
    st.subheader("Week View")
    sessions = plan_sessions(ss.data["assignments"], ss.data["prefs"])
    start = st.date_input("Week starting (Mon suggested)", value=now_local().date())
    week=[(start+timedelta(days=i)) for i in range(7)]
    by_day={d.strftime("%Y-%m-%d"):[] for d in week}
    for sitem in sessions:
        if sitem["date"] in by_day: by_day[sitem["date"]].append(sitem)
    cols=st.columns(7)
    for i,d in enumerate(week):
        key=d.strftime("%Y-%m-%d")
        with cols[i]:
            st.markdown(f"**{d.strftime('%a %m/%d')}**")
            items=by_day.get(key,[])
            if not items: st.caption("—")
            for it in items: st.write(f"- {it['minutes']} min • {it['title'][:50]}")
    st.markdown("---")
    colA,colB,colC = st.columns(3)
    with colA:
        if st.button("🔄 Regenerate Plan"): st.rerun()
    with colB:
        ics = ics_export(sessions, ss.data["prefs"])
        st.download_button("⬇ Download .ics", data=ics, file_name="study_plan.ics", mime="text/calendar")
    with colC:
        pdf_path = pdf_export_week([week[0].strftime("%Y-%m-%d")], ss.data["assignments"], sessions)
        with open(pdf_path,"rb") as f:
            st.download_button("📄 Export Week PDF", data=f.read(), file_name=pdf_path, mime="application/pdf")

# ---------------- GPA ----------------
with tab_gpa:
    st.subheader("Courses & Weights")
    col1,col2 = st.columns([2,1])
    with col1:
        code = st.text_input("Course code", placeholder="BIO / CHEM / MATH …")
        creds = st.number_input("Credits", min_value=0.5, max_value=8.0, step=0.5, value=3.0)
        if st.button("Add/Update Course"):
            c=code.strip().upper()
            if c:
                ss.data["courses"].setdefault(c, {"credits":float(creds), "weights":{}})
                ss.data["courses"][c]["credits"]=float(creds); save(); st.success(f"{c} saved.")
    with col2:
        pick = st.selectbox("Edit Weights", ["(select)"]+list(ss.data["courses"].keys()))
    if pick and pick!="(select)":
        w = ss.data["courses"][pick].get("weights",{})
        catlist=["homework","quiz","exam","project","lab","reading","essay"]
        cols=st.columns(len(catlist))
        neww={}
        for i,k in enumerate(catlist):
            neww[k]=cols[i].number_input(f"{k.title()} %", 0,100, value=int(w.get(k,0)))
        if st.button("Save Weights"):
            ss.data["courses"][pick]["weights"]={k:v for k,v in neww.items() if v>0}; save(); st.success("Weights saved.")

        st.markdown("---"); st.markdown("#### Grade Projection")
        target = st.slider("Target final grade %", 50, 100, 90)
        rows=[r for r in ss.data["assignments"] if r.get("course","")==pick]
        ach, rem = grade_projection(ss.data["courses"][pick]["weights"], rows)
        need = needed_avg_for_target(target, ach, rem)
        if rem<=0: st.info(f"All weighted work done. Achieved ≈ **{ach:.1f}%**.")
        else:
            st.write(f"Current earned (weighted): **{ach:.1f}%**  •  Remaining weight: **{rem:.1f}%**")
            st.write(f"You need an average of **{need:.1f}%** on remaining work to hit **{target}%**.")

# ---------------- Assignments ----------------
with tab_tasks:
    st.subheader("Assignments")
    q = st.text_input("Search (title/course/type)")
    items=ss.data["assignments"]
    for it in items: it.setdefault("score",0.0); it.setdefault("possible",0.0); it.setdefault("completed",False)
    view=[x for x in items if (q.lower() in (x.get("title","")+x.get("course","")+x.get("type","")).lower())] if q else items
    if view:
        df=pd.DataFrame(view); df["due_date"]=df["due_at"].astype(str).str[:10]
        st.dataframe(df[["course","title","type","due_date","est_minutes","score","possible","completed"]].sort_values(["due_date","course","type"]))
    else:
        st.caption("No matching assignments.")
    st.markdown("#### Add / Edit")
    titles=["(new)"]+[a["title"] for a in items]
    sel=st.selectbox("Pick assignment", titles)
    existing=None if sel=="(new)" else next(a for a in items if a["title"]==sel)
    colA,colB=st.columns(2)
    with colA:
        t_course=st.text_input("Course", value=(existing or {}).get("course",""))
        t_title =st.text_input("Title",  value=(existing or {}).get("title",""))
        t_type  =st.selectbox("Type", ["homework","quiz","exam","project","reading","lab","essay"], index=["homework","quiz","exam","project","reading","lab","essay"].index((existing or {}).get("type","homework")))
        t_due   =st.text_input("Due date (YYYY-MM-DD)", value=((existing or {}).get("due_at","")[:10] or (now_local()+timedelta(days=7)).strftime("%Y-%m-%d")))
    with colB:
        t_est   =st.number_input("Est. minutes", 15, 600, step=15, value=int((existing or {}).get("est_minutes", type_estimate(t_type))))
        t_score =st.number_input("Score", min_value=0.0, step=0.5, value=float((existing or {}).get("score",0.0)))
        t_poss  =st.number_input("Possible", min_value=0.0, step=0.5, value=float((existing or {}).get("possible",0.0)))
        t_done  =st.checkbox("Completed", value=bool((existing or {}).get("completed",False)))
    c1,c2,c3,c4=st.columns(4)
    if c1.button("Save"):
        try:
            if len(t_due.strip())!=10: raise ValueError("Use YYYY-MM-DD")
            payload={"id":(existing or {"id":uuid.uuid4().hex})["id"],"course":t_course.strip().upper(),"title":t_title.strip(),"type":t_type,
                     "due_at":t_due.strip()+"T23:59:00","est_minutes":int(t_est),"score":float(t_score),"possible":float(t_poss),"completed":bool(t_done)}
            if existing:
                for i,a in enumerate(ss.data["assignments"]):
                    if a["id"]==existing["id"]: ss.data["assignments"][i]=payload; break
            else:
                ss.data["assignments"].append(payload)
                if t_type in ("exam","quiz","project"): ss.data["study_targets"].setdefault(payload["id"], int(t_est))
            save(); st.success("Saved."); st.rerun()
        except Exception as e:
            st.error(f"Fix the form. {e}")
    if c2.button("Delete") and existing:
        aid=existing["id"]
        ss.data["assignments"]=[x for x in ss.data["assignments"] if x["id"]!=aid]
        ss.data["study_targets"].pop(aid, None); ss.data["study_log"].pop(aid, None)
        save(); st.warning("Deleted."); st.rerun()
    if c3.button("Mark Completed") and existing:
        existing["completed"]=True; save(); st.success("Marked complete."); st.rerun()
    if c4.button("🧹 Remove past-due completed"):
        now=now_local(); before=len(ss.data["assignments"])
        ss.data["assignments"]=[x for x in ss.data["assignments"] if not (x.get("completed") and parse_local(x["due_at"])<now)]
        save(); st.success(f"Removed {before-len(ss.data['assignments'])} items."); st.rerun()

# ---------------- Settings: Demo, Import, Prefs ----------------
with tab_settings:
    st.subheader("Welcome / Demo")
    c1,c2 = st.columns(2)
    with c1:
        if st.button("🎭 Load Sample Syllabus (BIO)"):
            load_demo(); ss.demo_mode=True; st.success("Sample syllabus loaded!"); st.rerun()
    with c2:
        if st.button("🧹 Start My Planner (clear & fresh)"):
            ss.data = default_data(); ss.demo_mode=False; save(); st.rerun()

    st.markdown("---")
    st.subheader("Syllabus Import")
    course_hint=st.text_input("Course code", value="COURSE")
    f=st.file_uploader("Upload PDF/DOCX/TXT", type=["pdf","docx","txt"])
    if f is not None:
        # Keep it simple & robust for cloud: try utf-8 → latin-1
        raw=f.read()
        text=""
        try: text=raw.decode("utf-8")
        except: 
            try: text=raw.decode("latin-1","ignore")
            except: text=""
        if not text.strip(): st.error("Could not read text. Try a .txt or simpler PDF.")
        else:
            items, weights = parse_syllabus(text, default_course=course_hint)
            if items:
                st.write(f"Detected {len(items)} dated items.")
                if st.button("Add All Items"):
                    ss.data["assignments"].extend(items)
                    for it in items:
                        if it["type"] in ("exam","quiz","project"):
                            ss.data["study_targets"].setdefault(it["id"], it.get("est_minutes", type_estimate(it["type"])))
                    save(); st.success("Added."); st.rerun()
            if weights:
                st.write("Detected weights:", weights)
                if st.button("Apply weights to course"):
                    c=course_hint.strip().upper()
                    ss.data["courses"].setdefault(c, {"credits":3,"weights":{}})
                    ss.data["courses"][c]["weights"]=weights; save(); st.success(f"Weights applied to {c}.")

    st.markdown("---")
    st.subheader("Preferences & Quotes")
    p=ss.data["prefs"]
    colA,colB,colC = st.columns(3)
    p["timezone"]=colA.text_input("Timezone", value=p.get("timezone",DEFAULT_TZ))
    p["horizon_days"]=int(colA.number_input("Horizon days", 7, 120, value=int(p.get("horizon

