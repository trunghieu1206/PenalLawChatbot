import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth.jsx';
import { chatApi } from '../services/api.js';
import MessageBubble from '../components/MessageBubble.jsx';
import RoleSelector from '../components/RoleSelector.jsx';
import styles from './ChatPage.module.css';

export default function ChatPage() {
  const navigate = useNavigate();
  const { user, logout } = useAuth();

  const [sessions, setSessions] = useState([]);
  const [currentSession, setCurrentSession] = useState(null);
  const [messages, setMessages] = useState([]);
  // `role` is only used for the pre-session flow; once a session exists,
  // activeRole is always derived from currentSession.mode (source of truth)
  const [role, setRole] = useState('neutral');
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [sidebarOpen, setSidebarOpen] = useState(true);

  // Role modal: shown before session creation to force role selection
  const [showRoleModal, setShowRoleModal] = useState(false);
  // pendingContent: message the user typed before a session existed
  const [pendingContent, setPendingContent] = useState('');
  // showRoleLockPopup: brief tooltip shown when user clicks locked role button
  const [showRoleLockPopup, setShowRoleLockPopup] = useState(false);

  // In-memory message cache per session
  const [sessionMessages, setSessionMessages] = useState({});

  const messagesEndRef = useRef(null);
  const textareaRef = useRef(null);

  // Always read role from the active session — never stale
  const activeRole = currentSession?.mode || role;

  useEffect(() => {
    chatApi.getSessions().then(data => {
      setSessions(data);
    }).catch(err => {
      console.error('Failed to load sessions:', err);
    });
  }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Called when user clicks "New session" — show role modal FIRST
  const createNewSession = () => {
    setPendingContent('');
    setShowRoleModal(true);
  };

  // Called after user picks a role in the modal — now create session
  const handleRoleConfirmed = async (selectedRole) => {
    setRole(selectedRole);
    setShowRoleModal(false);

    let newSession;
    try {
      newSession = await chatApi.createSession({ role: selectedRole });
      setSessions(prev => [newSession, ...prev]);
      setCurrentSession(newSession);
      setMessages([]);
    } catch (err) {
      console.error('Create session failed:', err);
      setError('Không thể tạo phiên mới.');
      return;
    }

    // If user had typed a message before session existed, send it now
    // BUG-07 FIX: pass [] as priorMessages since this is a brand-new session.
    if (pendingContent.trim()) {
      const content = pendingContent.trim();
      setPendingContent('');
      setInput('');
      await doSend(newSession, content, selectedRole, []);
    }
  };

  // Core send logic — separated so it can be called from two entry points
  // BUG-02 FIX: Use functional update in error catch to avoid stale-closure rollback.
  // BUG-08 FIX: Use .message (backend GlobalExceptionHandler) not .detail.
  const doSend = async (activeSession, content, sendRole, priorMessages = messages) => {
    const userMsg = {
      id: Date.now(),
      role: 'user',
      content,
      createdAt: new Date().toISOString(),
    };

    const nextMessages = [...priorMessages, userMsg];
    setMessages(nextMessages);
    setLoading(true);
    setError('');

    try {
      const aiResponse = await chatApi.sendMessage(activeSession.id, content, sendRole);

      const aiMsg = {
        id: aiResponse.id || Date.now() + 1,
        role: aiResponse.role || 'assistant',
        content: aiResponse.content,
        mappedLaws: aiResponse.mapped_laws || aiResponse.mappedLaws || [],
        extractedFacts: aiResponse.extracted_facts || aiResponse.extractedFacts,
        createdAt: aiResponse.createdAt || new Date().toISOString(),
      };

      const finalMessages = [...nextMessages, aiMsg];
      setMessages(finalMessages);
      setSessionMessages(prev => ({ ...prev, [activeSession.id]: finalMessages }));

      // Auto-title on first message
      // BUG-07 FIX: Use priorMessages.length === 0 so the check is based on what
      // was actually there before this send, not a stale captured `messages`.
      if (priorMessages.length === 0 && (activeSession.title === 'Phiên mới' || !activeSession.title)) {
        const newTitle = content.length > 50 ? content.substring(0, 50) + '...' : content;
        setSessions(prev => prev.map(s => s.id === activeSession.id ? { ...s, title: newTitle } : s));
        setCurrentSession(prev => ({ ...prev, title: newTitle }));
      }
    } catch (err) {
      // BUG-08 FIX: Backend returns { status, error, message, timestamp } — not .detail
      const msg = err.response?.data?.message || err.message || 'Đã xảy ra lỗi. Vui lòng thử lại.';
      setError(msg);
      // BUG-02 FIX: Functional update to avoid stale closure — remove the specific userMsg
      setMessages(prev => prev.filter(m => m.id !== userMsg.id));
    } finally {
      setLoading(false);
    }
  };

  const handleSend = async () => {
    const content = input.trim();
    if (!content || loading) return;

    // No session yet → save content, show role selection modal
    if (!currentSession) {
      setPendingContent(content);
      setShowRoleModal(true);
      return;
    }

    setInput('');
    // Role is always locked to session.mode once session exists
    await doSend(currentSession, content, currentSession.mode);
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleSessionClick = async (session) => {
    if (loading) return;
    setCurrentSession(session);
    // Always sync role from session.mode — fixes the "reset to neutral" bug
    setRole(session.mode || 'neutral');
    setError('');

    if (sessionMessages[session.id]) {
      setMessages(sessionMessages[session.id]);
      return;
    }

    try {
      const history = await chatApi.getHistory(session.id);
      setMessages(history.messages || []);
      setSessionMessages(prev => ({ ...prev, [session.id]: history.messages || [] }));
    } catch (err) {
      setError('Không thể tải lịch sử trò chuyện.');
      setMessages([]);
    }
  };

  const handleDeleteSession = async (e, sessionId) => {
    e.stopPropagation();
    if (!window.confirm('Bạn có chắc chắn muốn xóa cuộc trò chuyện này?')) return;

    try {
      await chatApi.deleteSession(sessionId);
      setSessions(prev => prev.filter(s => s.id !== sessionId));
      setSessionMessages(prev => {
        const copy = { ...prev };
        delete copy[sessionId];
        return copy;
      });
      if (currentSession?.id === sessionId) {
        setCurrentSession(null);
        setMessages([]);
        setRole('neutral');
      }
    } catch (err) {
      alert('Xóa thất bại. Vui lòng thử lại.');
    }
  };

  const roleLabel = activeRole === 'defense' ? 'Luật sư Bào chữa' : activeRole === 'victim' ? 'Luật sư Bị hại' : 'Thẩm phán';
  const charCount = input.length;

  const handleRoleBtnClick = () => {
    if (currentSession) {
      // Show lock popup and auto-dismiss after 2.5s
      setShowRoleLockPopup(true);
      setTimeout(() => setShowRoleLockPopup(false), 2500);
    } else {
      setShowRoleModal(true);
    }
  };

  return (
    <div className={styles.layout}>
      {/* SIDEBAR */}
      <aside className={`${styles.sidebar} ${sidebarOpen ? styles.sidebarOpen : styles.sidebarClosed}`}>
        <div className={styles.sidebarHeader}>
          <div className={styles.logo}>
            {sidebarOpen && <span className={styles.logoText}>VNPLaw</span>}
          </div>
        </div>

        {sidebarOpen && (
          <>
            <button className={`btn btn-primary ${styles.newChatBtn}`} onClick={createNewSession}>
              Cuộc trò chuyện mới
            </button>

            <div className={styles.sessionList}>
              {sessions.length === 0 && (
                <p className={styles.emptyNote}>Chưa có cuộc hội thoại nào</p>
              )}
              {sessions.map(s => (
                <div key={s.id} className={`${styles.sessionItemWrapper} ${currentSession?.id === s.id ? styles.sessionActive : ''}`}>
                  <button className={styles.sessionItem} onClick={() => handleSessionClick(s)}>
                    <div className={styles.sessionItemHeader}>
                      <span className={styles.sessionDate}>
                        {new Date(s.createdAt).toLocaleString('vi-VN', {
                          day: '2-digit', month: '2-digit', year: 'numeric',
                          hour: '2-digit', minute: '2-digit', second: '2-digit'
                        })}
                      </span>
                    </div>
                    <div className={styles.sessionTitle} title={s.title}>
                      {s.title || (s.id ? `Phiên #${s.id.substring(0, 8)}` : 'Cuộc trò chuyện mới')}
                    </div>
                  </button>
                  <button
                    className={styles.deleteSessionBtn}
                    onClick={(e) => handleDeleteSession(e, s.id)}
                    title="Xóa phiên"
                  >
                    ✕
                  </button>
                </div>
              ))}
            </div>

            {/* Training Mode Button — prominent */}
            <button
              className={styles.trainingBtn}
              onClick={() => navigate('/training')}
            >
              Chế độ Luyện tập
            </button>

            <div className={styles.userSection}>
              {user ? (
                <>
                  <div className={styles.userInfo}>
                    <span className={styles.userAvatar}>U</span>
                    <div>
                      <div className={styles.userName}>{user.fullName || user.email}</div>
                      <div className={styles.userEmail}>{user.email}</div>
                    </div>
                  </div>
                  <button
                    className="btn btn-ghost"
                    onClick={() => {
                      logout();
                      navigate('/login');
                    }}
                    style={{ width: '100%', marginTop: '8px' }}
                  >
                    Đăng xuất
                  </button>
                </>
              ) : (
                <>
                  <div className={styles.userInfo}>
                    <span className={styles.userAvatar}>G</span>
                    <div>
                      <div className={styles.userName}>Khách</div>
                      <div className={styles.userEmail}>Chưa đăng nhập</div>
                    </div>
                  </div>
                  <div className={styles.userActions}>
                    <button
                      className="btn btn-ghost"
                      onClick={() => navigate('/login')}
                    >
                      Đăng nhập
                    </button>
                    <button
                      className="btn btn-primary"
                      onClick={() => navigate('/register')}
                    >
                      Đăng ký
                    </button>
                  </div>
                </>
              )}
            </div>
          </>
        )}
      </aside>

      {/* FLOATING SIDEBAR TOGGLE BUTTON — always on top, never covered */}
      {!showRoleModal && (
        <button
          className={`btn btn-ghost ${styles.floatingToggleBtn}`}
          style={{ left: sidebarOpen ? '236px' : '32px' }}
          onClick={() => setSidebarOpen(o => !o)}
          title={sidebarOpen ? 'Thu gọn thanh bên' : 'Mở rộng thanh bên'}
        >
          {sidebarOpen ? '◀' : '▶'}
        </button>
      )}

      {/* MAIN CHAT AREA */}
      <main className={styles.main}>
        <header className={styles.header}>
          <div className={styles.headerLeft}>
            <h2 className={styles.headerTitle}>
              {currentSession ? currentSession.title : 'Phân tích vụ án mới'}
            </h2>
          </div>
          <div className={styles.headerRight}>
            {/* Role badge — clickable always; shows lock popup when session is active */}
            <div className={styles.roleBtnWrapper}>
              <button
                className={`btn btn-ghost ${styles.roleBtn} ${currentSession ? styles.roleLocked : ''}`}
                onClick={handleRoleBtnClick}
                title={currentSession ? 'Vai trò đã được khóa cho phiên này' : 'Chọn vai trò phân tích'}
              >
                <span className={`badge badge-${activeRole}`}>{roleLabel}</span>
                {currentSession && <span className={styles.roleLockIcon}>ĐÃ KHÓA</span>}
              </button>
              {showRoleLockPopup && (
                <div className={styles.roleLockPopup}>
                  Vai trò đã được khóa. Bạn không thể thay đổi vai trò trong phiên đang diễn ra.
                </div>
              )}
            </div>
          </div>
        </header>

        <div className={`${styles.messages} scroll-area`}>
          {messages.length === 0 && (
            <div className={styles.welcome}>
              <h3 className={styles.welcomeTitle}>Trợ lý Luật Hình sự</h3>
              <p className={styles.welcomeDesc}>
                Dán nội dung hồ sơ vụ án để nhận được phân tích pháp luật chi tiết.
              </p>
              <div className={styles.welcomeHints}>
                <span>Phân tích tội danh &amp; Điều luật áp dụng</span>
                <span>Hình phạt &amp; Tình tiết giảm nhẹ</span>
                <span>Trích dẫn Bộ luật Hình sự</span>
              </div>
            </div>
          )}

          {messages.map((msg, i) => (
            <div key={msg.id || i} className="animate-fade-in">
              <MessageBubble message={msg} role={activeRole} />
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

          {error && <div className={styles.errorBanner}>{error}</div>}
          <div ref={messagesEndRef} />
        </div>

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

      {/* Role Selection Modal — forced before session creation */}
      {showRoleModal && (
        <div
          className={styles.modalOverlay}
          onClick={() => { setShowRoleModal(false); }}
        >
          <div className={`${styles.modal} card`} onClick={e => e.stopPropagation()}>
            <h3 className={styles.modalTitle}>Chọn vai trò phân tích</h3>
            <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginBottom: '16px', marginTop: '-12px' }}>
              {pendingContent
                ? 'Chọn vai trò trước khi bắt đầu. Vai trò sẽ không thể thay đổi sau khi phiên bắt đầu.'
                : 'Vai trò sẽ được khóa cho toàn bộ phiên hội thoại này.'}
            </p>
            <RoleSelector selected={role} onChange={handleRoleConfirmed} />
            {/* BUG-13 FIX: Always show Cancel; clear pendingContent so it's not re-sent. */}
            <button
              className="btn btn-ghost"
              style={{ marginTop: '12px', width: '100%' }}
              onClick={() => { setShowRoleModal(false); setPendingContent(''); }}
            >
              Hủy
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
