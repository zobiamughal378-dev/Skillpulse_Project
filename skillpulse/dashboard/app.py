import re
import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "config.yaml"
PALETTE = ["#7C5CFF", "#22D3EE", "#F472B6", "#FBBF24", "#34D399", "#60A5FA", "#FB7185", "#A78BFA"]
PURPLE_SCALE = ["#2A2250", "#5B45C9", "#7C5CFF", "#B6A4FF"]

CUSTOM_CSS = """
<style>
  .block-container {padding-top: 1.4rem; max-width: 1280px;}
  /* hide ONLY the Deploy button; the three-dots menu (Settings, theme, Rerun...) stays */
  .stAppDeployButton, [data-testid="stAppDeployButton"], .stDeployButton {display: none !important;}
  .hero {
    background: linear-gradient(120deg, #7C5CFF 0%, #22D3EE 100%);
    border-radius: 18px; padding: 28px 34px; margin-bottom: 18px;
    box-shadow: 0 10px 30px rgba(124, 92, 255, .25);
  }
  .hero h1 {margin: 0; font-size: 2.1rem; color: #fff; letter-spacing: -0.5px;}
  .hero p {margin: 6px 0 0; color: rgba(255,255,255,.88); font-size: 1.02rem;}
  .kpi {
    background: #141B2E; border: 1px solid rgba(124,92,255,.35); border-radius: 14px;
    padding: 16px 18px; height: 100%;
  }
  .kpi-label {font-size: .78rem; text-transform: uppercase; letter-spacing: .08em; color: #8E97B5;}
  .kpi-value {font-size: 1.85rem; font-weight: 700; color: #F4F6FF; margin-top: 2px;}
  .kpi-sub {font-size: .78rem; color: #34D399; min-height: 1.1rem;}
  .footer {text-align: center; color: #6B7391; font-size: .8rem; margin-top: 36px;}
  div[data-testid="stTabs"] button {font-weight: 600;}
</style>
"""


# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------
def database_path() -> Path:
    """Resolve the SQLite path from config.yaml (falls back to data/database.db)."""
    relative = "data/database.db"
    try:
        config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
        relative = config.get("paths", {}).get("database", relative)
    except (OSError, yaml.YAMLError):
        pass
    path = Path(relative)
    return path if path.is_absolute() else ROOT / path


@st.cache_data(ttl=60, show_spinner=False)
def read_sql(db_path: str, query: str) -> pd.DataFrame:
    """Run a read-only query; returns an empty frame if the database or table is missing."""
    if not Path(db_path).exists():
        return pd.DataFrame()
    try:
        with closing(sqlite3.connect(db_path)) as conn:
            return pd.read_sql_query(query, conn)
    except (sqlite3.Error, pd.errors.DatabaseError):
        return pd.DataFrame()


def prepare_jobs(df: pd.DataFrame) -> pd.DataFrame:
    """Type-convert and enrich the raw ``jobs`` table for analysis."""
    jobs = df.copy()
    jobs["remote"] = jobs["remote"].astype(bool)
    jobs["seniority"] = jobs["seniority"].fillna("Not specified")
    jobs["company"] = jobs["company"].fillna("Unknown")
    jobs["location"] = jobs["location"].fillna("Unspecified")
    jobs["skills"] = jobs["skills"].fillna("")
    jobs["posted_date"] = pd.to_datetime(jobs["posted_date"], errors="coerce")
    jobs["first_seen"] = pd.to_datetime(jobs["first_seen"], errors="coerce", utc=True)
    jobs["last_seen"] = pd.to_datetime(jobs["last_seen"], errors="coerce", utc=True)
    jobs["salary_mid"] = (jobs["salary_min"] + jobs["salary_max"]) / 2
    jobs["skill_list"] = jobs["skills"].apply(lambda text: [s.strip() for s in text.split(",") if s.strip()])
    return jobs


