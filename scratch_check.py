import json

with open("ai-service/evaluation/results/new_combined_results.jsonl") as f:
    for line in f:
        d = json.loads(line)
        if d.get("retrieval_recall_hit") is False and d.get("generation_recall_hit") is True:
            print(f"Case {d.get('case_idx')}, Role {d.get('role')}, GT: {d.get('primary_num')}")
            print(f"  Retrieved: {d.get('retrieved_nums')}")
            print(f"  Cited: {d.get('generation_recall_cited')}")
            print(f"  Hallucinated: {d.get('hallucinated')}")
            if d.get("hall_l1_triggered"):
                print(f"  L1 False Articles: {d.get('hall_l1_false_articles')}")
            print()
