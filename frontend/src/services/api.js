import axios from 'axios';

// ---- DIRECT AI SERVICE CLIENT (no auth required) ----
// In dev: Vite proxies /ai-api → http://localhost:8000
// In production (Docker): nginx proxies /ai-api → ai-service:8000
const aiClient = axios.create({
  baseURL: '/ai-api',
  headers: { 'Content-Type': 'application/json' },
  timeout: 130000, // 130s — LLM calls can be slow
});

export const aiApi = {
  /**
   * Call the AI service directly.
   * @param {string} caseContent - case text or question
   * @param {'defense'|'victim'|'neutral'} role
   * @param {string|null} rebuttalAgainst - optional opposing argument to counter
   */
  predict: (caseContent, role = 'neutral', rebuttalAgainst = null) =>
    aiClient.post('/predict', {
      case_content: caseContent,
      role,
      rebuttal_against: rebuttalAgainst,
    }).then(r => r.data),

  health: () => aiClient.get('/health').then(r => r.data),
};

// ---- BACKEND API (kept for future use) ----
const api = axios.create({
  baseURL: '/api',
  headers: { 'Content-Type': 'application/json' },
  timeout: 130000,
});

export default api;

