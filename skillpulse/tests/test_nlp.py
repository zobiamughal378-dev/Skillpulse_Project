"""Tests for src/nlp_extractor.py: skills, salary, remote flag and seniority extraction."""

import yaml

from src.nlp_extractor import NLPExtractor, detect_remote, detect_seniority, extract_salary
from src.utils import PROJECT_ROOT

TAXONOMY = yaml.safe_load((PROJECT_ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))["skills"]
EXTRACTOR = NLPExtractor(TAXONOMY, use_spacy=False, fuzzy_enabled=True, fuzzy_threshold=90)


def test_skills_use_word_boundaries_and_aliases():
    assert EXTRACTOR.extract_skills("We use JavaScript and Node.js") == ["JavaScript", "Node.js"]
    assert "Java" not in EXTRACTOR.extract_skills("Strong JavaScript skills")
    assert EXTRACTOR.extract_skills("postgres, k8s and GitHub Actions") == ["PostgreSQL", "Kubernetes", "GitHub Actions"]
    assert EXTRACTOR.extract_skills("C++ or C# experience, plus .NET") == ["C++", "C#", ".NET"]
    assert "Git" not in EXTRACTOR.extract_skills("GitHub profile required")


def test_ambiguous_words_need_exact_case():
    assert "Rust" in EXTRACTOR.extract_skills("Backend in Rust")
    assert "Swift" not in EXTRACTOR.extract_skills("we value swift delivery")
    assert "Go" in EXTRACTOR.extract_skills("Go, Python and SQL")
    assert "Go" not in EXTRACTOR.extract_skills("let's go build things")


def test_fuzzy_matching_catches_typos_when_available():
    skills = EXTRACTOR.extract_skills("Experience with Kubernetess and Terraformm")
    assert "Kubernetes" in skills and "Terraform" in skills


def test_salary_extraction_handles_common_formats():
    assert extract_salary("Pay: $140k-$170k") == (140000.0, 170000.0, "USD")
    assert extract_salary("$120,000 - $150,000 per year") == (120000.0, 150000.0, "USD")
    assert extract_salary("€60k – 80k") == (60000.0, 80000.0, "EUR")
    assert extract_salary("120-150k USD") == (120000.0, 150000.0, "USD")
    assert extract_salary("up to $150k+") == (150000.0, 150000.0, "USD")


def test_salary_extraction_ignores_hourly_rates_and_noise():
    assert extract_salary("$50-$60 per hour") == (None, None, None)
    assert extract_salary("We raised $200 million in 2024") == (None, None, None)
    assert extract_salary("") == (None, None, None)


def test_remote_and_seniority_detection():
    assert detect_remote("Acme | Engineer | REMOTE (US)") is True
    assert detect_remote("Onsite in Berlin, no remote") is False
    assert detect_remote("Office-based role in Austin") is False
    assert detect_seniority("Senior Backend Engineer") == "Senior"
    assert detect_seniority("Engineering Manager") == "Manager"
    assert detect_seniority("Junior Data Analyst") == "Junior"
    assert detect_seniority("Data Analyst") is None


def test_enrich_adds_all_fields():
    record = {"title": "Senior ML Engineer", "location": "Berlin", "company": "Acme",
              "description": "Acme | Senior ML Engineer | Berlin | REMOTE | $150k-$180k\nStack: Python, PyTorch, Docker."}
    out = EXTRACTOR.enrich(record)
    assert set(out["skills"]) >= {"Python", "PyTorch", "Docker", "Machine Learning"}
    assert (out["salary_min"], out["salary_max"], out["salary_currency"]) == (150000.0, 180000.0, "USD")
    assert out["remote"] is True and out["seniority"] == "Senior"
