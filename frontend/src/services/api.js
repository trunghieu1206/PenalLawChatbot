import axios from 'axios';

// ---- BACKEND API CLIENT ----
// In dev: Vite proxies /api → http://localhost:8080 or equivalent backend port
// In production (Docker): nginx proxies /api → backend:8080
const apiClient = axios.create({
  baseURL: '/api',
  headers: { 'Content-Type': 'application/json' },
  timeout: 130000, // 130s — Backend waits for AI service
});

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
export const chatApi = {
  // Get all sessions for the current guest
  getSessions: () => 
    apiClient.get(`/chat/guest/${getGuestId()}/sessions`).then(r => r.data),

  // Create a new session
  createSession: (requestBody = {}) => 
    apiClient.post(`/chat/guest/${getGuestId()}/sessions`, requestBody).then(r => r.data),

  // Send a message within an existing session
  sendMessage: (sessionId, caseContent, role = 'neutral', rebuttalAgainst = null) =>
    apiClient.post(`/chat/sessions/${sessionId}/messages`, {
      caseContent,
      role,
      rebuttalAgainst
    }).then(r => r.data),

  // Load message history for a session
  getHistory: (sessionId) =>
    apiClient.get(`/chat/sessions/${sessionId}/messages`).then(r => r.data),

  // Delete a session
  deleteSession: (sessionId) =>
    apiClient.delete(`/chat/sessions/${sessionId}`).then(r => r.data),
};

export default apiClient;