def explode_skills(jobs: pd.DataFrame) -> pd.DataFrame:
    """One row per (job, skill) pair."""
    long = jobs[["job_id", "skill_list", "salary_mid", "salary_currency", "seniority"]].explode("skill_list")
    long = long.dropna(subset=["skill_list"]).rename(columns={"skill_list": "skill"})
    return long.reset_index(drop=True)  # exploding repeats index labels; crosstab/groupby need unique ones


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------
def sidebar_filters(jobs: pd.DataFrame) -> pd.DataFrame:
    """Render the sidebar widgets and return the filtered jobs frame."""
    st.sidebar.markdown("## 🎛️ Filters")
    sources = sorted(jobs["source"].unique())
    chosen_sources = st.sidebar.multiselect("Source", sources, default=sources)
    remote_mode = st.sidebar.radio("Work mode", ["All", "Remote only", "On-site / hybrid"])
    seniorities = sorted(jobs["seniority"].unique())
    chosen_levels = st.sidebar.multiselect("Seniority", seniorities, default=seniorities)
    all_skills = sorted({skill for skills in jobs["skill_list"] for skill in skills})
    chosen_skills = st.sidebar.multiselect("Must mention any of these skills", all_skills)
    query = st.sidebar.text_input("Search (title, company, location, skill)").strip().lower()

    mask = jobs["source"].isin(chosen_sources) & jobs["seniority"].isin(chosen_levels)
    if remote_mode == "Remote only":
        mask &= jobs["remote"]
    elif remote_mode == "On-site / hybrid":
        mask &= ~jobs["remote"]
    if chosen_skills:
        wanted = set(chosen_skills)
        mask &= jobs["skill_list"].apply(lambda skills: bool(wanted & set(skills)))
    if query:
        haystack = (jobs["title"] + " " + jobs["company"] + " " + jobs["location"] + " " + jobs["skills"]).str.lower()
        mask &= haystack.str.contains(re.escape(query), regex=True)

    valid_dates = jobs["posted_date"].dropna()
    if not valid_dates.empty:
        low, high = valid_dates.min().date(), valid_dates.max().date()
        picked = st.sidebar.date_input("Posted between", value=(low, high), min_value=low, max_value=high)
        if isinstance(picked, (tuple, list)) and len(picked) == 2:
            start, end = pd.Timestamp(picked[0]), pd.Timestamp(picked[1])
            mask &= jobs["posted_date"].between(start, end) | jobs["posted_date"].isna()

    if st.sidebar.button("🔄 Refresh data"):
        st.cache_data.clear()
        st.rerun()
    return jobs[mask]


# ---------------------------------------------------------------------------
# Chart helpers
# ---------------------------------------------------------------------------
def style_fig(fig, height: int = 380):
    """Apply the shared dark, transparent look to a Plotly figure."""
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=height,
        margin=dict(l=10, r=10, t=55, b=10),
        title_font_size=16,
        colorway=PALETTE,
    )
    fig.update_coloraxes(showscale=False)
    return fig


def kpi_card(label: str, value: str, sub: str = "") -> str:
    """HTML for one KPI tile."""
    return (
        f'<div class="kpi"><div class="kpi-label">{label}</div>'
        f'<div class="kpi-value">{value}</div><div class="kpi-sub">{sub}</div></div>'
    )


def format_money(value: Optional[float]) -> str:
    """Format a salary as e.g. ``$137k`` (or an em dash when unknown)."""
    return "—" if value is None or pd.isna(value) else f"${value / 1000:,.0f}k"


def salary_text(row: pd.Series) -> str:
    """Compact salary range for the table view."""
    if pd.isna(row["salary_min"]) or pd.isna(row["salary_max"]):
        return ""
    return f"{row['salary_currency'] or ''} {row['salary_min'] / 1000:.0f}k–{row['salary_max'] / 1000:.0f}k".strip()


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
def render_kpis(jobs: pd.DataFrame, runs: pd.DataFrame) -> None:
    """Top KPI row: totals, new today, remote share, median salary, last update."""
    today = pd.Timestamp.now(tz="UTC").normalize()
    new_today = int((jobs["first_seen"] >= today).sum())
    remote_share = f"{jobs['remote'].mean() * 100:.0f}%" if len(jobs) else "—"
    median_salary = format_money(jobs["salary_mid"].median()) if jobs["salary_mid"].notna().any() else "—"
    if not runs.empty and runs["finished_at"].notna().any():
        last = pd.to_datetime(runs["finished_at"], errors="coerce", utc=True).max()
    else:
        last = jobs["last_seen"].max()
    last_text = last.strftime("%d %b %Y, %H:%M UTC") if pd.notna(last) else "—"

    cols = st.columns(5)
    tiles = [
        ("Total jobs", f"{len(jobs):,}", "matching filters"),
        ("New today", f"{new_today:,}", "first seen today"),
        ("Remote share", remote_share, "of filtered jobs"),
        ("Median salary", median_salary, "annual, as posted"),
        ("Last update", last_text.split(",")[0], last_text.split(", ")[-1] if "," in last_text else ""),
    ]
    for col, (label, value, sub) in zip(cols, tiles):
        col.markdown(kpi_card(label, value, sub), unsafe_allow_html=True)
    st.write("")


