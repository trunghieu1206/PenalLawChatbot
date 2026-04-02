import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider, useAuth } from './hooks/useAuth.jsx';
import ChatPage from './pages/ChatPage.jsx';
import TrainingPage from './pages/TrainingPage.jsx';
import LoginPage from './pages/LoginPage.jsx';
import RegisterPage from './pages/RegisterPage.jsx';

// ProtectedRoute: Only allow authenticated or guest access
function ProtectedRoute({ children }) {
  const { user } = useAuth();
  // Allow both authenticated users and guests (null user) to access chat
  return children;
}

// RedirectIfAuthenticated: Redirect home if already logged in
function RedirectIfAuthenticated({ children }) {
  const { user } = useAuth();
  if (user) {
    return <Navigate to="/chat" replace />;
  }
  return children;
}

function Routes_() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/chat" replace />} />
      <Route path="/login" element={<RedirectIfAuthenticated><LoginPage /></RedirectIfAuthenticated>} />
      <Route path="/register" element={<RedirectIfAuthenticated><RegisterPage /></RedirectIfAuthenticated>} />
      <Route path="/chat" element={<ProtectedRoute><ChatPage /></ProtectedRoute>} />
      <Route path="/training" element={<ProtectedRoute><TrainingPage /></ProtectedRoute>} />
      <Route path="*" element={<Navigate to="/chat" replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes_/>
      </BrowserRouter>
    </AuthProvider>
  );
}
