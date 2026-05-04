import { useEffect, useState } from 'react';
import { adminApi } from '../services/api.js';
import styles from './AdminPage.module.css';

const ROLE_LABEL = { neutral: 'Thẩm phán', defense: 'Luật sư Bào chữa', victim: 'Luật sư Bị hại' };

function FeedbackList({ items, loading }) {
  const [expanded, setExpanded] = useState({});

  if (loading) {
    return (
      <div className={styles.loadingState}>
        <div className={styles.spinner} />
        <span>Đang tải phản hồi...</span>
      </div>
    );
  }

  if (!items || items.length === 0) {
    return (
      <div className={styles.emptyState}>
        <span className={styles.emptyIcon}>📭</span>
        <p>Chưa có phản hồi nào từ người dùng.</p>
      </div>
    );
  }

  const toggle = (id) => setExpanded(p => ({ ...p, [id]: !p[id] }));

  return (
    <div className={styles.feedbackList}>
      {items.map(f => (
        <div key={f.id} className={`${styles.feedbackItem} ${f.is_correct ? styles.feedbackOk : styles.feedbackBad}`}>
          <div className={styles.feedbackMeta}>
            <span className={`${styles.feedbackVote} ${f.is_correct ? styles.voteOk : styles.voteBad}`}>
              {f.is_correct ? '👍 Chính xác' : '👎 Không chính xác'}
            </span>
            <span className={styles.feedbackRole}>
              {ROLE_LABEL[f.session_mode] || f.session_mode || '—'}
            </span>
            <span className={styles.feedbackDate}>
              {new Date(f.created_at).toLocaleString('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh' })}
            </span>
            <button className={styles.expandBtn} onClick={() => toggle(f.id)}>
              {expanded[f.id] ? '▲ Thu gọn' : '▼ Xem hội thoại'}
            </button>
          </div>

          {f.comment && (
            <div className={styles.feedbackComment}>
              <span className={styles.commentIcon}>💬</span> {f.comment}
            </div>
          )}

          {expanded[f.id] && (
            <div className={styles.conversation}>
              {(f.conversation || []).map((msg, i) => (
                <div key={msg.id || i} className={`${styles.convMsg} ${msg.role === 'user' ? styles.convUser : styles.convAI}`}>
                  <span className={styles.convRole}>{msg.role === 'user' ? 'Người dùng' : 'Trợ lý AI'}</span>
                  <p className={styles.convContent}>{msg.content}</p>
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

export default function AdminPage() {
  const [feedback, setFeedback] = useState([]);
  const [loading, setLoading]   = useState(true);
  const [error, setError]       = useState(null);

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

  return (
    <div className={styles.page}>
      {/* Header */}
      <header className={styles.header}>
        <div className={styles.headerInner}>
          <div className={styles.logo}>
            <span className={styles.logoIcon}>⚖️</span>
            <div>
              <div className={styles.logoTitle}>Quản lý phản hồi</div>
              <div className={styles.logoSub}>Hệ thống Chatbot Pháp luật Hình sự · Chỉ dành cho Admin</div>
            </div>
          </div>
          {total > 0 && (
            <div className={styles.headerStats}>
              <div className={styles.hStat}><span className={styles.hStatVal}>{total}</span><span className={styles.hStatLbl}>Tổng</span></div>
              <div className={`${styles.hStat} ${styles.hStatGreen}`}><span className={styles.hStatVal}>{correct}</span><span className={styles.hStatLbl}>Chính xác</span></div>
              <div className={`${styles.hStat} ${styles.hStatRed}`}><span className={styles.hStatVal}>{incorrect}</span><span className={styles.hStatLbl}>Sai</span></div>
              {accuracy !== null && (
                <div className={styles.hStat}>
                  <div className={styles.accuracyWrap}>
                    <div className={styles.accuracyBar}><div className={styles.accuracyFill} style={{ width: `${accuracy}%` }} /></div>
                    <span className={styles.hStatLbl}>{accuracy}% độ chính xác</span>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </header>

      <main className={styles.main}>
        {error && <div className={styles.errorBanner}>{error}</div>}
        <FeedbackList items={feedback} loading={loading} />
      </main>
    </div>
  );
}
