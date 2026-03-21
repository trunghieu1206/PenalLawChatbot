import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { aiApi } from '../services/api.js';
import MessageBubble from '../components/MessageBubble.jsx';
import RoleSelector from '../components/RoleSelector.jsx';
import styles from './ChatPage.module.css';

// Local conversation stored in memory (resets on page refresh)
let localSessionCounter = 0;

function makeSession(role) {
  localSessionCounter += 1;
  return { id: `local-${localSessionCounter}`, mode: role, createdAt: new Date().toISOString() };
}

export default function ChatPage() {
  const navigate = useNavigate();

  const [sessions, setSessions] = useState([]); // local history list
  const [currentSession, setCurrentSession] = useState(null);
  const [messages, setMessages] = useState([]);
  const [role, setRole] = useState('neutral');
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [showRoleModal, setShowRoleModal] = useState(false);

  // Map: sessionId → messages[]  (in-memory store)
  const [sessionMessages, setSessionMessages] = useState({});

  const messagesEndRef = useRef(null);
  const textareaRef = useRef(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const createNewSession = () => {
    const session = makeSession(role);
    setSessions(prev => [session, ...prev]);
    setCurrentSession(session);
    setMessages([]);
  };

  const handleSend = async () => {
    const content = input.trim();
    if (!content || loading) return;

    // Create session on first message if none exists
    let session = currentSession;
    if (!session) {
      session = makeSession(role);
      setSessions(prev => [session, ...prev]);
      setCurrentSession(session);
    }

    const userMsg = {
      id: Date.now(),
      role: 'user',
      content,
      createdAt: new Date().toISOString(),
    };

    const nextMessages = [...messages, userMsg];
    setMessages(nextMessages);
    setInput('');
    setLoading(true);
    setError('');

    try {
      const data = await aiApi.predict(content, role);
      const aiMsg = {
        id: Date.now() + 1,
        role: 'assistant',
        content: data.result,
        mappedLaws: data.mapped_laws || [],
        extractedFacts: data.extracted_facts,
        createdAt: new Date().toISOString(),
      };
      const finalMessages = [...nextMessages, aiMsg];
      setMessages(finalMessages);
      // Persist to in-memory session store
      setSessionMessages(prev => ({ ...prev, [session.id]: finalMessages }));
    } catch (err) {
      const msg = err.response?.data?.detail || err.message || 'Đã xảy ra lỗi. Vui lòng thử lại.';
      setError(msg);
      setMessages(nextMessages.slice(0, -1)); // remove optimistic user msg
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleSessionClick = (session) => {
    setCurrentSession(session);
    setRole(session.mode);
    setMessages(sessionMessages[session.id] || []);
    setError('');
  };

  const charCount = input.length;

  return (
    <div className={styles.layout}>
      {/* SIDEBAR */}
      <aside className={`${styles.sidebar} ${sidebarOpen ? styles.sidebarOpen : styles.sidebarClosed}`}>
        <div className={styles.sidebarHeader}>
          <div className={styles.logo}>
            <span>⚖️</span>
            {sidebarOpen && <span className={styles.logoText}>LegalAI</span>}
          </div>
          <button className={`btn btn-ghost ${styles.toggleBtn}`} onClick={() => setSidebarOpen(o => !o)}>
            {sidebarOpen ? '◀' : '▶'}
          </button>
        </div>

        {sidebarOpen && (
          <>
            <button className={`btn btn-primary ${styles.newChatBtn}`} onClick={createNewSession}>
              ✦ Cuộc trò chuyện mới
            </button>

            <div className={styles.sessionList}>
              {sessions.length === 0 && (
                <p className={styles.emptyNote}>Chưa có cuộc hội thoại nào</p>
              )}
              {sessions.map(s => (
                <button
                  key={s.id}
                  className={`${styles.sessionItem} ${currentSession?.id === s.id ? styles.sessionActive : ''}`}
                  onClick={() => handleSessionClick(s)}
                >
                  <span className={`badge badge-${s.mode}`}>{s.mode}</span>
                  <span className={styles.sessionDate}>
                    {new Date(s.createdAt).toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' })}
                  </span>
                </button>
              ))}
            </div>

            <div className={styles.userSection}>
              <div className={styles.userInfo}>
                <span className={styles.userAvatar}>⚖️</span>
                <div>
                  <div className={styles.userName}>Khách</div>
                  <div className={styles.userEmail}>Không cần đăng nhập</div>
                </div>
              </div>
              <div className={styles.userActions}>
                <button className="btn btn-ghost" onClick={() => navigate('/training')} title="Chế độ luyện tập">🎓</button>
              </div>
            </div>
          </>
        )}
      </aside>

      {/* MAIN CHAT AREA */}
      <main className={styles.main}>
        {/* Header */}
        <header className={styles.header}>
          <div className={styles.headerLeft}>
            <h2 className={styles.headerTitle}>
              {currentSession ? `Phiên #${currentSession.id.replace('local-', '')}` : 'Phân tích vụ án mới'}
            </h2>
          </div>
          <div className={styles.headerRight}>
            <button
              className={`btn btn-ghost ${styles.roleBtn}`}
              onClick={() => setShowRoleModal(true)}
            >
              {role === 'defense' && '🛡️'}{role === 'victim' && '🔴'}{role === 'neutral' && '⚖️'}
              <span className={`badge badge-${role}`}>
                {role === 'defense' ? 'Bào chữa' : role === 'victim' ? 'Bị hại' : 'Trung lập'}
              </span>
            </button>
          </div>
        </header>

        {/* Messages */}
        <div className={`${styles.messages} scroll-area`}>
          {messages.length === 0 && (
            <div className={styles.welcome}>
              <div className={styles.welcomeIcon}>⚖️</div>
              <h3 className={styles.welcomeTitle}>Trợ lý Pháp luật Hình sự</h3>
              <p className={styles.welcomeDesc}>
                Dán nội dung vụ án vào ô bên dưới để nhận phân tích pháp lý chi tiết.
              </p>
              <div className={styles.welcomeHints}>
                <span>📋 Phân tích tội danh &amp; điều luật</span>
                <span>⚡ Lượng hình &amp; tình tiết</span>
                <span>📜 Trích dẫn bộ luật hình sự</span>
              </div>
            </div>
          )}

          {messages.map((msg, i) => (
            <div key={msg.id || i} className="animate-fade-in">
              <MessageBubble message={msg} role={role} />
            </div>
          ))}

          {loading && (
            <div className={styles.typingIndicator}>
              <span className={styles.typingDot} />
              <span className={styles.typingDot} />
              <span className={styles.typingDot} />
              <span className={styles.typingText}>AI đang phân tích...</span>
            </div>
          )}

          {error && (
            <div className={styles.errorBanner}>{error}</div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* Input Area */}
        <div className={styles.inputArea}>
          <div className={styles.inputWrapper}>
            <textarea
              ref={textareaRef}
              className={styles.textarea}
              placeholder="Dán nội dung hồ sơ vụ án hoặc câu hỏi pháp lý vào đây... (Enter để gửi, Shift+Enter xuống dòng)"
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              rows={4}
              disabled={loading}
            />
            <div className={styles.inputFooter}>
              <span className={styles.charCount}>{charCount} ký tự</span>
              <button
                className={`btn btn-primary ${styles.sendBtn}`}
                onClick={handleSend}
                disabled={loading || !input.trim()}
              >
                {loading ? <span className="loader" /> : '↑ Phân tích'}
              </button>
            </div>
          </div>
        </div>
      </main>

      {/* Role Modal */}
      {showRoleModal && (
        <div className={styles.modalOverlay} onClick={() => setShowRoleModal(false)}>
          <div className={`${styles.modal} card`} onClick={e => e.stopPropagation()}>
            <h3 className={styles.modalTitle}>Chọn vai trò phân tích</h3>
            <RoleSelector selected={role} onChange={(r) => { setRole(r); setShowRoleModal(false); }} />
          </div>
        </div>
      )}
    </div>
  );
}
