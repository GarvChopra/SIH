"""Measure overlap-detection precision/recall on the labelled pairs.

    python -m scripts.eval_overlap            # lexical (always) + embeddings if COHERE_API_KEY is set

Prints precision, recall and F1 for each candidate threshold so the constants in
domain/constants.py can be chosen from data rather than guessed. "Positive" means
the pair is labelled as overlapping; a prediction is positive when score >= threshold.
The labelled set is small and synthetic (tests/data/overlap_pairs.json), so treat
the numbers as a sanity check, not as a claim about real AICTE complaints.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "tests", "data", "overlap_pairs.json")


def load():
    with open(DATA, encoding="utf-8") as fh:
        return json.load(fh)["pairs"]


def sweep(scores, labels, grid):
    rows = []
    for t in grid:
        tp = sum(1 for s, y in zip(scores, labels) if s >= t and y)
        fp = sum(1 for s, y in zip(scores, labels) if s >= t and not y)
        fn = sum(1 for s, y in zip(scores, labels) if s < t and y)
        p = tp / (tp + fp) if tp + fp else 1.0
        r = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * p * r / (p + r) if p + r else 0.0
        rows.append((t, p, r, f1, tp, fp, fn))
    return rows


def report(name, scores, labels, grid):
    pos = sum(labels)
    print(f"\n== {name}: {len(labels)} pairs ({pos} overlap, {len(labels) - pos} not) ==")
    print("thr    precision  recall   F1     TP  FP  FN")
    best = None
    for t, p, r, f1, tp, fp, fn in sweep(scores, labels, grid):
        print(f"{t:0.2f}   {p:8.2f}  {r:6.2f}  {f1:5.2f}  {tp:3d} {fp:3d} {fn:3d}")
        if best is None or f1 > best[1] + 1e-9:
            best = (t, f1, p, r)
    print(f"best F1 {best[1]:.2f} at {best[0]:.2f} (precision {best[2]:.2f}, recall {best[3]:.2f})")
    return best


def main() -> int:
    from services import embedding_service, lexical
    pairs = load()
    labels = [bool(p["overlap"]) for p in pairs]

    lex_scores = [lexical.similarity(p["a"], p["b"])[0] for p in pairs]
    report("lexical (offline fallback)", lex_scores, labels, [x / 100 for x in range(10, 85, 5)])

    if embedding_service.available():
        sem = []
        for p in pairs:
            va, vb = embedding_service.embed(p["a"]), embedding_service.embed(p["b"])
            sem.append(embedding_service.cosine(va, vb) if va and vb else 0.0)
        report("Cohere embed-v4.0", sem, labels, [x / 100 for x in range(40, 96, 5)])
    elif os.environ.get("GROQ_API_KEY") or __import__("config").Config.GROQ_API_KEY:
        from services import llm_similarity
        llm = []
        for p in pairs:
            v = llm_similarity.judge(p["a"], p["b"])
            llm.append(v["score"] if v else 0.0)
        report("Groq LLM judge", llm, labels, [x / 100 for x in range(30, 96, 5)])
    else:
        print("\n(no COHERE_API_KEY or GROQ_API_KEY: AI modes not measured; their thresholds "
              "are unvalidated starting values)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
