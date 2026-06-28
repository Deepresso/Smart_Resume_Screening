import re
import fitz  # PyMuPDF
from docx import Document
from rapidfuzz import fuzz
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

_st_model = None

def _get_st_model():
    global _st_model
    if _st_model is None:
        from sentence_transformers import SentenceTransformer
        _st_model = SentenceTransformer('all-MiniLM-L6-v2')
    return _st_model


# ── Text Extraction ──────────────────────────────────────────────────────────

def extract_text(filepath):
    if filepath.lower().endswith('.pdf'):
        return _from_pdf(filepath)
    elif filepath.lower().endswith('.docx'):
        return _from_docx(filepath)
    return ''


def _from_pdf(filepath):
    text = ''
    with fitz.open(filepath) as doc:
        for page in doc:
            text += page.get_text()
    return text.strip()


def _from_docx(filepath):
    doc = Document(filepath)
    return '\n'.join(p.text for p in doc.paragraphs if p.text.strip()).strip()


# ── NLP Scoring ──────────────────────────────────────────────────────────────

def keyword_score(resume_text, keywords):
    """Percentage of job keywords found in resume (case-insensitive)."""
    if not keywords:
        return 0.0
    resume_lower = resume_text.lower()
    matched = sum(1 for kw in keywords if kw.lower() in resume_lower)
    return round((matched / len(keywords)) * 100, 2)


def fuzzy_score(resume_text, keywords):
    """Average best fuzzy match score for each keyword against resume words."""
    if not keywords:
        return 0.0
    resume_words = re.findall(r'\b\w+\b', resume_text.lower())
    scores = []
    for kw in keywords:
        best = max((fuzz.ratio(kw.lower(), word) for word in resume_words), default=0)
        scores.append(best)
    return round(sum(scores) / len(scores), 2)


def similarity_score(resume_text, job_description):
    """TF-IDF cosine similarity between resume and job description."""
    if not resume_text or not job_description:
        return 0.0
    vectorizer = TfidfVectorizer(stop_words='english')
    try:
        tfidf = vectorizer.fit_transform([resume_text, job_description])
        score = cosine_similarity(tfidf[0:1], tfidf[1:2])[0][0]
        return round(float(score) * 100, 2)
    except Exception:
        return 0.0


def semantic_score(resume_text, job_description):
    """BERT semantic similarity using sentence embeddings (all-MiniLM-L6-v2)."""
    if not resume_text or not job_description:
        return 0.0
    try:
        from sentence_transformers import util
        model = _get_st_model()
        embeddings = model.encode([resume_text, job_description], convert_to_tensor=True)
        score = util.cos_sim(embeddings[0], embeddings[1]).item()
        return round(max(0.0, float(score)) * 100, 2)
    except Exception:
        return 0.0


def keyword_breakdown(resume_text, keywords):
    """Per-keyword found/not-found list."""
    resume_lower = resume_text.lower()
    return [{'kw': kw, 'found': kw.lower() in resume_lower} for kw in keywords]


def fuzzy_breakdown(resume_text, keywords):
    """Per-keyword best fuzzy match score (0-100)."""
    resume_words = re.findall(r'\b\w+\b', resume_text.lower())
    result = []
    for kw in keywords:
        best = max((fuzz.ratio(kw.lower(), word) for word in resume_words), default=0)
        result.append({'kw': kw, 'score': round(best, 1)})
    return result


def compute_scores(resume_text, job_description, keywords):
    """Run all four algorithms and return individual + composite scores."""
    kw_list = [k.strip() for k in keywords.split(',') if k.strip()] if keywords else []

    kw  = keyword_score(resume_text, kw_list)
    fz  = fuzzy_score(resume_text, kw_list)
    sim = similarity_score(resume_text, job_description)
    sem = semantic_score(resume_text, job_description)

    # Weighted composite: keyword 30%, fuzzy 25%, TF-IDF 25%, BERT 20%
    composite = round((kw * 0.30) + (fz * 0.25) + (sim * 0.25) + (sem * 0.20), 2)

    return {
        'keyword_score':    kw,
        'fuzzy_score':      fz,
        'similarity_score': sim,
        'semantic_score':   sem,
        'composite_score':  composite,
    }
