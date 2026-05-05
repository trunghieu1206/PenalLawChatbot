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

  useEffect(() => {
    if (!selectedId && feedback.length > 0) {
      setSelectedId(feedback[0].id);
    }
  }, [feedback, selectedId]);

  const selected = feedback.find(item => item.id === selectedId) || null;
  const formatDate = (dateStr) => new Date(dateStr).toLocaleString('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh' });
  const shortCaseId = (value) => value ? `#${String(value).slice(0, 6)}` : '—';

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
                  <button type="button"><span className="material-symbols-outlined">filter_list</span></button>
                  <button type="button"><span className="material-symbols-outlined">download</span></button>
                </div>
              </div>

              <div className={styles.listHeader}>
                <span>Mã vụ án</span>
                <span>Vai trò / Người dùng</span>
                <span className={styles.centered}>Phản hồi</span>
                <span>Đoạn bình luận</span>
              </div>

              {loading ? (
                <div className={styles.loadingState}><span className="loader" /> Đang tải phản hồi...</div>
              ) : (
                <div className={styles.listBody}>
                  {feedback.map(item => (
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
                      <span className={styles.flagBadge}>Cần xem xét</span>
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
                       <button className="btn btn-primary" type="button">Giải quyết</button>
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
