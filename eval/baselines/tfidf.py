"""TF-IDF keyword-only job matcher（ER-3 baseline）。

純字面統計、零語意理解——正是 hybrid matcher 要對比的對照組。設計對齊：
- **文字基底與正式系統相同**：resume 文本用 production 的
  ``build_resume_embedding_texts``、job 文本用 ``chunk_job`` 的 chunk 內容
  ——兩個系統看同樣的資訊，差異只剩「怎麼比對」。
- **per-scenario fit**：vectorizer 只在單一情境的 (resume + 8 jobs) 語料上
  fit，杜絕跨情境詞彙表洩漏。
- 完全確定性、離線、零 API 成本。
"""

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel


def rank_jobs_tfidf(resume_doc: str, job_docs: dict[str, str]) -> list[str]:
    """以 TF-IDF cosine 相似度排序 job_key（高到低）；同分保持插入順序（穩定）。"""
    keys = list(job_docs)
    vectorizer = TfidfVectorizer(
        lowercase=True,
        stop_words="english",
        ngram_range=(1, 2),
        sublinear_tf=True,
    )
    matrix = vectorizer.fit_transform([resume_doc, *(job_docs[k] for k in keys)])
    similarities = linear_kernel(matrix[0:1], matrix[1:]).ravel()
    order = sorted(range(len(keys)), key=lambda i: (-similarities[i], i))
    return [keys[i] for i in order]
