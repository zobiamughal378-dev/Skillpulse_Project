# Resume / LinkedIn descriptions

## 1-line
Built **SkillPulse**, an automated Python pipeline that scrapes job postings, extracts skills and salaries with NLP, stores them in SQLite/CSV and serves a live Streamlit analytics dashboard with email/Telegram alerts.

## 2-line
Designed and built SkillPulse, a scheduled web-scraping and NLP pipeline (requests, BeautifulSoup, spaCy, pydantic, APScheduler) that turns unstructured job posts into clean, validated records in SQLite and CSV.
Delivered an interactive Streamlit + Plotly dashboard (skill trends, salary analysis, co-occurrence heatmap) with retry/backoff, robots.txt compliance, structured logging, a unit-test suite and scheduled runs.

## Paragraph (LinkedIn "Projects" / portfolio)
SkillPulse is an end-to-end data-engineering project I built during my Data Science internship at Progree. It automatically collects job listings from public sources, then extracts structured fields (title, company, location, salary, remote flag, seniority and skills) from unstructured text using regex, fuzzy matching and spaCy NER. Records are cleaned (Unicode and whitespace normalisation, hash-based de-duplication), validated with pydantic and stored in SQLite and CSV together with historical snapshots, so skill demand can be tracked over time. The pipeline runs on a schedule (APScheduler), is resilient (rotating user-agents, exponential-backoff retries, rate limiting, robots.txt checks), logs with loguru, and sends HTML email or Telegram alerts for new matching jobs. A Streamlit + Plotly dashboard presents KPIs, trending skills, salary distributions and skill co-occurrence, and the project ships with a pytest suite.

**Tech:** Python, requests, BeautifulSoup4, spaCy, rapidfuzz, pydantic, SQLite, APScheduler, Streamlit, Plotly, loguru, pytest.

## LinkedIn post caption (Task 1 style)
Excited to share my latest project from the @Progree Data Science internship: **SkillPulse** 📡, an automated pipeline that scrapes job posts, extracts skills & salaries with NLP and shows market trends in a live dashboard. Learned a lot about resilient scraping, data cleaning and NLP-based extraction!
#datascience #python #analytics #progree
