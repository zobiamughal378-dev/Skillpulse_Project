"""NLP-style field extraction: skills, salary, remote flag, seniority and named entities.

Skills use boundary-aware regex matching over a configurable taxonomy plus optional fuzzy
matching (rapidfuzz) for typos. Entities use spaCy NER when a model is installed and degrade
gracefully to regex-only otherwise.
"""

import re
from typing import Any, Optional

from loguru import logger

try:
    from rapidfuzz import fuzz, process
except ImportError:  # pragma: no cover - optional dependency
    fuzz = None  # type: ignore[assignment]
    process = None  # type: ignore[assignment]

# Aliases that are also everyday English words are matched case-sensitively
# (an ALL-CAPS variant is accepted too, since HN headers are often shouted).
CASE_SENSITIVE_ALIASES = {"Go", "Swift", "Rust", "Ruby", "Excel", "Spark", "Flask", "Rails"}

MIN_SALARY = 20_000
MAX_SALARY = 1_000_000
CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP"}

_AMOUNT = r"\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?"
_SEP = r"\s*(?:-|–|—|to)\s*"
_RANGE_SYMBOL = re.compile(
    rf"(?P<cur>[$€£])\s?(?P<lo>{_AMOUNT})\s?(?P<lok>[kK])?{_SEP}(?:[$€£]\s?)?(?P<hi>{_AMOUNT})\s?(?P<hik>[kK])?(?![\d,])"
)
_RANGE_K = re.compile(
    rf"(?P<lo>\d{{2,3}})\s?(?P<lok>[kK])?{_SEP}(?P<hi>\d{{2,3}})\s?(?P<hik>[kK])\b\s*(?P<code>USD|EUR|GBP|CAD|AUD)?",
    re.IGNORECASE,
)
_SINGLE_SYMBOL = re.compile(rf"(?P<cur>[$€£])\s?(?P<lo>{_AMOUNT})\s?(?P<lok>[kK])?")
_HOURLY_TAIL = re.compile(r"^\s*(/|per\s+|an\s+)?\s*(hr|hour|h)\b", re.IGNORECASE)

_REMOTE_POSITIVE = re.compile(
    r"\b(remote|work(?:ing)? from home|wfh|fully distributed|distributed team|work from anywhere)\b",
    re.IGNORECASE,
)
_REMOTE_NEGATIVE = re.compile(
    r"\b(?:no|not|non|without)[\s-]+(?:fully[\s-]+)?remote\b"
    r"|\bremote\s+(?:is\s+)?not\s+(?:available|possible|an option)\b"
    r"|\bon-?site\s+only\b",
    re.IGNORECASE,
)

_SENIORITY_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("Intern", re.compile(r"\bintern(?:ship)?\b", re.IGNORECASE)),
    ("Junior", re.compile(r"\b(?:junior|jr\.?|entry[- ]level|graduate|trainee)\b", re.IGNORECASE)),
    ("Manager", re.compile(r"\b(?:manager|director|vp|chief|cto)\b", re.IGNORECASE)),
    ("Lead", re.compile(r"\b(?:lead|principal|staff|architect|head of)\b", re.IGNORECASE)),
    ("Senior", re.compile(r"\b(?:senior|sr\.?)\b", re.IGNORECASE)),
]


# ---------------------------------------------------------------------------
# Stand-alone extractors
# ---------------------------------------------------------------------------
def _to_amount(number: str, k_suffix: Optional[str]) -> float:
    """Convert '140' + 'k' -> 140000.0 and '120,000' -> 120000.0."""
    value = float(number.replace(",", ""))
    return value * 1000 if k_suffix else value


def _plausible(low: float, high: float) -> bool:
    """True when both ends look like an annual salary."""
    return MIN_SALARY <= low <= MAX_SALARY and MIN_SALARY <= high <= MAX_SALARY


def _is_hourly(text: str, end: int) -> bool:
    """True when the text right after a match says 'per hour' / '/hr'."""
    return bool(_HOURLY_TAIL.match(text[end : end + 12]))