def render_overview(jobs: pd.DataFrame) -> None:
    """Overview tab: work mode, seniority, posting volume, top companies and locations."""
    left, right = st.columns(2)
    mode = jobs["remote"].map({True: "Remote", False: "On-site / hybrid"}).value_counts().rename_axis("mode").reset_index(name="jobs")
    fig = px.pie(mode, names="mode", values="jobs", hole=0.58, title="Remote vs on-site", color_discrete_sequence=PALETTE)
    left.plotly_chart(style_fig(fig), use_container_width=True)

    levels = jobs["seniority"].value_counts().rename_axis("seniority").reset_index(name="jobs")
    fig = px.bar(levels, x="seniority", y="jobs", color="seniority", title="Jobs by seniority", color_discrete_sequence=PALETTE)
    fig.update_layout(showlegend=False)
    right.plotly_chart(style_fig(fig), use_container_width=True)

    dated = jobs.dropna(subset=["posted_date"])
    if not dated.empty:
        weekly = dated.set_index("posted_date").resample("W")["job_id"].count().rename("jobs").reset_index()
        fig = px.area(weekly, x="posted_date", y="jobs", title="Postings per week", color_discrete_sequence=[PALETTE[0]])
        st.plotly_chart(style_fig(fig, 320), use_container_width=True)

    left, right = st.columns(2)
    companies = jobs["company"].value_counts().head(10).rename_axis("company").reset_index(name="jobs")
    fig = px.bar(companies.sort_values("jobs"), x="jobs", y="company", orientation="h", title="Top hiring companies",
                 color="jobs", color_continuous_scale=PURPLE_SCALE)
    left.plotly_chart(style_fig(fig), use_container_width=True)
    places = jobs["location"].value_counts().head(10).rename_axis("location").reset_index(name="jobs")
    fig = px.bar(places.sort_values("jobs"), x="jobs", y="location", orientation="h", title="Top locations",
                 color="jobs", color_continuous_scale=PURPLE_SCALE)
    right.plotly_chart(style_fig(fig), use_container_width=True)


def render_skills(jobs: pd.DataFrame, skill_snaps: pd.DataFrame, market_snaps: pd.DataFrame) -> None:
    """Skills tab: top skills, trend over time, and co-occurrence heatmap."""
    skills_long = explode_skills(jobs)
    if skills_long.empty:
        st.info("No skills extracted for the current filters.")
        return

    top_n = st.slider("Show top N skills", 5, 30, 15)
    counts = skills_long["skill"].value_counts().head(top_n).rename_axis("skill").reset_index(name="jobs")
    fig = px.bar(counts.sort_values("jobs"), x="jobs", y="skill", orientation="h", color="jobs",
                 color_continuous_scale=PURPLE_SCALE, title=f"Top {top_n} in-demand skills")
    st.plotly_chart(style_fig(fig, max(380, 24 * top_n)), use_container_width=True)

    st.subheader("📈 Skill trends over time")
    if skill_snaps.empty or market_snaps.empty or market_snaps["run_id"].nunique() < 2:
        st.info("Trend lines appear after at least two pipeline runs (try `python main.py --demo`).")
    else:
        trend = skill_snaps.merge(market_snaps[["run_id", "total_jobs"]], on="run_id")
        trend["share"] = trend["job_count"] / trend["total_jobs"] * 100
        trend["snapshot_date"] = pd.to_datetime(trend["snapshot_date"])
        latest = trend[trend["run_id"] == trend["run_id"].max()].sort_values("share", ascending=False)
        options = sorted(trend["skill"].unique())
        picked = st.multiselect("Skills to compare", options, default=list(latest["skill"].head(6)))
        shown = trend[trend["skill"].isin(picked)]
        if not shown.empty:
            fig = px.line(shown, x="snapshot_date", y="share", color="skill", markers=True,
                          title="Share of all jobs mentioning each skill (%)", color_discrete_sequence=PALETTE)
            fig.update_layout(yaxis_title="% of jobs", xaxis_title="")
            st.plotly_chart(style_fig(fig, 420), use_container_width=True)

    st.subheader("🔗 Skills that appear together")
    top_skills = skills_long["skill"].value_counts().head(12).index
    subset = skills_long[skills_long["skill"].isin(top_skills)]
    matrix = pd.crosstab(subset["job_id"], subset["skill"])
    cooc = matrix.T.dot(matrix).astype(float)
    values = cooc.to_numpy(dtype=float, copy=True)
    np.fill_diagonal(values, np.nan)
    cooc = pd.DataFrame(values, index=cooc.index, columns=cooc.columns)
    fig = px.imshow(cooc, text_auto=".0f", color_continuous_scale=PURPLE_SCALE, aspect="auto",
                    title="Co-occurrence of the 12 most common skills")
    st.plotly_chart(style_fig(fig, 520), use_container_width=True)


