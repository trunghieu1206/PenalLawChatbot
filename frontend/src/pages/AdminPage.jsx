import { useEffect, useState } from 'react';
import { adminApi } from '../services/api.js';
import styles from './AdminPage.module.css';

const ROLE_LABEL = { neutral: 'Thẩm phán', defense: 'Luật sư Bào chữa', victim: 'Luật sư Bị hại' };

function StatCard({ label, value, sub, accent }) {
  return (
    <div className={`${styles.statCard} ${accent ? styles[`accent_${accent}`] : ''}`}>
      <div className={styles.statValue}>{value ?? '—'}</div>
      <div className={styles.statLabel}>{label}</div>
      {sub && <div className={styles.statSub}>{sub}</div>}
    </div>
  );
}

function BarChart({ title, data, colorClass }) {
  if (!data || Object.keys(data).length === 0) {
    return (
      <div className={styles.chartCard}>
        <h3 className={styles.chartTitle}>{title}</h3>
        <p className={styles.emptyNote}>Chưa có dữ liệu.</p>
      </div>
    );
  }
  const sorted = Object.entries(data).sort((a, b) => b[1] - a[1]).slice(0, 15);
  const max = sorted[0][1] || 1;
  return (
    <div className={styles.chartCard}>
      <h3 className={styles.chartTitle}>{title}</h3>
      <div className={styles.barList}>
        {sorted.map(([label, count]) => (
          <div key={label} className={styles.barRow}>
            <span className={styles.barLabel} title={label}>{label}</span>
            <div className={styles.barTrack}>
              <div
                className={`${styles.barFill} ${styles[colorClass] || ''}`}
                style={{ width: `${(count / max) * 100}%` }}
              />
            </div>
            <span className={styles.barCount}>{count}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function FeedbackList({ items }) {
  const [expanded, setExpanded] = useState({});
  if (!items || items.length === 0) {
    return <p className={styles.emptyNote}>Chưa có phản hồi nào.</p>;
  }
  const toggle = (id) => setExpanded(p => ({ ...p, [id]: !p[id] }));

  return (
    <div className={styles.feedbackList}>
      {items.map(f => (
        <div key={f.id} className={`${styles.feedbackItem} ${f.is_correct ? styles.feedbackOk : styles.feedbackBad}`}>
          <div className={styles.feedbackMeta}>
            <span className={styles.feedbackVote}>{f.is_correct ? '👍 Chính xác' : '👎 Không chính xác'}</span>
            <span className={styles.feedbackRole}>{ROLE_LABEL[f.session_mode] || f.session_mode}</span>
            <span className={styles.feedbackDate}>{new Date(f.created_at).toLocaleString('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh' })}</span>
            <button className={styles.expandBtn} onClick={() => toggle(f.id)}>
              {expanded[f.id] ? 'Thu gọn ▲' : 'Xem hội thoại ▼'}
            </button>
          </div>
          {f.comment && <div className={styles.feedbackComment}>💬 {f.comment}</div>}
          {expanded[f.id] && (
            <div className={styles.conversation}>
              {(f.conversation || []).map(msg => (
                <div key={msg.id} className={`${styles.convMsg} ${msg.role === 'user' ? styles.convUser : styles.convAI}`}>
                  <span className={styles.convRole}>{msg.role === 'user' ? 'Người dùng' : 'Trợ lý'}</span>
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
  const [stats, setStats]       = useState(null);
  const [feedback, setFeedback] = useState([]);
  const [loading, setLoading]   = useState(true);
  const [error, setError]       = useState(null);
  const [tab, setTab]           = useState('stats'); // 'stats' | 'feedback'

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      setError(null);
      try {
        const [s, f] = await Promise.all([adminApi.getStats(), adminApi.getFeedback()]);
        setStats(s);
        setFeedback(f);
      } catch (e) {
        setError('Không thể tải dữ liệu. ' + (e.message || ''));
      } finally {
        setLoading(false);
      }
    };
    load();
  }, []);

  const accuracy = stats && stats.feedback_total > 0
    ? Math.round((stats.feedback_correct / stats.feedback_total) * 100)
    : null;

  return (
    <div className={styles.page}>
      {/* Header */}
      <header className={styles.header}>
        <div className={styles.headerInner}>
          <div className={styles.logo}>
            <span className={styles.logoIcon}>⚖️</span>
            <div>
              <div className={styles.logoTitle}>Bảng điều khiển Admin</div>
              <div className={styles.logoSub}>Hệ thống Chatbot Pháp luật Hình sự</div>
            </div>
          </div>
          <nav className={styles.tabs}>
            <button className={`${styles.tab} ${tab === 'stats' ? styles.tabActive : ''}`} onClick={() => setTab('stats')}>
              📊 Thống kê
            </button>
            <button className={`${styles.tab} ${tab === 'feedback' ? styles.tabActive : ''}`} onClick={() => setTab('feedback')}>
              💬 Phản hồi {feedback.length > 0 && <span className={styles.badge}>{feedback.length}</span>}
            </button>
          </nav>
        </div>
      </header>

      <main className={styles.main}>
        {loading && (
          <div className={styles.loadingState}>
            <div className={styles.spinner} />
            <span>Đang tải dữ liệu...</span>
          </div>
        )}

        {error && <div className={styles.errorBanner}>{error}</div>}

        {!loading && !error && stats && tab === 'stats' && (
          <>
            {/* Stat cards row */}
            <div className={styles.statGrid}>
              <StatCard label="Tổng phiên làm việc"   value={stats.total_sessions}   accent="blue" />
              <StatCard label="Người dùng đã đăng ký" value={stats.total_users}       accent="teal" />
              <StatCard label="Vụ án đã phân tích"    value={stats.cases_processed}   accent="purple" />
              <StatCard label="Phản hồi nhận được"    value={stats.feedback_total}    accent="orange"
                sub={accuracy !== null ? `${accuracy}% chính xác` : null} />
            </div>

            {/* Feedback mini-stats */}
            {stats.feedback_total > 0 && (
              <div className={styles.feedbackSummaryRow}>
                <div className={styles.feedbackSummaryCard}>
                  <span className={styles.feedbackOkDot} /> {stats.feedback_correct} Chính xác
                </div>
                <div className={styles.feedbackSummaryCard}>
                  <span className={styles.feedbackBadDot} /> {stats.feedback_incorrect} Không chính xác
                </div>
                <div className={styles.feedbackSummaryCard}>
                  <div className={styles.accuracyBar}>
                    <div className={styles.accuracyFill} style={{ width: `${accuracy}%` }} />
                  </div>
                  <span>{accuracy}% độ chính xác</span>
                </div>
              </div>
            )}

            {/* Role breakdown */}
            <section className={styles.section}>
              <h2 className={styles.sectionTitle}>Phân bố theo vai trò</h2>
              <div className={styles.roleGrid}>
                {['neutral', 'defense', 'victim'].map(r => (
                  <div key={r} className={`${styles.roleCard} ${styles[`role_${r}`]}`}>
                    <div className={styles.roleCount}>{stats.by_role?.[r] ?? 0}</div>
                    <div className={styles.roleLabel}>{ROLE_LABEL[r]}</div>
                  </div>
                ))}
              </div>
            </section>

            {/* Charts */}
            <div className={styles.chartsRow}>
              <BarChart title="Vụ án theo địa danh" data={stats.by_province}  colorClass="barBlue" />
              <BarChart title="Vụ án theo loại tội" data={stats.by_crime_type} colorClass="barPurple" />
            </div>
          </>
        )}

        {!loading && !error && tab === 'feedback' && (
          <section className={styles.section}>
            <h2 className={styles.sectionTitle}>Lịch sử phản hồi ({feedback.length})</h2>
            <FeedbackList items={feedback} />
          </section>
        )}
      </main>
    </div>
  );
}
