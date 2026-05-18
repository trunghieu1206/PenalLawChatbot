# how to deploy overnight
## ssh into server
ssh root@74.81.39.6 -p 10000

## create tmux session
tmux new -s deploy

## detach from the session

# open following ports on server
22,80,8000,8080,7681

# upload scripts + .env template + DB backup ───────────────────────
# Run these from your LOCAL machine (inside PenalLawChatbot/ directory):
scp -P 2692 scripts/setup_server.sh scripts/deploy.sh scripts/deploy_nodocker.sh scripts/backup_database.sh scripts/restore_database.sh root@n3.ckey.vn:/root/
scp -P 2692 .env.example root@n3.ckey.vn:/root/.env.example

# ── ON SERVER: Run the base setup first ──────────────────────────────────────
chmod +x setup_server.sh deploy_nodocker.sh restore_database.sh
bash setup_server.sh

# ── ON LOCAL: Upload backups and datasets ────────────────────────────────────
# Run these from your LOCAL machine AFTER setup_server.sh has finished!
ssh -p 2692 root@n3.ckey.vn "mkdir -p ~/PenalLawChatbot/database/backups ~/PenalLawChatbot/ai-service/scraped_datasets"

scp -P 2692 ~/Desktop/Projects/PenalLawChatbot/database/backups/penallaw_backup_20260505_150435.sql \
    root@n3.ckey.vn:~/PenalLawChatbot/database/backups/

scp -P 2692 ai-service/scraped_datasets/thesis_eval_1000.json \
    root@n3.ckey.vn:~/PenalLawChatbot/ai-service/scraped_datasets/

# ── ON SERVER: Finish deployment ─────────────────────────────────────────────
# Back on your server terminal:
bash restore_database.sh
bash deploy_nodocker.sh

# how to create backup db file and download to local
## create backup on server
cd ~/PenalLawChatbot
mkdir -p ./database/backups
cd ~
chmod 777 ./PenalLawChatbot/database/backups
./backup_database.sh

## download backup to local
scp -P 10000 'root@74.81.39.6:~/PenalLawChatbot/database/backups/penallaw_backup_*.sql' ~/Desktop/Projects/PenalLawChatbot/database/backups/

## verify 
ls -lh ~/PenalLawChatbot/database/backups/

# monitor GPU usage
watch -n 1 nvidia-smi

# server specs
CUDA 12.4.1 Ubuntu 22.04

# ── EVALUATION ────────────────────────────────────────────────────────────────
# Run FROM the server inside ~/PenalLawChatbot/ (ai-service must be running)
# Results save to: ~/PenalLawChatbot/ai-service/evaluation/results/
#
# Files produced:
#   hallucination_results.jsonl      ← per-case L1–L4 scores
#   hallucination_summary.json       ← overall hallucination rate
#   primary_recall_results.jsonl     ← per-case hit/miss
#   primary_recall_summary.json      ← overall recall@primary article
#   role_adherence_results.jsonl     ← per-case role scores
#   role_adherence_summary.json      ← overall role adherence
#   rubric_neutral_results.jsonl     ← per-case rubric scores (judge role)
#   rubric_neutral_summary.json
#   rubric_defense_results.jsonl     ← per-case rubric scores (defense role)
#   rubric_defense_summary.json
#   rubric_victim_results.jsonl      ← per-case rubric scores (victim role)
#   rubric_victim_summary.json

## Step 1 — cd into project
cd ~/PenalLawChatbot

## Step 2 — run each script (use tmux so it survives disconnect)
tmux new -s eval   # or attach: tmux attach -t eval

# Primary Article Recall (no LLM cost — fast)
python3 ai-service/evaluation/eval_primary_recall.py \
  --log-file ai-service/logs/eval_primary_recall.txt

# Role Adherence — Layer A only (no LLM cost — fast)
python3 ai-service/evaluation/eval_role_adherence.py \
  --skip-llm \
  --log-file ai-service/logs/eval_role_adherence.txt

# Role Adherence — with LLM judge (costs API credits)
python3 ai-service/evaluation/eval_role_adherence.py \
  --log-file ai-service/logs/eval_role_adherence.txt

# Hallucination — skip L4 (no LLM cost)
python3 ai-service/evaluation/eval_hallucination.py \
  --skip-l4 \
  --log-file ai-service/logs/eval_hallucination.txt

# Hallucination — full 4-layer (costs API credits for L4 judge)
python3 ai-service/evaluation/eval_hallucination.py \
  --log-file ai-service/logs/eval_hallucination.txt

# Rubric — Neutral / Judge role
python3 ai-service/evaluation/eval_rubric_neutral.py \
  --log-file ai-service/logs/eval_rubric_neutral.txt

# Rubric — Defense role
python3 ai-service/evaluation/eval_rubric_defense.py \
  --log-file ai-service/logs/eval_rubric_defense.txt

# Rubric — Victim role
python3 ai-service/evaluation/eval_rubric_victim.py \
  --log-file ai-service/logs/eval_rubric_victim.txt

## Step 3 — download ALL results to local machine
## Run these from your LOCAL machine (inside ~/Desktop/Projects/PenalLawChatbot/)

# Download result JSONs and JSONL files
scp -P 1927 -r \
  'root@n3.ckey.vn:~/PenalLawChatbot/ai-service/evaluation/results/' \
  ~/Desktop/Projects/PenalLawChatbot/ai-service/evaluation/

# Download log .txt files
scp -P 1927 \
  'root@n3.ckey.vn:~/PenalLawChatbot/ai-service/logs/eval_*.txt' \
  ~/Desktop/Projects/PenalLawChatbot/ai-service/logs/

## (alt server — if using 74.81.39.6:10000 instead of n3.ckey.vn:1927)
scp -P 10000 -r \
  'root@74.81.39.6:~/PenalLawChatbot/ai-service/evaluation/results/' \
  ~/Desktop/Projects/PenalLawChatbot/ai-service/evaluation/

scp -P 10000 \
  'root@74.81.39.6:~/PenalLawChatbot/ai-service/logs/eval_*.txt' \
  ~/Desktop/Projects/PenalLawChatbot/ai-service/logs/

## Step 4 — resume an interrupted eval (e.g. primary_recall crashed at case 200)
python3 ai-service/evaluation/eval_primary_recall.py \
  --resume \
  --log-file ai-service/logs/eval_primary_recall.txt