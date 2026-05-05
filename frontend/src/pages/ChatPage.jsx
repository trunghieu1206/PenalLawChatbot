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
      <div className="bg-background text-on-background font-body-md text-body-md h-full min-h-screen flex overflow-hidden">
        {/* SideNavBar */}
        <nav className="h-screen w-64 border-r fixed left-0 top-0 border-slate-200 shadow-sm bg-slate-50 flex flex-col py-6 px-4 z-50">
          <div className="mb-8 px-2 flex items-center gap-3">
            <div className="w-8 h-8 rounded bg-primary text-on-primary flex items-center justify-center font-h3 text-h3">V</div>
            <div>
              <h1 className="text-xl font-bold text-slate-900 tracking-tight font-h3">VNPLaw</h1>
              <p className="text-xs text-on-surface-variant font-label-sm">Legal Intelligence</p>
            </div>
          </div>
          <div className="flex-1 space-y-1">
            <button className="w-full flex items-center gap-3 px-3 py-2 rounded text-slate-900 font-semibold border-r-4 border-slate-900 bg-slate-100 duration-200 ease-in-out">
              <span className="material-symbols-outlined" style={{ fontVariationSettings: "'FILL' 1" }}>chat</span>
              <span className="font-label-sm text-label-sm">Chat</span>
            </button>
            <button onClick={() => navigate('/training')} className="w-full flex items-center gap-3 px-3 py-2 rounded text-slate-500 hover:text-slate-700 hover:bg-slate-100 transition-colors duration-200 ease-in-out">
              <span className="material-symbols-outlined">gavel</span>
              <span className="font-label-sm text-label-sm">Chế độ Luyện tập</span>
            </button>
            <button onClick={() => navigate('/stats')} className="w-full flex items-center gap-3 px-3 py-2 rounded text-slate-500 hover:text-slate-700 hover:bg-slate-100 transition-colors duration-200 ease-in-out">
              <span className="material-symbols-outlined">dashboard</span>
              <span className="font-label-sm text-label-sm">Bảng điều khiển</span>
            </button>
            {user?.role === 'admin' && (
              <button onClick={() => navigate('/admin')} className="w-full flex items-center gap-3 px-3 py-2 rounded text-slate-500 hover:text-slate-700 hover:bg-slate-100 transition-colors duration-200 ease-in-out">
                <span className="material-symbols-outlined">admin_panel_settings</span>
                <span className="font-label-sm text-label-sm">Admin</span>
              </button>
            )}
          </div>
          <div className="mt-auto space-y-1 pt-4 border-t border-slate-200">
            <button className="w-full flex items-center gap-3 px-3 py-2 rounded text-slate-500 hover:text-slate-700 hover:bg-slate-100 transition-colors duration-200 ease-in-out">
              <span className="material-symbols-outlined">settings</span>
              <span className="font-label-sm text-label-sm">Cài đặt</span>
            </button>
            <button onClick={() => { logout(); navigate('/login'); }} className="w-full flex items-center gap-3 px-3 py-2 rounded text-slate-500 hover:text-error hover:bg-red-50 transition-colors duration-200 ease-in-out">
              <span className="material-symbols-outlined">logout</span>
              <span className="font-label-sm text-label-sm">Đăng xuất</span>
            </button>
          </div>
        </nav>

        {/* Main Content Area */}
        <main className="ml-64 flex-1 flex flex-col h-screen bg-surface">
          {/* TopAppBar */}
          <header className="bg-white/80 backdrop-blur-md fixed top-0 right-0 w-[calc(100%-16rem)] z-40 border-b border-surface-variant flex justify-between items-center h-16 px-8 transition-all duration-300">
            <div className="flex items-center gap-6">
              <span className="text-lg font-black text-slate-900 font-h3">VNPLaw Intelligence</span>
              <nav className="hidden md:flex gap-4">
                <button className="font-newsreader text-sm font-medium text-slate-500 hover:text-slate-900 transition-all duration-300">Tài liệu</button>
                <button className="font-newsreader text-sm font-medium text-slate-500 hover:text-slate-900 transition-all duration-300">Lưu trữ</button>
              </nav>
            </div>
            <div className="flex items-center gap-4">
              <div className="relative hidden lg:block">
                <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-outline text-sm">search</span>
                <input className="pl-9 pr-4 py-1.5 bg-surface-container-low border border-surface-variant rounded-full text-sm font-body-md focus:border-primary-container focus:ring-1 focus:ring-primary-container outline-none w-64 transition-all" placeholder="Tìm kiếm án lệ..." type="text"/>
              </div>
              <button onClick={createNewSession} className="bg-primary-container text-on-primary font-label-sm text-label-sm px-4 py-2 rounded hover:bg-primary transition-colors flex items-center gap-2">
                <span className="material-symbols-outlined text-sm">add</span> Vụ án Mới
              </button>
              <div className="flex items-center gap-3 border-l border-surface-variant pl-4 ml-2">
                <button className="text-on-surface-variant hover:text-primary transition-colors">
                  <span className="material-symbols-outlined">notifications</span>
                </button>
                <button className="text-on-surface-variant w-8 h-8 rounded-full bg-surface-container border border-surface-variant flex items-center justify-center font-bold text-sm hover:text-primary transition-colors">
                  {userInitials}
                </button>
              </div>
            </div>
          </header>

          {/* Canvas */}
          <div className="flex-1 mt-16 flex overflow-hidden">
            {/* History List */}
            <aside className="w-72 border-r border-surface-variant bg-surface-bright flex flex-col overflow-y-auto">
              <div className="p-4 border-b border-surface-variant sticky top-0 bg-surface-bright/95 backdrop-blur z-10">
                <h3 className="font-label-sm text-label-sm text-on-surface-variant uppercase tracking-wider">Tư vấn Gần đây</h3>
              </div>
              <div className="flex-1 p-2 space-y-1">
                {sessions.length === 0 && <div className="p-4 text-sm text-on-surface-variant text-center">Chưa có cuộc hội thoại nào</div>}
                {sessions.map((s) => {
                  const isActive = currentSession?.id === s.id;
                  return (
                    <button
                      key={s.id}
                      onClick={() => handleSessionClick(s)}
                      className={`w-full text-left p-3 rounded transition-all group relative ${isActive ? 'bg-surface-container-high border border-surface-variant shadow-sm' : 'hover:bg-surface-container-low border border-transparent'}`}
                    >
                      <div className="flex justify-between items-start mb-1">
                        <span className={`font-label-sm text-[12px] ${isActive ? 'text-primary' : 'text-on-surface-variant'}`}>
                          {new Date(s.createdAt).toLocaleDateString('vi-VN')}
                        </span>
                        <span 
                          onClick={(e) => handleDeleteSession(e, s.id)}
                          className="material-symbols-outlined text-[14px] text-outline hover:text-error opacity-0 group-hover:opacity-100 transition-opacity z-10"
                        >close</span>
                      </div>
                      <h4 className={`font-body-md text-sm font-medium line-clamp-2 leading-snug ${isActive ? 'text-on-surface' : 'text-on-surface opacity-80 group-hover:opacity-100'}`}>
                        {s.title || 'Phiên mới'}
                      </h4>
                    </button>
                  );
                })}
              </div>
            </aside>

            {/* Main Chat Area */}
            <section className="flex-1 flex flex-col bg-surface relative min-w-0">
              <div className="px-8 py-6 border-b border-surface-variant bg-surface-container-lowest flex-shrink-0">
                <div className="max-w-3xl mx-auto flex items-start justify-between">
                  <div>
                    <div className="flex items-center gap-2 mb-2">
                      <span className="bg-surface-container text-on-surface-variant px-2 py-0.5 rounded font-label-sm text-xs border border-surface-variant">Luật Hình sự</span>
                      <span className="bg-secondary-container text-on-secondary-container px-2 py-0.5 rounded font-label-sm text-xs border border-secondary-fixed-dim">Đang Xem xét</span>
                    </div>
                    <h2 className="font-h2 text-h2 text-on-surface mb-2">{currentSession ? currentSession.title : 'Phân tích vụ án mới'}</h2>
                    <div className="flex gap-4 font-body-md text-sm text-on-surface-variant">
                      <div className="flex items-center gap-1">
                        <span className="material-symbols-outlined text-[16px]">calendar_today</span>
                        <span>Ngày: {new Date().toLocaleDateString('vi-VN')}</span>
                      </div>
                      <div className="flex items-center gap-1">
                        <span className="material-symbols-outlined text-[16px]">account_balance</span>
                        <span>Địa điểm: Tòa án Nhân dân</span>
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-2 relative">
                    <button onClick={handleRoleBtnClick} className="flex items-center gap-2 bg-surface-container text-on-surface font-label-sm text-xs px-3 py-1.5 rounded border border-surface-variant hover:bg-surface-container-high transition-colors">
                      <span className="material-symbols-outlined text-sm">person</span>
                      {roleLabel}
                      {currentSession && <span className="material-symbols-outlined text-[14px] text-outline">lock</span>}
                    </button>
                    {showRoleLockPopup && (
                      <div className="absolute top-10 right-0 bg-inverse-surface text-inverse-on-surface text-xs px-3 py-1.5 rounded shadow-md whitespace-nowrap z-50">
                        Vai trò đã bị khóa cho phiên này.
                      </div>
                    )}
                    <button className="text-primary hover:bg-surface-container p-2 rounded transition-colors border border-transparent hover:border-surface-variant">
                      <span className="material-symbols-outlined">download</span>
                    </button>
                  </div>
                </div>
              </div>

              <div className="flex-1 overflow-y-auto px-8 py-8">
                <div className="max-w-3xl mx-auto space-y-8 pb-12">
                  {messages.length === 0 && (
                     <div className="text-center py-12 text-on-surface-variant">
                       <h3 className="font-h3 text-xl mb-2 text-on-surface">Trợ lý Luật Hình sự</h3>
                       <p className="font-body-md text-sm mb-6">Dán nội dung hồ sơ vụ án để nhận phân tích pháp luật chi tiết.</p>
                       <div className="flex justify-center gap-4 text-xs font-label-sm opacity-60">
                         <span>• Phân tích tội danh</span>
                         <span>• Điều luật áp dụng</span>
                         <span>• Tình tiết giảm nhẹ</span>
                       </div>
                     </div>
                  )}
                  {messages.map((msg) => (
                    <div key={msg.id} className="animate-fade-in">
                      <MessageBubble message={msg} role={activeRole} sessionId={currentSession?.id} onLawClick={handleLawClick} />
                    </div>
                  ))}
                  {loading && (
                    <div className="flex gap-4 items-start animate-fade-in">
                      <div className="w-8 h-8 rounded-full bg-primary text-on-primary flex-shrink-0 flex items-center justify-center font-h3 text-sm">V</div>
                      <div className="bg-surface-container-lowest border border-surface-variant p-4 rounded-lg rounded-tl-none shadow-sm flex gap-1 items-center">
                        <div className="w-2 h-2 rounded-full bg-primary animate-pulse" style={{animationDelay: '0ms'}}></div>
                        <div className="w-2 h-2 rounded-full bg-primary animate-pulse" style={{animationDelay: '150ms'}}></div>
                        <div className="w-2 h-2 rounded-full bg-primary animate-pulse" style={{animationDelay: '300ms'}}></div>
                      </div>
                    </div>
                  )}
                  {error && <div className="p-3 bg-error-container text-on-error-container rounded text-sm text-center">{error}</div>}
                  <div ref={messagesEndRef} />
                </div>
              </div>

              <div className="p-6 bg-surface-container-lowest border-t border-surface-variant">
                <div className="max-w-3xl mx-auto relative">
                  <textarea
                    ref={textareaRef}
                    className="w-full bg-surface resize-none border border-surface-variant rounded-lg p-4 pr-16 font-body-md text-on-surface focus:border-primary-container focus:ring-1 focus:ring-primary-container outline-none transition-all shadow-sm"
                    placeholder="Đặt câu hỏi tiếp theo hoặc yêu cầu trích xuất điều khoản..."
                    value={input}
                    onChange={e => setInput(e.target.value)}
                    onKeyDown={handleKeyDown}
                    disabled={loading}
                    rows={3}
                  />
                  <button 
                    onClick={handleSend}
                    disabled={loading || !input.trim()}
                    className="absolute right-4 bottom-4 w-10 h-10 bg-primary-container text-on-primary rounded-full flex items-center justify-center hover:bg-primary transition-colors shadow-sm disabled:opacity-50 disabled:cursor-not-allowed">
                    <span className="material-symbols-outlined">send</span>
                  </button>
                  <div className="absolute left-4 bottom-4 flex gap-2">
                    <button className="text-outline hover:text-primary-container transition-colors p-1"><span className="material-symbols-outlined text-[20px]">attach_file</span></button>
                    <span className="text-xs text-outline absolute left-8 top-1.5 whitespace-nowrap">{charCount} ký tự</span>
                  </div>
                </div>
              </div>
            </section>

            {lawSidebar.open && (
              <aside className="w-80 border-l border-surface-variant bg-surface-container-lowest flex flex-col z-20">
                <div className="p-4 border-b border-surface-variant flex justify-between items-center bg-surface-bright">
                  <h3 className="font-label-sm text-label-sm text-on-surface-variant uppercase tracking-wider flex items-center gap-2">
                    <span className="material-symbols-outlined text-[16px]">menu_book</span> Văn bản Tham chiếu
                  </h3>
                  <button onClick={closeLawSidebar} className="text-outline hover:text-on-surface">
                    <span className="material-symbols-outlined text-[18px]">close</span>
                  </button>
                </div>
                <div className="p-0 overflow-y-auto flex-1 relative">
                  <LawSidebar lawData={lawSidebar.data} loading={lawSidebar.loading} error={lawSidebar.error} onClose={closeLawSidebar} isEmbedded={true} />
                </div>
              </aside>
            )}
          </div>
        </main>

        {showRoleModal && (
          <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-[100] flex items-center justify-center" onClick={() => setShowRoleModal(false)}>
            <div className="bg-surface-container-lowest p-6 rounded-lg max-w-md w-full shadow-lg" onClick={e => e.stopPropagation()}>
              <h3 className="font-h3 text-xl mb-2 text-on-surface">Chọn vai trò phân tích</h3>
              <p className="text-sm text-on-surface-variant mb-6">
                {pendingContent ? 'Chọn vai trò trước khi bắt đầu. Vai trò sẽ không thể thay đổi sau khi phiên bắt đầu.' : 'Vai trò sẽ được khóa cho toàn bộ phiên hội thoại này.'}
              </p>
              <div className="mb-6">
                 <RoleSelector selected={role} onChange={handleRoleConfirmed} />
              </div>
              <div className="flex justify-end">
                <button className="px-4 py-2 text-sm font-medium text-on-surface-variant hover:text-on-surface hover:bg-surface-container rounded transition-colors" onClick={() => { setShowRoleModal(false); setPendingContent(''); }}>Hủy</button>
              </div>
            </div>
          </div>
        )}
      </div>
    );
}
