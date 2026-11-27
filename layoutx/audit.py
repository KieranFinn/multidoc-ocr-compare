import json
import os
import random
import re

HERE = os.path.dirname(os.path.abspath(__file__))


def norm(s):
    return re.sub(r"\s+", "", str(s or "")).lower()


def main():
    records = [json.loads(x) for x in open(os.path.join(HERE, "diff.jsonl"), encoding="utf-8")]
    supersets = []
    for r in records:
        old = r["fields"]["authors"]["old"]
        new = r["fields"]["authors"]["new"]
        old_names = {norm(a.get("name")) for a in old}
        new_names = {norm(a.get("name")) for a in new}
        if old_names < new_names and r["fields"]["authors"].get("assessment") == "new_better":
            supersets.append(r)
    rng = random.Random(20261127)
    sample = rng.sample(supersets, max(1, (len(supersets) + 9) // 10)) if supersets else []
    unresolved = [r for r in records if r["category"] == "双错且不同"]
    payload = {"seed": 20261127, "author_superset_total": len(supersets),
               "author_superset_sample": sample, "unresolved_all": unresolved}
    with open(os.path.join(HERE, "audit_sample.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(json.dumps({"author_superset_total": len(supersets), "sample": [r["doc"] for r in sample], "unresolved": [r["doc"] for r in unresolved]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
