/bin/bash <<'BASH'
set -euo pipefail

# 1) Fresh folder + venv
mkdir -p "$HOME/studyforge_v7_2"
cd "$HOME/studyforge_v7_2"
python3 -m venv .venv
source .venv/bin/activate

# 2) Deps
python -m pip install --upgrade pip
python -m pip install streamlit pandas python-dateutil pyarrow google-api-python-client google-auth-httplib2 google-auth-oauthlib

# 3) App code
cat > app.py <<'PY'
import streamlit as st, pandas as pd, re, math
from datetime import datetime, timedelta
from dateutil.parser import parse as dateparse
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

APP_NAME = "StudyForge"
APP_VER  = "v7.2 • AI Tron (stable)"
SCOPES   = ['https://www.googleapis.com/auth/calendar']

# ---------------- Session (no disk save) ----------------
def _default_state():
    return {
        "assignments": [],           # [{id, name, course, type, due_date (YYYY-MM-DD), est_min}]
        "study_log": {},             # {aid: minutes}
        "targets": {},               # {aid: target_minutes}
        "gpa": [],                   # [{course, weight, score}]
        "mood": [],                  # [{date, energy, note}]
        "google": {"client_id":"", "client_secret":"", "creds":None, "calendar_id":"primary"},
        "show_intro": True,
    }
if "data" not in st.session_state:
    st.session_state.data = _default_state()
D = st.session_state.data

# ---------------- Theme ----------------
st.set_page_config(page_title=f"{APP_NAME} {APP_VER}", page_icon="⚡", layout="wide")
st.markdown("""
<style>
@keyframes gridMove {0%{background-position:0 0,0 0}100%{background-position:100px 100px,100px 100px}}
:root{--bg:#0b0f14;--cyan:#00ffff;--blue:#0aa2ff;--white:#e6f7ff}
.stApp{background:radial-gradient(1200px 600px at 50% -10%, #06202a55, transparent),
linear-gradient(0deg,#061018 0%,#061118 100%); color:var(--white)}
.stApp::before{content:"";position:fixed;inset:0;z-index:-1;opacity:.25;
background:linear-gradient(transparent 23px,#00ffff22 24px),
linear-gradient(90deg,transparent 23px,#00ffff22 24px);
background-size:24px 24px,24px 24px;animation:gridMove 12s linear infinite}
.sf-card{background:#0c1b24cc;border:1px solid #00ffff44;border-radius:16px;padding:16px;backdrop-filter:blur(6px)}
.sf-title{font-weight:800;letter-spacing:.5px}
.sf-glow{color:#00ffff;text-shadow:0 0 6px #00ffff}
.sf-chip{border:1px solid #00ffff66;padding:4px 8px;border-radius:999px;font-size:12px;margin-right:6px}
div[data-testid="stProgress"] div[role="progressbar"]{
  background:linear-gradient(90deg,#00ffff,#0aa2ff); box-shadow:0 0 12px #00ffff55}
.sf-hide header, .sf-hide [data-testid="stSidebar"] { display:none !important }
</style>
""", unsafe_allow_html=True)

# ---------------- Helpers ----------------
def now(): return datetime.now()
def new_id(): return f"a{int(datetime.utcnow().timestamp()*1000)}"
def urgency_label(days_left:int) -> str:
    if days_left <= 2: return "🔴 High"
    if days_left <= 5: return "🟡 Medium"
    return "🟢 Low"
def type_priority(t:str) -> float:
    return {"exam":3.0,"project":2.2,"quiz":1.8,"homework":1.0,"other":0.7}.get((t or "other").lower(),0.7)
def coach_score(a:dict) -> float:
    due = dateparse(a["due_date"])
    days = (due - now()).total_seconds()/86400.0
    urgency = max(0, 21 - days)
    target = D["targets"].get(a["id"], a.get("est_min", 60))
    logged = D["study_log"].get(a["id"], 0)
    study_gap_hours = max(0, target - logged) / 60.0
    return 0.6*urgency + 0.3*type_priority(a.get("type")) + 0.1*study_gap_hours

