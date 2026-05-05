import { useEffect, useMemo, useState } from 'react';
import Sidebar from '../components/Sidebar.jsx';
import { useNavigate } from 'react-router-dom';
import { adminApi } from '../services/api.js';
import { useAuth } from '../hooks/useAuth.jsx';
import styles from './StatsPage.module.css';

const ROLE_LABEL = { neutral: 'Thẩm phán', defense: 'Luật sư Bào chữa', victim: 'Luật sư Bị hại' };
const ROLE_COLOR = { neutral: '#4da6c8', defense: '#a78bfa', victim: '#fb923c' };

function StatCard({ label, value, icon, accent }) {
  return (
    <div className={`${styles.statCard} ${accent ? styles[`accent_${accent}`] : ''}`}>
      <span className={styles.statIcon}>{icon}</span>
      <div className={styles.statValue}>{value ?? '—'}</div>
      <div className={styles.statLabel}>{label}</div>
    </div>
  );
}

function BarChart({ title, data, color }) {
  const sorted = useMemo(() =>
    Object.entries(data || {}).sort((a, b) => b[1] - a[1]).slice(0, 15),
  [data]);

  if (sorted.length === 0) {
    return (
      <div className={styles.chartCard}>
        <h3 className={styles.chartTitle}>{title}</h3>
        <p className={styles.emptyNote}>Chưa có dữ liệu.</p>
      </div>
    );
  }
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
                className={styles.barFill}
                style={{ width: `${(count / max) * 100}%`, background: color }}
              />
            </div>
            <span className={styles.barCount}>{count}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function StatsPage() {
  const navigate   = useNavigate();
  const { user }   = useAuth();
  const [stats, setStats]   = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]   = useState(null);

  useEffect(() => {
    adminApi.getStats()
      .then(s => { setStats(s); setLoading(false); })
      .catch(e => { setError('Không thể tải thống kê. ' + (e.message || '')); setLoading(false); });
  }, []);

  const accuracy = stats && stats.feedback_total > 0
    ? Math.round((stats.feedback_correct / stats.feedback_total) * 100)
    : null;

  return (
    <div className="bg-background text-on-background font-body-md text-body-md h-full min-h-screen flex overflow-hidden">
      <Sidebar activeTab="stats" />

      <main className="ml-64 flex-1 flex flex-col h-screen bg-surface overflow-y-auto pt-16">
        <header className="bg-white/80 backdrop-blur-md fixed top-0 right-0 w-[calc(100%-16rem)] z-40 border-b border-surface-variant flex justify-between items-center h-16 px-8 transition-all duration-300">
                <div className="flex items-center gap-6">
                  <span className="text-lg font-black text-slate-900 font-h3">VNPLaw</span>
                </div>
                <div className="flex items-center gap-4">
                  <button className="bg-surface-container text-on-surface text-sm font-semibold px-4 py-2 rounded hover:bg-surface-container-high transition-colors" type="button">Xuất báo cáo</button>
                </div>
              </header>

        <div className={styles.content}>
          <div className={styles.pageHeader}>
            <div>
              <h1>Dashboard/Thống kê</h1>
              <p>System metrics and operational overview.</p>
            </div>
          </div>

          {loading && (
            <div className={styles.loadingState}>
              <div className={styles.spinner} />
              <span>Đang tải thống kê...</span>
            </div>
          )}
          {error && <div className={styles.errorBanner}>{error}</div>}

          {!loading && !error && stats && (
            <>
              <div className={styles.statGrid}>
                <StatCard icon="💬" label="Tổng phiên làm việc" value={stats.total_sessions} accent="blue" />
                <StatCard icon="👤" label="Người dùng đã đăng ký" value={stats.total_users} accent="teal" />
                <StatCard icon="⚖️" label="Vụ án đã phân tích" value={stats.cases_processed} accent="purple" />
                <StatCard icon="📝" label="Phản hồi nhận được" value={stats.feedback_total} accent="orange" />
              </div>

              {stats.feedback_total > 0 && (
                <div className={styles.accuracyRow}>
                  <span>
                    Độ chính xác phản hồi: <strong>{accuracy}%</strong>
                    &ensp;({stats.feedback_correct} chính xác / {stats.feedback_incorrect} sai)
                  </span>
                  <div className={styles.accuracyBar}>
                    <div className={styles.accuracyFill} style={{ width: `${accuracy}%` }} />
                  </div>
                </div>
              )}

              <section className={styles.section}>
                <h2>Phân bổ Vai trò</h2>
                <div className={styles.roleGrid}>
                  {['neutral', 'defense', 'victim'].map(r => (
                    <div key={r} className={styles.roleCard} style={{ borderColor: `${ROLE_COLOR[r]}44` }}>
                      <div className={styles.roleCount} style={{ color: ROLE_COLOR[r] }}>
                        {stats.by_role?.[r] ?? 0}
                      </div>
                      <div className={styles.roleLabel}>{ROLE_LABEL[r]}</div>
                    </div>
                  ))}
                </div>
              </section>

              <div className={styles.chartsRow}>
                <BarChart
                  title="Vụ án theo địa danh (tỉnh/thành)"
                  data={stats.by_province}
                  color="linear-gradient(90deg,#4f6073,#7c93ab)"
                />
                <BarChart
                  title="Vụ án theo loại tội danh"
                  data={stats.by_crime_type}
                  color="linear-gradient(90deg,#775a19,#c5a059)"
                />
              </div>
            </>
          )}
        </div>
      </main>
    </div>
  );
}
