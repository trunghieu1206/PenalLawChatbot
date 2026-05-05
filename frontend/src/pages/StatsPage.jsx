import { useEffect, useMemo, useState } from 'react';
import Footer from '../components/Footer.jsx';
import Topbar from '../components/Topbar.jsx';
import Sidebar from '../components/Sidebar.jsx';
import { useNavigate } from 'react-router-dom';
import { adminApi } from '../services/api.js';
import { useAuth } from '../hooks/useAuth.jsx';
import styles from './StatsPage.module.css';

const ROLE_LABEL = { neutral: 'Thẩm phán', defense: 'Luật sư Bào chữa', victim: 'Luật sư Bị hại' };
const ROLE_COLOR = { neutral: '#4da6c8', defense: '#a78bfa', victim: '#fb923c' };

function StatCard({ label, value, accent }) {
  return (
    <div className={`${styles.statCard} ${accent ? styles[`accent_${accent}`] : ''} flex flex-col justify-center items-center text-center p-6 gap-2`}>
      <div className="text-sm font-semibold text-slate-500">{label}</div>
      <div className="text-4xl font-bold text-slate-900">{value ?? '—'}</div>
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
      <Sidebar activeTab="home" />

      <main className="ml-64 flex-1 flex flex-col h-screen bg-surface overflow-y-auto pt-16">
        <Topbar />

        <div className={styles.content}>
          <div className={styles.pageHeader}>
            <h1>Dashboard/Thống kê</h1>
          </div>

          <div className={styles.descriptionCard}>
            <div className={styles.descriptionHeader}>
              <span className="material-symbols-outlined text-primary">gavel</span>
              <h3>Về Hệ thống VNPLaw</h3>
            </div>
            <p>
              VNPLaw là hệ thống được phát triển để giải quyết một vụ án hình sự, tích hợp chức năng điều chỉnh góc nhìn (thẩm phán, luật sư bảo vệ bị hại, luật sư bảo vệ bị cáo). Hệ thống hỗ trợ phân tích tình tiết vụ án, trích dẫn điều luật tương ứng trong Bộ luật Hình sự và đưa ra câu trả lời theo từng vai trò.
            </p>
            <div className={styles.featureGrid}>
              <div className={styles.featureItem}>
                <span className="material-symbols-outlined">analytics</span>
                <div>
                  <strong>Giải quyết vụ án</strong>
                  <span>Xử lý vụ án hình sự, trích dẫn điều luật tương ứng theo văn bản pháp luật.</span>
                </div>
              </div>
              <div className={styles.featureItem}>
                <span className="material-symbols-outlined">diversity_3</span>
                <div>
                  <strong>Điều chỉnh góc nhìn</strong>
                  <span>Xử lý vụ án theo 3 góc nhìn: thẩm phán, luật sư bảo vệ bị hại, luật sư bảo vệ bị cáo.</span>
                </div>
              </div>
              <div className={styles.featureItem}>
                <span className="material-symbols-outlined">psychology</span>
                <div>
                  <strong>Luyện tập kỹ năng</strong>
                  <span>Đóng vai là thẩm phán/luật sư để rèn luyện khả năng giải quyết vụ án hình sự.</span>
                </div>
              </div>
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
                <StatCard label="Lượt truy cập" value={stats.total_sessions} accent="blue" />
                <StatCard label="Người dùng đã đăng ký" value={stats.total_users} accent="teal" />
                <StatCard label="Vụ án đã phân tích" value={stats.cases_processed} accent="purple" />
                <StatCard label="Phản hồi nhận được" value={stats.feedback_total} accent="orange" />
              </div>

              {stats.feedback_total > 0 && (
                <div className={styles.accuracyRow}>
                  <span>
                    Độ chính xác phản hồi: <strong>{accuracy}%</strong>
                    &ensp;({stats.feedback_correct} / {stats.feedback_total})
                  </span>
                  <div className={styles.accuracyBar}>
                    <div className={styles.accuracyFill} style={{ width: `${accuracy}%` }} />
                  </div>
                </div>
              )}

              <section className={styles.section}>
                <h2>Lượt câu trả lời đưa ra theo vai trò</h2>
                <div className={styles.roleGrid}>
                  {['neutral', 'defense', 'victim'].map(r => (
                    <div key={r} className={styles.roleCard} style={{ borderColor: `${ROLE_COLOR[r]}44`, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '8px', padding: '24px' }}>
                      <div className="text-sm font-semibold text-slate-500">{ROLE_LABEL[r]}</div>
                      <div className="text-4xl font-bold" style={{ color: ROLE_COLOR[r] }}>
                        {stats.by_role?.[r] ?? 0}
                      </div>
                    </div>
                  ))}
                </div>
              </section>

              <div className={styles.chartsRow}>
                <BarChart
                  title="Thống kê theo tỉnh thành"
                  data={stats.by_province}
                  color="linear-gradient(90deg,#4f6073,#7c93ab)"
                />
                <BarChart
                  title="Thống kê theo tội danh"
                  data={stats.by_crime_type}
                  color="linear-gradient(90deg,#775a19,#c5a059)"
                />
              </div>
            </>
          )}
          
          <div className="mt-12 flex justify-center pb-12">
            <button onClick={() => navigate('/chat')} className="bg-primary text-on-primary px-8 py-3 rounded-full text-lg font-bold shadow-md hover:bg-primary-container hover:text-on-primary-container transition-colors">
              Giải quyết vụ án ngay
            </button>
          </div>
        </div>
        <Footer />
      </main>
    </div>
  );
}