def render_salaries(jobs: pd.DataFrame) -> None:
    """Salary tab: distribution, seniority comparison and best-paid skills."""
    paid = jobs.dropna(subset=["salary_mid"])
    if paid.empty:
        st.info("No salary data for the current filters.")
        return
    currencies = paid["salary_currency"].fillna("Unknown").value_counts().index.tolist()
    currency = st.selectbox("Currency", currencies)
    paid = paid[paid["salary_currency"].fillna("Unknown") == currency]

    left, right = st.columns(2)
    fig = px.histogram(paid, x="salary_mid", nbins=30, title="Salary distribution (midpoint)", color_discrete_sequence=[PALETTE[1]])
    left.plotly_chart(style_fig(fig), use_container_width=True)
    fig = px.box(paid, x="seniority", y="salary_mid", color="seniority", title="Salary by seniority", color_discrete_sequence=PALETTE)
    fig.update_layout(showlegend=False)
    right.plotly_chart(style_fig(fig), use_container_width=True)

    by_skill = explode_skills(paid).groupby("skill")["salary_mid"].agg(["median", "count"]).reset_index()
    by_skill = by_skill[by_skill["count"] >= 5].sort_values("median", ascending=False).head(15)
    if not by_skill.empty:
        fig = px.bar(by_skill.sort_values("median"), x="median", y="skill", orientation="h", color="median",
                     color_continuous_scale=PURPLE_SCALE, title="Median salary by skill (skills with 5+ postings)")
        st.plotly_chart(style_fig(fig, 460), use_container_width=True)


def render_explorer(jobs: pd.DataFrame) -> None:
    """Explorer tab: searchable table with CSV download."""
    table = jobs.copy()
    table["salary"] = table.apply(salary_text, axis=1)
    table["posted"] = table["posted_date"].dt.date
    columns = ["title", "company", "location", "remote", "seniority", "salary", "skills", "posted", "source", "url"]
    table = table[columns].sort_values("posted", ascending=False)
    st.dataframe(
        table,
        use_container_width=True,
        hide_index=True,
        height=520,
        column_config={
            "url": st.column_config.LinkColumn("Link", display_text="Open"),
            "remote": st.column_config.CheckboxColumn("Remote"),
        },
    )
    st.download_button("⬇️ Download filtered data (CSV)", data=table.to_csv(index=False).encode("utf-8"),
                       file_name="skillpulse_jobs.csv", mime="text/csv")


def render_pipeline(runs: pd.DataFrame) -> None:
    """Pipeline tab: history of scraper runs."""
    if runs.empty:
        st.info("No pipeline runs recorded yet.")
        return
    runs = runs.copy()
    runs["started_at"] = pd.to_datetime(runs["started_at"], errors="coerce", utc=True)
    fig = px.bar(runs, x="started_at", y="records_new", color="status", title="New records per run",
                 color_discrete_sequence=PALETTE)
    st.plotly_chart(style_fig(fig, 340), use_container_width=True)
    st.dataframe(runs.sort_values("run_id", ascending=False), use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
def main() -> None:
    """Build the dashboard page."""
    st.set_page_config(page_title="SkillPulse", page_icon="📡", layout="wide")
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)
    st.markdown(
        '<div class="hero"><h1>📡 SkillPulse</h1>'
        "<p>Live tech-job market analytics: skills, salaries and trends extracted from scraped job posts.</p></div>",
        unsafe_allow_html=True,
    )

    db = str(database_path())
    raw_jobs = read_sql(db, "SELECT * FROM jobs")
    if raw_jobs.empty:
        st.warning("No data yet. Fill the database first:")
        st.code("python main.py --demo   # instant synthetic data\npython main.py          # real scrape", language="bash")
        st.stop()

    jobs = prepare_jobs(raw_jobs)
    runs = read_sql(db, "SELECT * FROM runs")
    skill_snaps = read_sql(db, "SELECT * FROM skill_snapshots")
    market_snaps = read_sql(db, "SELECT * FROM market_snapshots")

    filtered = sidebar_filters(jobs)
    if filtered.empty:
        st.warning("No jobs match the current filters.")
        st.stop()
    if (filtered["source"] == "demo").any():
        st.info("Showing synthetic **demo** data (source = demo). Untick it in the sidebar once real data is available.")

    render_kpis(filtered, runs)
    tabs = st.tabs(["📊 Overview", "🧠 Skills", "💰 Salaries", "🗂️ Job explorer", "🛠️ Pipeline"])
    with tabs[0]:
        render_overview(filtered)
    with tabs[1]:
        render_skills(filtered, skill_snaps, market_snaps)
    with tabs[2]:
        render_salaries(filtered)
    with tabs[3]:
        render_explorer(filtered)
    with tabs[4]:
        render_pipeline(runs)

    st.markdown('<div class="footer">SkillPulse · built for the Progree Data Science Internship</div>', unsafe_allow_html=True)


if __name__ == "__main__":
    main()