def extract_salary(text: str) -> tuple[Optional[float], Optional[float], Optional[str]]:
    """Find an annual salary range in free text.

    Handles ``$120k-$150k``, ``$120,000 - $150,000``, ``€60k-80k``, ``120-150k USD`` and a
    single figure such as ``$150k+``. Hourly rates and implausible values are ignored.

    Returns:
        ``(min, max, currency)`` with ``None`` for anything not found.
    """
    if not text:
        return None, None, None

    for match in _RANGE_SYMBOL.finditer(text):
        low = _to_amount(match["lo"], match["lok"])
        high = _to_amount(match["hi"], match["hik"])
        if match["hik"] and not match["lok"] and low < 1000:
            low *= 1000  # "120-150k" -> both in thousands
        low, high = sorted((low, high))
        if _plausible(low, high) and not _is_hourly(text, match.end()):
            return low, high, CURRENCY_SYMBOLS[match["cur"]]

    for match in _RANGE_K.finditer(text):
        low = _to_amount(match["lo"], match["lok"] or match["hik"])
        high = _to_amount(match["hi"], match["hik"])
        low, high = sorted((low, high))
        if _plausible(low, high):
            code = match["code"].upper() if match["code"] else None
            return low, high, code

    for match in _SINGLE_SYMBOL.finditer(text):
        value = _to_amount(match["lo"], match["lok"])
        if _plausible(value, value) and not _is_hourly(text, match.end()):
            return value, value, CURRENCY_SYMBOLS[match["cur"]]

    return None, None, None


def detect_remote(text: str) -> bool:
    """True if the text offers remote work (explicit negations such as 'no remote' are ignored)."""
    cleaned = _REMOTE_NEGATIVE.sub(" ", text or "")
    return bool(_REMOTE_POSITIVE.search(cleaned))


def detect_seniority(title: str) -> Optional[str]:
    """Infer seniority (Intern/Junior/Manager/Lead/Senior) from a job title, else ``None``."""
    for label, pattern in _SENIORITY_RULES:
        if pattern.search(title or ""):
            return label
    return None