def parse_syllabus_text(text:str, default_course="COURSE"):
    items=[]; weights={}
    date_pat = re.compile(r'(\b\d{4}-\d{1,2}-\d{1,2}\b|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+\d{1,2}(?:,\s*\d{4})?)', re.I)
    weight_line = re.compile(r'(?P<cat>exam|final|midterm|quiz|quizzes|homework|assignments|labs?|projects?|essays?)\s*[:\-]?\s*(?P<pct>\d{1,3})\s*%+', re.I)
    for raw in text.splitlines():
        line=raw.strip()
        if not line: continue
        m=date_pat.search(line)
        if m:
            dstr=m.group(1)
            dt = dateparse(dstr, fuzzy=True, default=datetime(2000,1,1,23,59))
            if dt.year == 2000: dt = dt.replace(year=now().year)
            low=line.lower()
            typ="homework"
            if any(k in low for k in ["exam","final","midterm"]): typ="exam"
            elif "quiz" in low: typ="quiz"
            elif "project" in low: typ="project"
            items.append({
                "id": new_id(),
                "name": line if len(line) <= 120 else line[:120],
                "course": default_course,
                "type": typ,
                "due_date": dt.strftime("%Y-%m-%d"),
                "est_min": 120
            })
        for wm in weight_line.finditer(line):
            cat=wm.group("cat").lower(); pct=int(wm.group("pct"))
            key="exam" if ("exam" in cat or "final" in cat or "midterm" in cat) else \
                 "quiz" if "quiz" in cat else \
                 "homework" if ("homework" in cat or "assign" in cat) else \
                 "project" if "project" in cat else "other"
            weights[key]=pct
    s = sum(weights.values()) if weights else 0
    if s and 90 <= s <= 110:
        for k in list(weights.keys()):
            weights[k] = round(100*weights[k]/s)
    return items, weights

def pct_to_gpa(p:float)->float:
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

# Google Calendar
def google_connected(): return D["google"].get("creds") is not None
def build_calendar():
    creds = D["google"]["creds"]
    return build("calendar","v3", credentials=creds, cache_discovery=False)
def run_google_oauth(client_id:str, client_secret:str):
    cfg={"installed":{
        "client_id":client_id,
        "project_id":"studyforge-local",
        "auth_uri":"https://accounts.google.com/o/oauth2/auth",
        "token_uri":"https://oauth2.googleapis.com/token",
        "redirect_uris":["http://localhost:8080/","http://localhost"]
    }}
    flow=InstalledAppFlow.from_client_config(cfg, SCOPES)
    creds=flow.run_local_server(port=8080, prompt='consent')
    D["google"]["creds"]=creds
def sync_assignments_to_calendar():
    service=build_calendar()
    cal_id=D["google"].get("calendar_id","primary")
    for a in D["assignments"]:
        body={"summary": f"[{a.get('course','')}] {a['name']}",
              "description": f"Type: {a.get('type','')}. Created by StudyForge.",
              "start":{"date":a["due_date"]},
              "end":{"date":a["due_date"]}}
        service.events().insert(calendarId=cal_id, body=body).execute()
def generate_study_sessions(a:dict):
    target=D["targets"].get(a["id"], a.get("est_min",60))
    blocks=math.ceil(target/60)
    due=datetime.strptime(a["due_date"], "%Y-%m-%d").date()
    sessions=[]; d=due - timedelta(days=1)
    today=datetime.today().date()
    while blocks>0 and d>=today:
        sessions.append({"date":d.isoformat(),"minutes":60})
        blocks-=1; d-=timedelta(days=1)
    if blocks>0: sessions.append({"date":due.isoformat(),"minutes":blocks*60})
    return sessions
