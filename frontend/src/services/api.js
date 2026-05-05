import axios from 'axios';

// ---- BACKEND API CLIENT ----
// In dev: Vite proxies /api → http://localhost:8080 or equivalent backend port
// In production (Docker): nginx proxies /api → backend:8080
const apiClient = axios.create({
  baseURL: '/api',
  headers: { 'Content-Type': 'application/json' },
  timeout: 660000, // 660s (11 min) — must exceed backend's 600s AI service timeout for CPU inference
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
// source (optional): law source like "Bộ luật Hình sự 2025" — used for disambiguation when
// the same article number exists in different versions.
export const lawsApi = {
  /**
   * @param {string} articleNumber - e.g. "Điều 249" or bare "249"
   * @param {string|null} crimeDate - ISO date "YYYY-MM-DD" or null
   * @param {string|null} source - law source like "Bộ luật Hình sự 2025" or null
   */
  getLaw: (articleNumber, crimeDate = null, source = null) => {
    const params = new URLSearchParams();
    if (crimeDate) params.append('crimeDate', crimeDate);
    if (source) params.append('source', source);
    const queryString = params.toString() ? `?${params.toString()}` : '';
    return apiClient.get(`/laws/${encodeURIComponent(articleNumber)}${queryString}`).then(r => r.data);
  },
};

// ---- ADMIN API ----
export const adminApi = {
  /** Get aggregate dashboard statistics. */
  getStats: () =>
    apiClient.get('/home').then(r => r.data),

  /** Get all feedback records with full conversation context (admin view). */
  getFeedback: () =>
    apiClient.get('/admin/feedback').then(r => r.data),

  /** Per-user session (case) counts for the admin user-stats tab. */
  getUserCaseStats: () =>
    apiClient.get('/admin/user-stats').then(r => r.data),

  /** Update the review status of a feedback record (admin only). */
  updateFeedbackStatus: (id, status) =>
    apiClient.patch(`/admin/feedback/${id}/status`, { status }).then(r => r.data),

  /**
   * Submit feedback on an AI response.
   * @param {string} sessionId - UUID of the chat session
   * @param {string} messageId - UUID of the AI message being rated
   * @param {boolean} isCorrect - true = helpful/correct, false = incorrect/unhelpful
   * @param {string|null} comment - optional text explanation
   */
  submitFeedback: (sessionId, messageId, isCorrect, comment = null) =>
    apiClient.post('/admin/feedback', {
      session_id: sessionId,
      message_id: messageId,
      is_correct: isCorrect,
      comment,
    }).then(r => r.data),
};

// ---- VISITOR TRACKING API ----
// Generates a persistent visitorId (UUID in localStorage) and pings
// /api/home/track-visit once per calendar day. This prevents the same user
// navigating between pages from being counted multiple times.
const _getOrCreateVisitorId = () => {
  let id = localStorage.getItem('visitorId');
  if (!id) {
    // Use crypto.randomUUID() if available (modern browsers), otherwise fallback
    id = (typeof crypto !== 'undefined' && crypto.randomUUID)
      ? crypto.randomUUID()
      : 'v-' + Math.random().toString(36).substring(2, 15) + Math.random().toString(36).substring(2, 15);
    localStorage.setItem('visitorId', id);
  }
  return id;
};

export const trackVisitApi = {
  /**
   * Record a unique daily visit. Safe to call on every page load —
   * will only send a request if this visitor hasn't been tracked today.
   */
  track: () => {
    const today = new Date().toISOString().slice(0, 10); // "YYYY-MM-DD"
    const lastTracked = localStorage.getItem('lastVisitDate');
    if (lastTracked === today) return; // Already counted today — skip

    const visitorId = _getOrCreateVisitorId();
    apiClient.post('/home/track-visit', { visitor_id: visitorId })
      .then(() => {
        localStorage.setItem('lastVisitDate', today);
      })
      .catch(() => {
        // Silently ignore — tracking failure should never affect UX
      });
  },
};

export default apiClient;

// ---- AI SERVICE DIRECT CLIENT (proxied via /ai-api/) ----
const aiServiceClient = axios.create({
  baseURL: '/ai-api',
  headers: { 'Content-Type': 'application/json' },
  timeout: 660000, // 660s (11 min) — CPU LLM inference can take 5-10 min
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
