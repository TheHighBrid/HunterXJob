"""Job-fit scoring: score_job_fit(resume_text, job_description) -> 0..100.

Uses TF-IDF cosine similarity via scikit-learn if it happens to be installed
(it is NOT a hard dependency — keep this module free of a required heavy ML
stack), otherwise falls back to a pure-python weighted keyword/skill overlap
scorer. Either way this never touches the network or needs a downloaded
model, so it always works offline.
"""
from __future__ import annotations

import re
from collections import Counter

_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "then", "of", "to", "in",
    "on", "for", "with", "as", "is", "are", "was", "were", "be", "been",
    "being", "at", "by", "this", "that", "it", "from", "will", "you",
    "your", "we", "our", "they", "their", "have", "has", "had", "not",
    "our", "us", "who", "what", "which", "can", "using", "use", "into",
    "about", "including", "etc", "such", "than", "also", "any", "all",
    "job", "role", "work", "working", "team", "years", "year", "experience",
}

_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+#.]{1,}")


def tokenize(text: str) -> list[str]:
    if not text:
        return []
    tokens = _TOKEN_RE.findall(text.lower())
    return [t for t in tokens if t not in _STOPWORDS and len(t) > 1]


def _pure_python_overlap_score(resume_text: str, job_description: str) -> float:
    """Weighted keyword/skill overlap scorer, no external deps.

    Score reflects: fraction of *distinct* job-description keywords that
    appear in the resume, weighted by each keyword's frequency in the job
    description (keywords mentioned more often in the JD matter more).
    """
    resume_tokens = set(tokenize(resume_text))
    job_tokens = tokenize(job_description)

    if not job_tokens:
        return 0.0

    job_counts = Counter(job_tokens)
    distinct_job_terms = set(job_counts)
    if not distinct_job_terms:
        return 0.0

    total_weight = sum(job_counts.values())
    matched_weight = sum(count for term, count in job_counts.items() if term in resume_tokens)

    # Base overlap ratio (weighted by term frequency in the JD).
    weighted_ratio = matched_weight / total_weight if total_weight else 0.0

    # Bonus for breadth: fraction of distinct JD terms covered, rewards
    # resumes that touch many different requirements rather than repeating
    # one matched buzzword.
    distinct_matched = sum(1 for term in distinct_job_terms if term in resume_tokens)
    breadth_ratio = distinct_matched / len(distinct_job_terms)

    score = 0.6 * weighted_ratio + 0.4 * breadth_ratio
    return round(min(max(score, 0.0), 1.0) * 100, 2)


def _tfidf_cosine_score(resume_text: str, job_description: str) -> float | None:
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
    except ImportError:
        return None

    if not resume_text.strip() or not job_description.strip():
        return 0.0

    vectorizer = TfidfVectorizer(stop_words="english")
    try:
        matrix = vectorizer.fit_transform([resume_text, job_description])
    except ValueError:
        # e.g. only stopwords present
        return None
    similarity = cosine_similarity(matrix[0:1], matrix[1:2])[0][0]
    return round(min(max(similarity, 0.0), 1.0) * 100, 2)


def score_job_fit(resume_text: str, job_description: str) -> float:
    """Return a 0-100 score for how well `resume_text` matches `job_description`.

    Tries a TF-IDF cosine-similarity approach if scikit-learn is available
    (usually a slightly richer signal), and always falls back to a
    dependency-free weighted keyword-overlap scorer otherwise.
    """
    tfidf_score = _tfidf_cosine_score(resume_text, job_description)
    if tfidf_score is not None:
        return tfidf_score
    return _pure_python_overlap_score(resume_text, job_description)
