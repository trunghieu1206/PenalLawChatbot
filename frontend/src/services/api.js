import axios from 'axios';

// ---- BACKEND API CLIENT ----
// In dev: Vite proxies /api → http://localhost:8080 or equivalent backend port
// In production (Docker): nginx proxies /api → backend:8080
const apiClient = axios.create({
  baseURL: '/api',
  headers: { 'Content-Type': 'application/json' },
  timeout: 130000, // 130s — Backend waits for AI service
});

// Add Authorization header if token exists
apiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem('token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// ---- AUTH API METHODS ----
// BUG-01 FIX: authApi was imported by LoginPage/RegisterPage but was never defined.
export const authApi = {
  login: (credentials) =>
    apiClient.post('/auth/login', credentials).then(r => r.data),

  register: (userData) =>
    apiClient.post('/auth/register', userData).then(r => r.data),
};

// ---- GUEST ID MANAGEMENT ----
export const getGuestId = () => {
  let guestId = localStorage.getItem('guestId');
  if (!guestId) {
    guestId = 'guest_' + Math.random().toString(36).substring(2, 15);
    localStorage.setItem('guestId', guestId);
  }
  return guestId;
};

// ---- CHAT API METHODS ----
// Support both authenticated and guest users
export const chatApi = {
  // Get all sessions (authenticated if logged in, otherwise guest)
  getSessions: () => {
    const token = localStorage.getItem('token');
    if (token) {
      // Authenticated user sessions
      return apiClient.get(`/chat/sessions`).then(r => r.data);
    } else {
      // Guest sessions
      return apiClient.get(`/chat/guest/${getGuestId()}/sessions`).then(r => r.data);
    }
  },

  // Create a new session (authenticated if logged in, otherwise guest)
  createSession: (requestBody = {}) => {
    const token = localStorage.getItem('token');
    const mode = requestBody.role || requestBody.mode || 'neutral';
    
    if (token) {
      // Authenticated user session
      return apiClient.post(`/chat/sessions`, {
        mode,
      }).then(r => r.data);
    } else {
      // Guest session
      return apiClient.post(`/chat/guest/${getGuestId()}/sessions`, {
        mode,
      }).then(r => r.data);
    }
  },

  // Send a message within an existing session
  // Backend SendMessageRequest expects: { content, role, rebuttal_against }
  // NOTE: 'content' is @NotBlank — must not be empty or missing!
  sendMessage: (sessionId, content, role = 'neutral', rebuttalAgainst = null) =>
    apiClient.post(`/chat/sessions/${sessionId}/messages`, {
      content,                              // ← must be 'content', NOT 'caseContent'
      role,
      rebuttal_against: rebuttalAgainst,   // ← must match @JsonProperty("rebuttal_against")
    }).then(r => r.data),

  // Load message history for a session
  getHistory: (sessionId) =>
    apiClient.get(`/chat/sessions/${sessionId}/messages`).then(r => r.data),

  // Delete a session
  deleteSession: (sessionId) =>
    apiClient.delete(`/chat/sessions/${sessionId}`).then(r => r.data),
};

// ---- LAWS API ----
// Fetches law text from PostgreSQL via backend.
// crimeDate (optional): ISO date string "YYYY-MM-DD" — used to select the
// law version applicable at the time of the crime.
export const lawsApi = {
  /**
   * @param {string} articleNumber - e.g. "Điều 249" or bare "249"
   * @param {string|null} crimeDate - ISO date "YYYY-MM-DD" or null
   */
  getLaw: (articleNumber, crimeDate = null) => {
    const params = crimeDate ? `?crimeDate=${encodeURIComponent(crimeDate)}` : '';
    return apiClient.get(`/laws/${encodeURIComponent(articleNumber)}${params}`).then(r => r.data);
  },
};

export default apiClient;

// ---- AI SERVICE DIRECT CLIENT (proxied via /ai-api/) ----
const aiServiceClient = axios.create({
  baseURL: '/ai-api',
  headers: { 'Content-Type': 'application/json' },
  timeout: 120000, // 2 minutes for LLM calls
});

// ---- PRACTICE MODE API ----
export const practiceApi = {
  /**
   * Evaluate user's legal analysis.
   * @param {string} caseDescription - The case content
   * @param {'neutral'|'defense'|'victim'} mode - User's chosen role
   * @param {string} userAnalysis - The user's written analysis
   */
  evaluate: (caseDescription, mode, userAnalysis) =>
    aiServiceClient.post('/practice/evaluate', {
      case_description: caseDescription,
      user_mode: mode,
      user_analysis: userAnalysis,
    }).then(r => r.data),
};
