# EC2 Deployment Tutorial
# Amazon EC2 — Ubuntu Instance

# ── Key differences vs VPS (n3.ckey.vn) ─────────────────────────────────────
# VPS:  ssh root@n3.ckey.vn -p 1927          → root user, custom port, password auth
# EC2:  ssh -i "chatbot-key.pem" ubuntu@...  → ubuntu user, port 22, PEM key auth
#
# What changes in every command:
#   -P 1927          → (removed — EC2 uses default port 22)
#   -p 1927          → (removed)
#   root@n3.ckey.vn  → ubuntu@ec2-13-239-38-15.ap-southeast-2.compute.amazonaws.com
#   /root/           → /home/ubuntu/
#   Add: -i "chatbot-key.pem"  to every ssh/scp command

# ── EC2 connection details ────────────────────────────────────────────────────
EC2_HOST=ec2-13-239-38-15.ap-southeast-2.compute.amazonaws.com
EC2_USER=ubuntu
EC2_KEY=chatbot-key.pem   # must be in current directory or use full path

# ── Open following ports in EC2 Security Group (AWS console) ─────────────────
# 22    (SSH)
# 80    (HTTP)
# 8000  (AI service)
# 8080  (Backend)
# 7681  (ttyd / web terminal, optional)

# ── SSH into server ───────────────────────────────────────────────────────────
ssh -i "chatbot-key.pem" ubuntu@ec2-13-239-38-15.ap-southeast-2.compute.amazonaws.com

# ── Create tmux session (run after SSH) ───────────────────────────────────────
tmux new -s deploy
# re-attach: tmux attach -t deploy

# ── UPLOAD: scripts + .env + DB backup ───────────────────────────────────────
# Run these from your LOCAL machine (inside ~/Desktop/Projects/PenalLawChatbot/)

# Upload deploy scripts (note: no -P flag, EC2 uses port 22 by default)
scp -i "chatbot-key.pem" \
  scripts/setup_server.sh scripts/deploy.sh scripts/deploy_nodocker.sh \
  scripts/backup_database.sh scripts/restore_database.sh \
  ubuntu@ec2-13-239-38-15.ap-southeast-2.compute.amazonaws.com:/home/ubuntu/

# Upload .env (note: home dir is /home/ubuntu/ not /root/)
scp -i "chatbot-key.pem" \
  .env.example \
  ubuntu@ec2-13-239-38-15.ap-southeast-2.compute.amazonaws.com:/home/ubuntu/.env.example

# Create backup dir on server, then upload DB backup
ssh -i "chatbot-key.pem" ubuntu@ec2-13-239-38-15.ap-southeast-2.compute.amazonaws.com \
  "mkdir -p ~/PenalLawChatbot/database/backups"

scp -i "chatbot-key.pem" \
  ~/Desktop/Projects/PenalLawChatbot/database/backups/penallaw_backup_20260505_150435.sql \
  ubuntu@ec2-13-239-38-15.ap-southeast-2.compute.amazonaws.com:~/PenalLawChatbot/database/backups/

# Upload eval dataset (create directory on server first)
scp -i "chatbot-key.pem" \
  ai-service/evaluation/thesis_eval_1000.json \
  ubuntu@ec2-13-239-38-15.ap-southeast-2.compute.amazonaws.com:~/PenalLawChatbot/ai-service/evaluation/

# ── ON SERVER: run the installer ─────────────────────────────────────────────
# (after SSH into EC2)
chmod +x setup_server.sh restore_database.sh deploy_nodocker.sh

# 1. Run the base setup first (this will clone the git repo into /root/PenalLawChatbot)
sudo bash setup_server.sh

# 2. Move the files you uploaded into the newly cloned repository
sudo mkdir -p /root/PenalLawChatbot/database/backups
sudo mkdir -p /root/PenalLawChatbot/ai-service/scraped_datasets
sudo cp ~/.env.example /root/PenalLawChatbot/.env.example 2>/dev/null || true
sudo cp -r ~/PenalLawChatbot/database/backups/* /root/PenalLawChatbot/database/backups/ 2>/dev/null || true
sudo cp -r ~/PenalLawChatbot/ai-service/scraped_datasets/* /root/PenalLawChatbot/ai-service/scraped_datasets/ 2>/dev/null || true

# 3. restore database (must use sudo to create logs and switch users)
sudo bash restore_database.sh

# 4. deploy the app (use sudo since it installs services and opens ports)
sudo bash deploy_nodocker.sh

# ── BACKUP DATABASE: create backup on server and download ─────────────────────
# On server:
cd ~/PenalLawChatbot
mkdir -p ./database/backups
chmod 777 ./database/backups
./backup_database.sh

# Download backup to local machine (from LOCAL):
scp -i "chatbot-key.pem" \
  'ubuntu@ec2-13-239-38-15.ap-southeast-2.compute.amazonaws.com:~/PenalLawChatbot/database/backups/penallaw_backup_*.sql' \
  ~/Desktop/Projects/PenalLawChatbot/database/backups/

# ── EVALUATION ────────────────────────────────────────────────────────────────
# On server — inside ~/PenalLawChatbot/ (ai-service must be running)

tmux new -s eval   # or: tmux attach -t eval

cd ~/PenalLawChatbot
source ai-service/venv/bin/activate
pip install openai requests python-dotenv   # first time only

# Primary Article Recall (no LLM cost — fast)
python3 ai-service/evaluation/eval_primary_recall.py \
  --log-file ai-service/logs/eval_primary_recall.txt

# Role Adherence — Layer A only (no LLM cost — fast)
python3 ai-service/evaluation/eval_role_adherence.py \
  --skip-llm \
  --log-file ai-service/logs/eval_role_adherence.txt

# Role Adherence — with LLM judge
python3 ai-service/evaluation/eval_role_adherence.py \
  --log-file ai-service/logs/eval_role_adherence.txt

# Hallucination — skip L4 (no LLM cost)
python3 ai-service/evaluation/eval_hallucination.py \
  --skip-l4 \
  --log-file ai-service/logs/eval_hallucination.txt

# Hallucination — full 4-layer
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

# Resume interrupted eval
python3 ai-service/evaluation/eval_primary_recall.py \
  --resume \
  --log-file ai-service/logs/eval_primary_recall.txt

# ── DOWNLOAD RESULTS to local machine ────────────────────────────────────────
# Run from LOCAL machine (inside ~/Desktop/Projects/PenalLawChatbot/)

# Download result JSONs and JSONL files
scp -i "chatbot-key.pem" -r \
  'ubuntu@ec2-13-239-38-15.ap-southeast-2.compute.amazonaws.com:~/PenalLawChatbot/ai-service/evaluation/results/' \
  ~/Desktop/Projects/PenalLawChatbot/ai-service/evaluation/

# Download log .txt files
scp -i "chatbot-key.pem" \
  'ubuntu@ec2-13-239-38-15.ap-southeast-2.compute.amazonaws.com:~/PenalLawChatbot/ai-service/logs/eval_*.txt' \
  ~/Desktop/Projects/PenalLawChatbot/ai-service/logs/

# ── MONITOR GPU ───────────────────────────────────────────────────────────────
watch -n 1 nvidia-smi
