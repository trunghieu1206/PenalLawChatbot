import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth.jsx';
import { chatApi, lawsApi } from '../services/api.js';
import MessageBubble from '../components/MessageBubble.jsx';
import RoleSelector from '../components/RoleSelector.jsx';
import LawSidebar from '../components/LawSidebar.jsx';
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

  // Law reference sidebar state
  const [lawSidebar, setLawSidebar] = useState({
    open: false,
    loading: false,
    data: null,   // LawLookupResponse from backend
    error: null,
  });

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

  /**
   * Called when user clicks a law pill in MessageBubble.
   * Fetches the version of the law effective at the crimeDate.
   * `source` (edition_applied) narrows lookup to the exact BLHS edition.
   */
  const handleLawClick = async (law, crimeDate, source = null) => {
    const articleRaw = law.article || law.article_number || '';
    if (!articleRaw) return;

    setLawSidebar({ open: true, loading: true, data: null, error: null });

    try {
      const data = await lawsApi.getLaw(articleRaw, crimeDate, source);
      if (data.versions && data.versions.length === 0) {
        setLawSidebar({ open: true, loading: false, data, error: `Không tìm thấy ${articleRaw} trong cơ sở dữ liệu.` });
      } else {
        setLawSidebar({ open: true, loading: false, data, error: null });
      }
    } catch (err) {
      const msg = err.response?.data?.message || err.message || 'Lỗi khi tải điều luật.';
      setLawSidebar({ open: true, loading: false, data: null, error: msg });
    }
  };


  const closeLawSidebar = () =>
    setLawSidebar({ open: false, loading: false, data: null, error: null });

  const handleRoleBtnClick = () => {
    if (currentSession) {
      // Show lock popup and auto-dismiss after 2.5s
      setShowRoleLockPopup(true);
      setTimeout(() => setShowRoleLockPopup(false), 2500);
    } else {
      setShowRoleModal(true);
    }
  };

  const userInitials = user?.fullName
    ? user.fullName.split(' ').slice(0, 2).map(part => part[0]).join('')
    : user?.email?.[0]?.toUpperCase() || 'G';

  return (
    <div className={styles.page}>
      <aside className={styles.nav}>
        <div className={styles.navHeader}>
          <div className={styles.brandBadge}>V</div>
          <div>
            <div className={styles.brandTitle}>VNPLaw</div>
            <div className={styles.brandSub}>Legal Intelligence</div>
          </div>
        </div>
        <div className={styles.navLinks}>
          <button className={`${styles.navItem} ${styles.navItemActive}`} type="button">
            <span className="material-symbols-outlined">chat</span>
            Chat
          </button>
          <button className={styles.navItem} type="button" onClick={() => navigate('/training')}>
            <span className="material-symbols-outlined">gavel</span>
            Chế độ Luyện tập
          </button>
          <button className={styles.navItem} type="button" onClick={() => navigate('/stats')}>
            <span className="material-symbols-outlined">dashboard</span>
            Bảng điều khiển
          </button>
          {user?.role === 'admin' && (
            <button className={styles.navItem} type="button" onClick={() => navigate('/admin')}>
              <span className="material-symbols-outlined">admin_panel_settings</span>
              Quản lý phản hồi
            </button>
          )}
        </div>
        <div className={styles.navFooter}>
          <button className={styles.navItem} type="button">
            <span className="material-symbols-outlined">settings</span>
            Cài đặt
          </button>
          <button className={styles.navItem} type="button">
            <span className="material-symbols-outlined">help</span>
            Hỗ trợ
          </button>
        </div>
      </aside>

      <main className={styles.main}>
        <header className={styles.topbar}>
          <div className={styles.topbarLeft}>
            <div className={styles.topbarTitle}>VNPLaw Intelligence</div>
            <nav className={styles.topbarLinks}>
              <button type="button">Tài liệu</button>
              <button type="button">Lưu trữ</button>
            </nav>
          </div>
          <div className={styles.topbarRight}>
            <div className={styles.searchWrap}>
              <span className="material-symbols-outlined">search</span>
              <input className={styles.searchInput} placeholder="Tìm kiếm án lệ..." />
            </div>
            <button className="btn btn-primary" type="button" onClick={createNewSession}>
              <span className="material-symbols-outlined">add</span>
              Vụ án mới
            </button>
            <div className={styles.userActions}>
              <button className={styles.iconBtn} type="button" title="Thông báo">
                <span className="material-symbols-outlined">notifications</span>
              </button>
              <button className={styles.avatarBtn} type="button" title={user?.email || 'Khách'}>
                {userInitials}
              </button>
            </div>
          </div>
        </header>

        <div className={styles.canvas}>
          <aside className={styles.history}>
            <div className={styles.historyHeader}>
              <span>Tư vấn gần đây</span>
            </div>
            <div className={styles.historyList}>
              {sessions.length === 0 && (
                <div className={styles.emptyNote}>Chưa có cuộc hội thoại nào</div>
              )}
              {sessions.map((s) => (
                <div
                  key={s.id}
                  className={`${styles.sessionCard} ${currentSession?.id === s.id ? styles.sessionActive : ''}`}
                  onClick={() => handleSessionClick(s)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => { if (e.key === 'Enter') handleSessionClick(s); }}
                >
                  <div className={styles.sessionMeta}>
                    <span>
                      {new Date(s.createdAt).toLocaleDateString('vi-VN', {
                        timeZone: 'Asia/Ho_Chi_Minh',
                        day: '2-digit', month: '2-digit', year: 'numeric'
                      })}
                    </span>
                    <button
                      className={styles.sessionDelete}
                      onClick={(e) => handleDeleteSession(e, s.id)}
                      title="Xóa phiên"
                      type="button"
                    >
                      <span className="material-symbols-outlined">close</span>
                    </button>
                  </div>
                  <div className={styles.sessionTitle}>
                    {s.title || (s.id ? `Phiên #${s.id.substring(0, 8)}` : 'Cuộc trò chuyện mới')}
                  </div>
                </div>
              ))}
            </div>
            <div className={styles.historyFooter}>
              {user ? (
                <button
                  className="btn btn-outline"
                  type="button"
                  onClick={() => {
                    logout();
                    navigate('/login');
                  }}
                >
                  Đăng xuất
                </button>
              ) : (
                <div className={styles.historyAuth}>
                  <button className="btn btn-ghost" type="button" onClick={() => navigate('/login')}>
                    Đăng nhập
                  </button>
                  <button className="btn btn-primary" type="button" onClick={() => navigate('/register')}>
                    Đăng ký
                  </button>
                </div>
              )}
            </div>
          </aside>

          <section className={styles.chatPanel}>
            <div className={styles.caseHeader}>
              <div>
                <div className={styles.caseTags}>
                  <span className={styles.tagNeutral}>Luật Hình sự</span>
                  <span className={styles.tagSecondary}>Đang xem xét</span>
                </div>
                <h2 className={styles.caseTitle}>
                  {currentSession ? currentSession.title : 'Phân tích vụ án mới'}
                </h2>
                <div className={styles.caseMeta}>
                  <span>
                    <span className="material-symbols-outlined">calendar_today</span>
                    {new Date().toLocaleDateString('vi-VN')}
                  </span>
                  <span>
                    <span className="material-symbols-outlined">account_balance</span>
                    People&apos;s Court
                  </span>
                </div>
              </div>
              <div className={styles.caseActions}>
                <button className={styles.iconBtn} type="button" title="Tải xuống">
                  <span className="material-symbols-outlined">download</span>
                </button>
                <div className={styles.roleBtnWrap}>
                  <button
                    className={styles.roleBtn}
                    onClick={handleRoleBtnClick}
                    type="button"
                  >
                    <span className={`badge badge-${activeRole}`}>{roleLabel}</span>
                    {currentSession && <span className={styles.roleLocked}>Đã khóa</span>}
                  </button>
                  {showRoleLockPopup && (
                    <div className={styles.rolePopup}>
                      Vai trò đã được khóa cho phiên này.
                    </div>
                  )}
                </div>
              </div>
            </div>

            <div className={styles.feed}>
              <div className={styles.feedInner}>
                {messages.length === 0 && (
                  <div className={styles.emptyState}>
                    <h3>Trợ lý Luật Hình sự</h3>
                    <p>Dán nội dung hồ sơ vụ án để nhận phân tích pháp luật chi tiết.</p>
                    <div className={styles.hintRow}>
                      <span>Phân tích tội danh</span>
                      <span>Điều luật áp dụng</span>
                      <span>Tình tiết giảm nhẹ</span>
                    </div>
                  </div>
                )}

                {messages.map((msg, i) => (
                  <div key={msg.id || i} className="animate-fade-in">
                    <MessageBubble
                      message={msg}
                      role={activeRole}
                      sessionId={currentSession?.id}
                      onLawClick={handleLawClick}
                    />
                  </div>
                ))}

                {loading && (
                  <div className={styles.typingIndicator}>
                    <span className={styles.typingDot} />
                    <span className={styles.typingDot} />
                    <span className={styles.typingDot} />
                    <span>AI đang phân tích...</span>
                  </div>
                )}

                {error && <div className={styles.errorBanner}>{error}</div>}
                <div ref={messagesEndRef} />
              </div>
            </div>

            <div className={styles.composer}>
              <div className={styles.composerInner}>
                <textarea
                  ref={textareaRef}
                  className={styles.composerTextarea}
                  placeholder="Đặt câu hỏi tiếp theo hoặc yêu cầu trích xuất điều khoản..."
                  value={input}
                  onChange={e => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  rows={3}
                  disabled={loading}
                />
                <div className={styles.composerActions}>
                  <button className={styles.attachBtn} type="button" title="Đính kèm">
                    <span className="material-symbols-outlined">attach_file</span>
                  </button>
                  <span className={styles.charCount}>{charCount} ký tự</span>
                  <button
                    className={styles.sendBtn}
                    onClick={handleSend}
                    disabled={loading || !input.trim()}
                    type="button"
                  >
                    <span className="material-symbols-outlined">send</span>
                  </button>
                </div>
              </div>
            </div>
          </section>

          <aside className={`${styles.lawPanel} ${lawSidebar.open ? styles.lawPanelOpen : ''}`}>
            {lawSidebar.open && (
              <LawSidebar
                lawData={lawSidebar.data}
                loading={lawSidebar.loading}
                error={lawSidebar.error}
                onClose={closeLawSidebar}
              />
            )}
          </aside>
        </div>
      </main>

      {showRoleModal && (
        <div className={styles.modalOverlay} onClick={() => setShowRoleModal(false)}>
          <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
            <h3 className={styles.modalTitle}>Chọn vai trò phân tích</h3>
            <p className={styles.modalDesc}>
              {pendingContent
                ? 'Chọn vai trò trước khi bắt đầu. Vai trò sẽ không thể thay đổi sau khi phiên bắt đầu.'
                : 'Vai trò sẽ được khóa cho toàn bộ phiên hội thoại này.'}
            </p>
            <RoleSelector selected={role} onChange={handleRoleConfirmed} />
            <button
              className="btn btn-ghost"
              type="button"
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