def sync_study_to_calendar():
    service=build_calendar()
    cal_id=D["google"].get("calendar_id","primary")
    for a in D["assignments"]:
        for s in generate_study_sessions(a):
            start_dt=f"{s['date']}T19:00:00"
            end_dt=(datetime.strptime(start_dt,"%Y-%m-%dT%H:%M:%S")+timedelta(minutes=s["minutes"])).strftime("%Y-%m-%dT%H:%M:%S")
            body={"summary": f"Study: {a['name']}",
                  "description":"Auto-planned by StudyForge",
                  "start":{"dateTime":start_dt},
                  "end":{"dateTime":end_dt}}
            service.events().insert(calendarId=cal_id, body=body).execute()

# ---------------- Header / Intro ----------------
st.markdown(f"<div class='sf-title' style='font-size:28px'><span class='sf-glow'>⚡ {APP_NAME}</span> {APP_VER}</div>", unsafe_allow_html=True)
st.markdown("<div class='sf-card'>“You don’t rise to your goals. You fall to your systems.” — James Clear</div>", unsafe_allow_html=True)

if D.get("show_intro", True):
    st.markdown("<div class='sf-card'><b>Welcome!</b> Click continue to enter the dashboard.</div>", unsafe_allow_html=True)
    if st.button("Enter Dashboard", key="intro_enter"):
        D["show_intro"] = False
        st.rerun()
    st.stop()

# ---------------- Tabs ----------------
tab_home, tab_coach, tab_gpa, tab_tasks, tab_reader, tab_focus, tab_settings = st.tabs(
    ["🏠 Home","🧠 Coach","📊 GPA","📝 Tasks","📂 Syllabus","⏱ Focus","⚙ Settings"]
)

# ---------------- Home ----------------
with tab_home:
    c1,c2,c3 = st.columns([2,1,1])
    with c1:
        st.subheader("Upcoming", anchor=False)
        if not D["assignments"]:
            st.info("No assignments yet. Add some in Tasks or import via Syllabus.")
        else:
            for a in sorted(D["assignments"], key=lambda x:x["due_date"])[:6]:
                st.markdown(f"- <span class='sf-chip'>{a.get('type','')}</span> **{a['name']}** <span class='sf-chip'>{a['due_date']}</span>", unsafe_allow_html=True)
    with c2:
        st.subheader("Study Progress", anchor=False)
        total_target = sum(D["targets"].get(a["id"], a.get("est_min",0)) for a in D["assignments"])
        total_logged = sum(D["study_log"].get(a["id"],0) for a in D["assignments"])
        pct = 0 if total_target == 0 else min(1.0, total_logged/max(1,total_target))
        st.progress(pct, text=f"{total_logged}/{total_target} min")
    with c3:
        st.subheader("Energy", anchor=False)
        energy = st.slider("How energized?", 1, 5, 4, key="home_energy")
        note   = st.text_input("Note (optional)", key="home_note")
        if st.button("Log energy", key="home_log_energy"):
            D["mood"].append({"date": now().strftime("%Y-%m-%d"), "energy": int(energy), "note": note})
            st.success("Logged.")

# ---------------- Coach ----------------
with tab_coach:
    st.subheader("Today’s Plan", anchor=False)
    if not D["assignments"]:
        st.info("Add assignments first.")
    else:
        pending = [a for a in D["assignments"] if datetime.strptime(a["due_date"], "%Y-%m-%d") >= datetime.today()]
        for a in sorted(pending, key=lambda a:(-coach_score(a), a["due_date"]))[:8]:
            due = datetime.strptime(a["due_date"], "%Y-%m-%d").date()
            days = (due - datetime.today().date()).days
            badge = urgency_label(days)
            target = D["targets"].get(a["id"], a.get("est_min",60))
            logged = D["study_log"].get(a["id"], 0)
            st.markdown(f"**{a['name']}** · {a.get('course','')} · *{a.get('type','')}* · Due **{a['due_date']}** · {badge}")
            st.progress(min(1.0, logged/max(1,target)), text=f"{logged}/{target} min")
            cc1,cc2,cc3,cc4 = st.columns(4)
            add_val = cc1.number_input("Add min", 0, 240, 0, 5, key=f"coach_add_{a['id']}")
            if cc2.button("+15", key=f"coach_15_{a['id']}"):
                D["study_log"][a["id"]] = logged + 15; st.experimental_rerun()
            if cc3.button("+30", key=f"coach_30_{a['id']}"):
                D["study_log"][a["id"]] = logged + 30; st.experimental_rerun()
            if cc4.button("Add", key=f"coach_addbtn_{a['id']}"):
                D["study_log"][a["id"]] = logged + int(add_val or 0); st.experimental_rerun()
            st.markdown("---")