# ---------------------------------------------------------------------------
# Extractor class
# ---------------------------------------------------------------------------
class NLPExtractor:
    """Enrich cleaned job dictionaries with skills, salary, remote flag, seniority and entities."""

    def __init__(
        self,
        taxonomy: dict[str, list[str]],
        use_spacy: bool = False,
        spacy_model: str = "en_core_web_sm",
        fuzzy_enabled: bool = True,
        fuzzy_threshold: int = 90,
    ) -> None:
        self._skill_order = list(taxonomy)
        self._patterns = self._compile_patterns(taxonomy)
        self._fuzzy_threshold = fuzzy_threshold
        self._fuzzy_enabled = fuzzy_enabled and fuzz is not None
        if fuzzy_enabled and fuzz is None:
            logger.warning("rapidfuzz not installed; fuzzy skill matching disabled")
        self._fuzzy_lookup = self._build_fuzzy_lookup(taxonomy)
        self._nlp: Any = self._load_spacy(spacy_model) if use_spacy else None

    # ----------------------------------------------------------------- set-up
    @staticmethod
    def _alias_pattern(alias: str) -> re.Pattern[str]:
        """Compile a boundary-aware regex for one alias (``Java`` must not hit ``JavaScript``)."""
        def body(word: str) -> str:
            return r"\s+".join(re.escape(token) for token in word.split())

        if alias in CASE_SENSITIVE_ALIASES:
            core = f"(?:{body(alias)}|{body(alias.upper())})"
            flags = 0
        else:
            core = body(alias)
            flags = re.IGNORECASE
        return re.compile(rf"(?<![A-Za-z0-9_+#.]){core}(?![A-Za-z0-9_+#]|\.[A-Za-z0-9])", flags)

    def _compile_patterns(self, taxonomy: dict[str, list[str]]) -> list[tuple[str, list[re.Pattern[str]]]]:
        compiled = []
        for skill, aliases in taxonomy.items():
            names = list(dict.fromkeys([skill, *aliases]))
            compiled.append((skill, [self._alias_pattern(name) for name in names]))
        return compiled

    @staticmethod
    def _build_fuzzy_lookup(taxonomy: dict[str, list[str]]) -> dict[str, str]:
        """Map lowercase single-word aliases (6+ letters) to their canonical skill."""
        lookup: dict[str, str] = {}
        for skill, aliases in taxonomy.items():
            for name in [skill, *aliases]:
                if re.fullmatch(r"[A-Za-z0-9]{6,}", name):
                    lookup[name.lower()] = skill
        return lookup

    @staticmethod
    def _load_spacy(model_name: str) -> Any:
        """Load a spaCy model for NER, or return ``None`` if spaCy / the model is missing."""
        try:
            import spacy

            nlp = spacy.load(model_name)
            for component in ("parser", "lemmatizer", "attribute_ruler", "tagger"):
                if component in nlp.pipe_names:
                    nlp.disable_pipe(component)  # only NER is needed; keep it fast
            logger.info("spaCy model '{}' loaded", model_name)
            return nlp
        except (ImportError, OSError) as exc:
            logger.warning("spaCy NER unavailable ({}); continuing with regex-only extraction", exc)
            return None

    # ------------------------------------------------------------- extraction
    def extract_skills(self, text: str) -> list[str]:
        """Return the canonical skills mentioned in ``text`` (taxonomy order, no duplicates)."""
        if not text:
            return []
        found = {
            skill
            for skill, patterns in self._patterns
            if any(pattern.search(text) for pattern in patterns)
        }
        if self._fuzzy_enabled:
            found |= self._fuzzy_skills(text, found)
        return [skill for skill in self._skill_order if skill in found]

    def _fuzzy_skills(self, text: str, already_found: set[str]) -> set[str]:
        """Catch misspelled long skill names (e.g. 'Kuberentes') with rapidfuzz."""
        matches: set[str] = set()
        choices = list(self._fuzzy_lookup)
        for token in set(re.findall(r"[A-Za-z][A-Za-z0-9]{5,}", text.lower())):
            if token in self._fuzzy_lookup:
                continue
            hit = process.extractOne(token, choices, scorer=fuzz.ratio, score_cutoff=self._fuzzy_threshold)
            if hit:
                skill = self._fuzzy_lookup[hit[0]]
                if skill not in already_found:
                    matches.add(skill)
        return matches

    def extract_entities(self, text: str) -> dict[str, list[str]]:
        """Return organisations and places found by spaCy NER (empty lists without a model)."""
        result: dict[str, list[str]] = {"organizations": [], "locations": []}
        if self._nlp is None or not text:
            return result
        for ent in self._nlp(text).ents:
            bucket = "organizations" if ent.label_ == "ORG" else "locations" if ent.label_ == "GPE" else None
            if bucket and ent.text not in result[bucket]:
                result[bucket].append(ent.text)
        return result

    def enrich(self, record: dict[str, Any]) -> dict[str, Any]:
        """Return a copy of ``record`` with skills, salary, remote, seniority (and filled gaps)."""
        title = record.get("title") or ""
        description = record.get("description") or ""
        location = record.get("location") or ""
        text = f"{title}\n{description}"

        enriched = dict(record)
        enriched["skills"] = self.extract_skills(text)
        low, high, currency = extract_salary(text)
        enriched.update(salary_min=low, salary_max=high, salary_currency=currency)
        # Remote is judged on the header area only, so "we help remote teams" deep in the body is ignored.
        enriched["remote"] = detect_remote(f"{title}\n{location}\n{description[:400]}")
        enriched["seniority"] = detect_seniority(title)

        if self._nlp is not None and (not record.get("company") or not record.get("location")):
            entities = self.extract_entities(text[:600])
            if not record.get("company") and entities["organizations"]:
                enriched["company"] = entities["organizations"][0]
            if not record.get("location") and entities["locations"]:
                enriched["location"] = entities["locations"][0]
        return enriched

    def enrich_many(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Enrich a batch of records."""
        enriched = [self.enrich(record) for record in records]
        with_skills = sum(1 for rec in enriched if rec["skills"])
        logger.info("NLP: {} records enriched, {} with at least one skill", len(enriched), with_skills)
        return enriched
