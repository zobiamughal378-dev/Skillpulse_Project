"""Synthetic job postings for the demo mode (``python main.py --demo``).

Everything here is fictional: company names are invented and URLs point to example.com.
Records are labelled ``source = "demo"`` so they are easy to filter out in the dashboard.
The skill mix drifts week by week so the trend charts have something interesting to show.
"""

import random
from datetime import datetime, timedelta
from typing import Any

COMPANIES = [
    "Nimbus Labs", "Quantix", "Helio Systems", "Brightwave", "Cobalt & Co", "Lumen Analytics",
    "Orbitly", "Pixelforge", "Verdant AI", "Tidewater Data", "Skyline Robotics", "Mintleaf",
    "Aurora Cloud", "Kestrel Works", "Polaris Health", "Fjord Software", "Sundial Labs", "Neonbyte",
]
LOCATIONS = [
    "Remote", "Remote (EU)", "Remote (US)", "Amsterdam, Netherlands", "Berlin, Germany",
    "London, UK", "New York, NY", "San Francisco, CA", "Austin, TX", "Toronto, Canada",
    "Bangalore, India", "Singapore", "Lisbon, Portugal",
]
ROLES = [
    ("Backend Engineer", ["Python", "Django", "PostgreSQL", "Docker", "AWS", "REST API", "Redis"]),
    ("Data Engineer", ["Python", "SQL", "Spark", "Airflow", "Snowflake", "dbt", "Kafka", "AWS"]),
    ("Data Scientist", ["Python", "Pandas", "scikit-learn", "SQL", "Machine Learning", "NumPy", "Tableau"]),
    ("Machine Learning Engineer", ["Python", "PyTorch", "TensorFlow", "MLOps", "Docker", "Kubernetes", "AWS"]),
    ("AI Engineer", ["Python", "LLM", "LangChain", "RAG", "Generative AI", "Hugging Face", "FastAPI"]),
    ("Frontend Engineer", ["JavaScript", "TypeScript", "React", "Next.js", "Tailwind CSS", "GraphQL"]),
    ("Full Stack Developer", ["TypeScript", "React", "Node.js", "PostgreSQL", "Docker", "GraphQL"]),
    ("DevOps Engineer", ["Kubernetes", "Terraform", "AWS", "Docker", "Linux", "Ansible", "Prometheus", "Grafana"]),
    ("Analytics Engineer", ["SQL", "dbt", "Snowflake", "BigQuery", "Looker", "Python"]),
    ("Systems Engineer", ["Rust", "Go", "Linux", "C++", "Kubernetes"]),
]
SENIORITY = [("Junior", 0.15, (70, 95)), ("", 0.30, (95, 130)), ("Senior", 0.40, (130, 175)), ("Lead", 0.15, (160, 210))]

# Skills whose popularity rises (>0) or falls (<0) across the demo period.
TREND_SLOPE = {
    "LLM": 2.4, "LangChain": 2.0, "RAG": 2.2, "Generative AI": 2.4, "Rust": 0.8, "dbt": 0.7,
    "Kubernetes": 0.4, "FastAPI": 0.6, "jQuery": -0.8, "Hadoop": -0.8, "PHP": -0.5,
}
EXTRA_SKILLS = ["Git", "CI/CD", "Microservices", "Elasticsearch", "MongoDB", "MySQL", "Selenium", "Web Scraping"]


def _pick_skills(role_skills: list[str], progress: float, rng: random.Random) -> list[str]:
    """Choose 4-7 skills, boosting or dampening them according to the weekly trend."""
    rising = [s for s, slope in TREND_SLOPE.items() if slope > 1.5 and rng.random() < progress * 0.5]
    fading = [s for s, slope in TREND_SLOPE.items() if slope < 0 and rng.random() < (1 - progress) * 0.5]
    pool = role_skills + rng.sample(EXTRA_SKILLS, k=2) + rising + fading
    weights = [max(0.1, 1.0 + TREND_SLOPE.get(skill, 0.0) * (progress - 0.4)) for skill in pool]
    chosen: list[str] = []
    target = rng.randint(4, 7)
    while len(chosen) < min(target, len(set(pool))):
        skill = rng.choices(pool, weights=weights, k=1)[0]
        if skill not in chosen:
            chosen.append(skill)
    return chosen


def generate_demo_batch(
    week_index: int, total_weeks: int, count: int, scraped_at: datetime, seed: int = 7
) -> list[dict[str, Any]]:
    """Create ``count`` raw job dictionaries for one simulated weekly scrape."""
    rng = random.Random(seed * 1000 + week_index)
    progress = week_index / max(1, total_weeks - 1)
    records: list[dict[str, Any]] = []
    for number in range(count):
        role, role_skills = rng.choice(ROLES)
        level, _, (low, high) = rng.choices(SENIORITY, weights=[s[1] for s in SENIORITY], k=1)[0]
        title = f"{level} {role}".strip()
        company = rng.choice(COMPANIES)
        location = rng.choice(LOCATIONS)
        skills = _pick_skills(role_skills, progress, rng)
        low_k = low + rng.randint(-8, 8)
        high_k = max(low_k + 10, high + rng.randint(-8, 12))
        remote_word = "REMOTE" if location.startswith("Remote") else rng.choice(["ONSITE", "HYBRID"])
        description = (
            f"{company} | {title} | {location} | {remote_word} | ${low_k}k-${high_k}k\n"
            f"{company} is hiring a {title}. You will build and ship production systems with a small, "
            f"friendly team. Tech stack: {', '.join(skills)}.\n"
            "We offer flexible hours, learning budget and a transparent hiring process."
        )
        records.append(
            {
                "source": "demo",
                "title": title,
                "company": company,
                "location": location,
                "posted": (scraped_at - timedelta(days=rng.randint(0, 6))).date().isoformat(),
                "url": f"https://example.com/jobs/demo-w{week_index}-{number}",
                "description": description,
            }
        )
    return records
