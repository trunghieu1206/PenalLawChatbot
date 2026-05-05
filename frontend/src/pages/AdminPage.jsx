import { useEffect, useState } from 'react';
import Sidebar from '../components/Sidebar.jsx';
import { useNavigate } from 'react-router-dom';
import { adminApi } from '../services/api.js';
import styles from './AdminPage.module.css';

const ROLE_LABEL = { neutral: 'Thẩm phán', defense: 'Luật sư Bào chữa', victim: 'Luật sư Bị hại' };

export default function AdminPage() {
  const navigate = useNavigate();
  const [feedback, setFeedback] = useState([]);
  const [loading, setLoading]   = useState(true);
  const [error, setError]       = useState(null);
  const [selectedId, setSelectedId] = useState(null);
  const [showConversation, setShowConversation] = useState(false);

  // Tab state
  const [activeTab, setActiveTab] = useState('feedback'); // 'feedback' | 'users'
  const [userStats, setUserStats] = useState([]);
  const [loadingUsers, setLoadingUsers] = useState(false);

  // Status filter
  const [statusFilter, setStatusFilter] = useState('all'); // 'all' | 'can_xem_xet' | 'da_xem_xet'
  const filteredFeedback = statusFilter === 'all'
    ? feedback
    : feedback.filter(f => (f.status || 'can_xem_xet') === statusFilter);

  // Summary numbers
  const total     = feedback.length;
  const correct   = feedback.filter(f => f.is_correct).length;
  const incorrect = feedback.filter(f => !f.is_correct).length;
  const accuracy  = total > 0 ? Math.round((correct / total) * 100) : null;

  useEffect(() => {
    adminApi.getFeedback()
      .then(data => { setFeedback(data); setLoading(false); })
      .catch(e => { setError('Không thể tải phản hồi. ' + (e.message || '')); setLoading(false); });
  }, []);

  // Lazy-load user stats when that tab is first opened
  useEffect(() => {
    if (activeTab === 'users' && userStats.length === 0 && !loadingUsers) {
      setLoadingUsers(true);
      adminApi.getUserCaseStats()
        .then(data => { setUserStats(data); setLoadingUsers(false); })
        .catch(() => setLoadingUsers(false));
    }
  }, [activeTab]);

  useEffect(() => {
    if (!selectedId && feedback.length > 0) {
      setSelectedId(feedback[0].id);
    }
  }, [feedback, selectedId]);

  const selected = feedback.find(item => item.id === selectedId) || null;
  const formatDate = (dateStr) => new Date(dateStr).toLocaleString('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh' });
  const shortCaseId = (value) => value ? `#${String(value).slice(0, 6)}` : '—';

  /** Mark the currently selected feedback as \"reviewed\". */
  const handleResolve = async () => {
    if (!selected) return;
    try {
      await adminApi.updateFeedbackStatus(selected.id, 'da_xem_xet');
      setFeedback(prev => prev.map(f =>
        f.id === selected.id ? { ...f, status: 'da_xem_xet' } : f
      ));
    } catch (err) {
      console.error('Failed to update feedback status:', err);
    }
  };

  /** Mark the currently selected feedback back to \"needs review\". */
  const handleUnresolve = async () => {
    if (!selected) return;
    try {
      await adminApi.updateFeedbackStatus(selected.id, 'can_xem_xet');
      setFeedback(prev => prev.map(f =>
        f.id === selected.id ? { ...f, status: 'can_xem_xet' } : f
      ));
    } catch (err) {
      console.error('Failed to update feedback status:', err);
    }
  };

  const isResolved = selected?.status === 'da_xem_xet';

  return (
    <div className="bg-background text-on-background font-body-md text-body-md h-full min-h-screen flex overflow-hidden">
      <Sidebar activeTab="admin" />

      <main className="ml-64 flex-1 flex flex-col h-screen bg-surface overflow-y-auto pt-16">
        <header className="bg-white/80 backdrop-blur-md fixed top-0 right-0 w-[calc(100%-16rem)] z-40 border-b border-surface-variant flex justify-between items-center h-16 px-8 transition-all duration-300">
                <div className="flex items-center gap-6">
                  <span className="text-lg font-black text-slate-900 font-h3">VNPLaw</span>
                </div>
              </header>

        <div className={styles.content}>
          <div className={styles.pageHeader}>
            <h1>Quản lý phản hồi</h1>
            <p>Xem xét và quản lý phản hồi của người dùng về tính chính xác của câu trả lời AI để cải thiện độ chính xác của hệ thống.</p>
          </div>

          {error && <div className={styles.errorBanner}>{error}</div>}

          {/* ── Tab bar ─────────────────────────────────────────── */}
          <div className={styles.tabBar}>
            <button
              type="button"
              className={`${styles.tabBtn} ${activeTab === 'feedback' ? styles.tabBtnActive : ''}`}
              onClick={() => setActiveTab('feedback')}
            >
              <span className="material-symbols-outlined">reviews</span>
              Phản hồi
            </button>
            <button
              type="button"
              className={`${styles.tabBtn} ${activeTab === 'users' ? styles.tabBtnActive : ''}`}
              onClick={() => setActiveTab('users')}
            >
              <span className="material-symbols-outlined">manage_accounts</span>
              Thống kê vụ án
            </button>
          </div>

          {activeTab === 'feedback' && (
          <div className={styles.grid}>
            <section className={styles.listPanel}>
              <div className={styles.toolbar}>
                <div className={styles.sorter}>
                  <span>Sắp xếp theo:</span>
                  <select>
                    <option>Mới nhất</option>
                    <option>Cũ nhất</option>
                    <option>Trạng thái</option>
                  </select>
                </div>
                <div className={styles.toolbarActions}>
                  <button type="button"><span className="material-symbols-outlined">download</span></button>
                </div>
              </div>

              {/* Filter pills */}
              <div className={styles.filterPills}>
                <button
                  type="button"
                  className={`${styles.filterPill} ${statusFilter === 'all' ? styles.filterPillActive : ''}`}
                  onClick={() => setStatusFilter('all')}
                >
                  Tất cả
                  <span className={styles.filterCount}>{feedback.length}</span>
                </button>
                <button
                  type="button"
                  className={`${styles.filterPill} ${styles.filterPillReview} ${statusFilter === 'can_xem_xet' ? styles.filterPillReviewActive : ''}`}
                  onClick={() => setStatusFilter('can_xem_xet')}
                >
                  Cần xem xét
                  <span className={styles.filterCount}>{feedback.filter(f => (f.status || 'can_xem_xet') === 'can_xem_xet').length}</span>
                </button>
                <button
                  type="button"
                  className={`${styles.filterPill} ${styles.filterPillDone} ${statusFilter === 'da_xem_xet' ? styles.filterPillDoneActive : ''}`}
                  onClick={() => setStatusFilter('da_xem_xet')}
                >
                  Đã xem xét
                  <span className={styles.filterCount}>{feedback.filter(f => f.status === 'da_xem_xet').length}</span>
                </button>
              </div>

              <div className={styles.listHeader}>
                <span>Mã vụ án</span>
                <span>Vai trò / Người dùng</span>
                <span className={styles.centered}>Phản hồi</span>
                <span>Trạng thái</span>
                <span>Đoạn bình luận</span>
              </div>

              {loading ? (
                <div className={styles.loadingState}><span className="loader" /> Đang tải phản hồi...</div>
              ) : (
                <div className={styles.listBody}>
                  {filteredFeedback.length === 0 ? (
                    <div className={styles.emptyPanel} style={{padding:'24px',textAlign:'center'}}>
                      Không có phản hồi nào.
                    </div>
                  ) : filteredFeedback.map(item => (
                    <button
                      key={item.id}
                      className={`${styles.listItem} ${selectedId === item.id ? styles.listItemActive : ''}`}
                      onClick={() => setSelectedId(item.id)}
                      type="button"
                    >
                      <span className={styles.caseId}>{shortCaseId(item.session_id || item.id)}</span>
                      <span className={styles.userCell}>
                        <strong>{ROLE_LABEL[item.session_mode] || item.session_mode || '—'}</strong>
                        <span>{item.user_name || item.user_email || 'Người dùng'}</span>
                      </span>
                      <span className={styles.centered}>
                        <span className={item.is_correct ? styles.voteGood : styles.voteBad}>
                          <span className="material-symbols-outlined">{item.is_correct ? 'thumb_up' : 'thumb_down'}</span>
                        </span>
                      </span>
                      <span>
                        {(item.status || 'can_xem_xet') === 'da_xem_xet' ? (
                          <span className={styles.statusDone}>Đã xem xét</span>
                        ) : (
                          <span className={styles.statusReview}>Cần xem xét</span>
                        )}
                      </span>
                      <span className={styles.commentCell}>
                        {item.comment || 'Không có bình luận.'}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </section>

            <section className={styles.detailPanel}>
              {selected ? (
                <>
                  <div className={styles.detailHeader}>
                    <div>
                      <span className={isResolved ? styles.flagBadgeDone : styles.flagBadge}>
                        {isResolved ? 'Đã xem xét' : 'Cần xem xét'}
                      </span>
                      <h3>Chi tiết phản hồi</h3>
                      <p>{shortCaseId(selected.session_id || selected.id)} • {formatDate(selected.created_at)}</p>
                    </div>
                    <button type="button" className={styles.iconBtn}>
                      <span className="material-symbols-outlined">more_vert</span>
                    </button>
                  </div>

                  <div className={styles.detailBody}>
                    <div className={styles.detailUser}>
                      <div className={styles.userAvatar}>{(selected.user_name || 'TV')[0]}</div>
                      <div>
                        <p>{selected.user_name || 'Người dùng'} <span>({ROLE_LABEL[selected.session_mode] || selected.session_mode || '—'})</span></p>
                        <p>{selected.user_email || '—'}</p>
                      </div>
                    </div>

                    <div>
                      <h4>Bình luận</h4>
                      <div className={styles.commentBox}>
                        {selected.comment || 'Không có bình luận.'}
                      </div>
                    </div>

                    <div className={styles.detailActions}>
                       {isResolved ? (
                         <button className="btn btn-outline" type="button" onClick={handleUnresolve}>
                           Đánh dấu cần xem lại
                         </button>
                       ) : (
                         <button className="btn btn-primary" type="button" onClick={handleResolve}>
                           Giải quyết
                         </button>
                       )}
                       <button className="btn btn-outline" type="button">Bỏ qua</button>
                       <button
                         className={styles.viewChatBtn}
                         type="button"
                         onClick={() => setShowConversation(true)}
                       >
                         <span className="material-symbols-outlined">forum</span>
                         Xem phiên chat
                       </button>
                     </div>
                  </div>
                </>
              ) : (
                <div className={styles.emptyPanel}>Chọn phản hồi để xem chi tiết.</div>
              )}
            </section>
          </div>
          )} {/* end activeTab === 'feedback' */}

          {/* ── User stats tab ──────────────────────────────────── */}
          {activeTab === 'users' && (
            <div className={styles.userStatsPanel}>
              <div className={styles.userStatsHeader}>
                <h2>Thống kê vụ án theo người dùng</h2>
                <p>Giới hạn: Khách <strong>3 vụ/ngày</strong> • Đăng nhập <strong>5 vụ/ngày</strong> • Admin <strong>không giới hạn</strong></p>
              </div>
              {loadingUsers ? (
                <div className={styles.loadingState}><span className="loader" /> Đang tải...</div>
              ) : userStats.length === 0 ? (
                <div className={styles.emptyPanel}>Chưa có dữ liệu.</div>
              ) : (
                <div className={styles.tableWrap}>
                  <table className={styles.statsTable}>
                    <thead>
                      <tr>
                        <th>Người dùng</th>
                        <th>Email</th>
                        <th>Vai trò</th>
                        <th>Hôm nay</th>
                        <th>Tổng vụ án</th>
                      </tr>
                    </thead>
                    <tbody>
                      {userStats.map(u => {
                        const limit = u.role === 'admin' ? null : 5;
                        const pct   = limit ? Math.min((u.cases_today / limit) * 100, 100) : 0;
                        const atLimit = limit && u.cases_today >= limit;
                        return (
                          <tr key={u.user_id}>
                            <td>
                              <div className={styles.userCellRow}>
                                <div className={styles.userAvatar}>{(u.full_name || u.email || 'U')[0].toUpperCase()}</div>
                                <span>{u.full_name || '—'}</span>
                              </div>
                            </td>
                            <td className={styles.emailCell}>{u.email}</td>
                            <td>
                              <span className={`${styles.rolePill} ${u.role === 'admin' ? styles.rolePillAdmin : styles.rolePillUser}`}>
                                {u.role === 'admin' ? 'Quản trị viên' : 'Người dùng'}
                              </span>
                            </td>
                            <td>
                              {limit ? (
                                <div className={styles.usageWrap}>
                                  <div className={styles.usageBar}>
                                    <div
                                      className={`${styles.usageFill} ${atLimit ? styles.usageFillFull : ''}`}
                                      style={{ width: `${pct}%` }}
                                    />
                                  </div>
                                  <span className={atLimit ? styles.usageLimitReached : ''}>
                                    {u.cases_today}&nbsp;/&nbsp;{limit}
                                  </span>
                                </div>
                              ) : (
                                <span className={styles.unlimitedBadge}>{u.cases_today} ∞</span>
                              )}
                            </td>
                            <td className={styles.totalCell}>{u.total_cases}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </div>
      </main>

      {/* ── Conversation Modal ───────────────────────────────────── */}
      {showConversation && selected && (
        <div className={styles.modalOverlay} onClick={() => setShowConversation(false)}>
          <div className={styles.modalPanel} onClick={e => e.stopPropagation()}>
            <div className={styles.modalHeader}>
              <div>
                <h3>Phiên chat {shortCaseId(selected.session_id || selected.id)}</h3>
                <p>{ROLE_LABEL[selected.session_mode] || selected.session_mode} • {formatDate(selected.created_at)}</p>
              </div>
              <button type="button" className={styles.iconBtn} onClick={() => setShowConversation(false)}>
                <span className="material-symbols-outlined">close</span>
              </button>
            </div>

            <div className={styles.conversationBody}>
              {(!selected.conversation || selected.conversation.length === 0) ? (
                <div className={styles.emptyConversation}>Không có tin nhắn trong phiên này.</div>
              ) : (
                selected.conversation.map(msg => (
                  <div key={msg.id} className={styles.messageRow}>
                    <div
                      className={`${
                        msg.role === 'user' ? styles.messageBubbleUser : styles.messageBubbleAssistant
                      }${msg.id === selected.message_id ? ` ${styles.messageBubbleRated}` : ''}`}
                    >
                      <div className={styles.messageRoleLabel}>
                        {msg.role === 'user'
                          ? (ROLE_LABEL[selected.session_mode] || 'Người dùng')
                          : '🤖 AI'}
                      </div>
                      <div className={styles.messageContent}>{msg.content}</div>
                      <div className={styles.messageTime}>{formatDate(msg.createdAt)}</div>
                    </div>
                    {msg.id === selected.message_id && (
                      <div className={`${styles.ratedBadge} ${selected.is_correct ? styles.ratedGood : styles.ratedBad}`}>
                        <span className="material-symbols-outlined">
                          {selected.is_correct ? 'thumb_up' : 'thumb_down'}
                        </span>
                        Tin nhắn được đánh giá
                      </div>
                    )}
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