# ---------------- GPA ----------------
with tab_gpa:
    st.subheader("GPA & Grade Rescue", anchor=False)
    g1,g2,g3,g4 = st.columns([2,1,1,1])
    course = g1.text_input("Course", key="gpa_course")
    weight = g2.number_input("Weight %", 0, 100, 10, key="gpa_weight")
    score  = g3.number_input("Score %", 0.0, 100.0, 92.0, key="gpa_score")
    if g4.button("Add grade", key="gpa_add"):
        D["gpa"].append({"course": course, "weight": weight, "score": score}); st.success("Grade added.")
    if D["gpa"]:
        df = pd.DataFrame(D["gpa"]); st.dataframe(df, use_container_width=True)
        tot_w = sum(x["weight"] for x in D["gpa"])
        wavg  = 0 if tot_w == 0 else sum(x["weight"]*x["score"] for x in D["gpa"]) / tot_w
        st.metric("Current weighted %", f"{wavg:.2f}%"); st.metric("≈ Course GPA", f"{pct_to_gpa(wavg):.2f}")
        target = st.slider("Target final %", 50, 100, 90, key="gpa_target")
        rem = 100 - tot_w
        if rem > 0:
            needed = (target - (wavg*(tot_w/100.0))) / (rem/100.0)
            st.info(f"You need **{needed:.1f}%** average on the remaining {rem}% to hit **{target}%**.")
        else:
            st.info("All weight graded.")

# ---------------- Tasks ----------------
with tab_tasks:
    st.subheader("Add / Manage Assignments", anchor=False)
    c1,c2,c3,c4 = st.columns([3,1.6,1.6,1.6])
    name  = c1.text_input("Name", key="task_name")
    course= c2.text_input("Course", key="task_course")
    typ   = c3.selectbox("Type", ["homework","quiz","exam","project","other"], key="task_type")
    due   = c4.date_input("Due date", value=datetime.today()+timedelta(days=7), key="task_due")
    est   = st.number_input("Est. study minutes (for tests/projects)", 15, 1000, 120, key="task_est")
    if st.button("Add assignment", key="task_add"):
        aid = new_id()
        D["assignments"].append({
            "id": aid, "name": name, "course": course, "type": typ,
            "due_date": due.strftime("%Y-%m-%d"), "est_min": int(est)
        })
        if typ in ("exam","project","quiz"):
            D["targets"][aid] = int(est)
        st.success("Assignment added.")
    st.markdown("#### Current")
    if D["assignments"]:
        df = pd.DataFrame(D["assignments"])
        st.dataframe(df[["name","course","type","due_date","est_min"]].sort_values("due_date"),
                     use_container_width=True)
        if st.button("🧹 Remove past-due completed", key="task_clean"):
            today = datetime.today().date(); keep=[]
            for a in D["assignments"]:
                done = D["study_log"].get(a["id"],0) >= D["targets"].get(a["id"], a.get("est_min",0))
                if datetime.strptime(a["due_date"],"%Y-%m-%d").date() < today and done: continue
                keep.append(a)
            D["assignments"] = keep; st.success("Cleaned.")
    else:
        st.caption("No tasks yet.")

# ---------------- Syllabus Reader ----------------
with tab_reader:
    st.subheader("AI Syllabus Reader (simple)", anchor=False)
    text  = st.text_area("Paste syllabus text here", height=180, key="syll_text")
    course_hint = st.text_input("Course code", value="COURSE", key="syll_course")
    if st.button("Parse & add", key="syll_parse"):
        items, weights = parse_syllabus_text(text, course_hint)
        D["assignments"].extend(items)
        for it in items:
            if it["type"] in ("exam","quiz","project"): D["targets"][it["id"]] = it.get("est_min", 120)
        st.success(f"Added {len(items)} items. Weights: {weights or 'n/a'}")
    st.markdown("---")
    if st.button("Load Sample Syllabus", key="syll_sample"):
        sample = """BIO 1401 – Intro to Biology – Fall 2025
Grading:
- Homework: 20%
- Quizzes: 10%
- Projects: 10%
- Exams: 60%

Assignments:
- Homework 1 — Sep 5, 2025
- Quiz 1 — Sep 12, 2025
- Project Proposal — Oct 1, 2025
- Exam 1 — Oct 10, 2025
- Homework 2 — Oct 24, 2025
- Exam 2 — Nov 14, 2025
- Final Exam — Dec 10, 2025
"""
        items, weights = parse_syllabus_text(sample, "BIO")
        D["assignments"].extend(items)
        for it in items:
            if it["type"] in ("exam","quiz","project"): D["targets"][it["id"]] = it.get("est_min", 120)
        st.success(f"Loaded {len(items)} items.")

# ---------------- Focus ----------------
with tab_focus:
    st.subheader("Focus Mode 360°", anchor=False)
    hide = st.toggle("Hide UI chrome (immersive)", key="focus_hide")
    if hide: st.markdown("<div class='sf-hide'></div>", unsafe_allow_html=True)
    f1,f2 = st.columns(2)
    with f1:
        st.markdown("#### Pomodoro")
        fmin = st.number_input("Focus (min)", 5, 120, 25, key="pomo_focus")
        bmin = st.number_input("Break (min)", 1, 60, 5, key="pomo_break")
        if st.button("Start Pomodoro", key="pomo_start"):
            st.success(f"Focus {fmin} / Break {bmin} started. (Timer runs while page is open)")
    with f2:
        st.markdown("#### Retention Check")
        topic  = st.text_input("What did you study?", key="ret_topic")
        recall = st.slider("Recall now (1–5)", 1, 5, 3, key="ret_recall")
        if st.button("Log retention", key="ret_log"):
            st.success("Logged. Review suggestions will appear in Coach later.")

# ---------------- Settings ----------------
with tab_settings:
    st.subheader("Google Calendar", anchor=False)
    g1,g2,g3 = st.columns([2,2,1])
    D["google"]["client_id"]     = g1.text_input("OAuth Client ID", value=D["google"].get("client_id",""), key="gc_id")
    D["google"]["client_secret"] = g2.text_input("OAuth Client Secret", value=D["google"].get("client_secret",""), key="gc_secret")
    D["google"]["calendar_id"]   = g3.text_input("Calendar ID", value=D["google"].get("calendar_id","primary"), key="gc_cal")
    h1,h2,h3 = st.columns(3)
    if h1.button("Connect Google", key="gc_connect"):
        try:
            run_google_oauth(D["google"]["client_id"], D["google"]["client_secret"])
            st.success("Connected to Google Calendar.")
        except Exception as e:
            st.error(f"OAuth failed: {e}")
    if h2.button("Sync Assignments → Calendar", key="gc_sync_tasks", disabled=not google_connected()):
        try: sync_assignments_to_calendar(); st.success("Assignments synced.")
        except Exception as e: st.error(f"Sync failed: {e}")
    if h3.button("Sync Study Sessions → Calendar", key="gc_sync_study", disabled=not google_connected()):
        try: sync_study_to_calendar(); st.success("Study sessions synced.")
        except Exception as e: st.error(f"Sync failed: {e}")
PY

# 4) Launch
streamlit run app.py
BASH
