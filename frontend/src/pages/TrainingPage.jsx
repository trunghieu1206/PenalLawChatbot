import { useState } from 'react';
import { Link } from 'react-router-dom';
import { practiceApi } from '../services/api.js';
import styles from './TrainingPage.module.css';

const MODES = [
  {
    id: 'neutral',
    label: 'Thẩm phán',
    desc: 'Phân tích khách quan dựa trên chứng cứ',
    badgeClass: 'badge-neutral',
  },
  {
    id: 'defense',
    label: 'Luật sư Bào chữa',
    desc: 'Bảo vệ quyền lợi bị cáo, giảm nhẹ hình phạt',
    badgeClass: 'badge-defense',
  },
  {
    id: 'victim',
    label: 'Luật sư Bị hại',
    desc: 'Bảo vệ bị hại, yêu cầu xử nghiêm',
    badgeClass: 'badge-victim',
  },
];

export default function TrainingPage() {
  const [mode, setMode] = useState('neutral');
  const [caseDesc, setCaseDesc] = useState('');
  const [userAnalysis, setUserAnalysis] = useState('');
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async () => {
    if (!caseDesc.trim()) {
      setError('Vui lòng điền nội dung vụ án.');
      return;
    }
    if (!userAnalysis.trim()) {
      setError('Vui lòng nhập phân tích của bạn trước khi gửi.');
      return;
    }
    setLoading(true);
    setError('');
    setResult(null);
    try {
      const data = await practiceApi.evaluate(caseDesc, mode, userAnalysis);
      setResult(data);
    } catch (err) {
      const msg = err.response?.data?.detail || err.message || 'Đánh giá thất bại. Vui lòng thử lại.';
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  const handleReset = () => {
    setResult(null);
    setError('');
    setUserAnalysis('');
    setCaseDesc('');
  };

  const getScoreClass = (score) => {
    if (score >= 80) return styles.scoreHigh;
    if (score >= 60) return styles.scoreMid;
    return styles.scoreLow;
  };

  const getScoreEmoji = (score) => {
    if (score >= 80) return 'Xuất sắc';
    if (score >= 60) return 'Tốt';
    return 'Cần cải thiện';
  };

  const selectedMode = MODES.find(m => m.id === mode);

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <Link to="/chat" className="btn btn-ghost">← Quay lại Chat</Link>
        <div className={styles.headerTitle}>
          Chế độ Luyện tập
        </div>
        <div />
      </header>

      {/* MODE SELECTOR */}
      <div className={styles.modeBar}>
        <span className={styles.modeBarLabel}>Chọn vai trò luyện tập:</span>
        <div className={styles.modeOptions}>
          {MODES.map(m => (
            <button
              key={m.id}
              className={`${styles.modeOption} ${mode === m.id ? styles.modeOptionActive : ''}`}
              onClick={() => { setMode(m.id); setResult(null); setError(''); }}
              disabled={loading}
              title={m.desc}
            >
              <span className={styles.modeLabel}>{m.label}</span>
              <span className={styles.modeDesc}>{m.desc}</span>
            </button>
          ))}
        </div>
      </div>

      <div className={styles.body}>
        {/* Left: Input */}
        <div className={styles.panel}>
          <div className="card" style={{ padding: 24 }}>
            <h3 className={styles.panelTitle}>Nội dung Vụ án</h3>
            <textarea
              className={styles.textarea}
              placeholder="Dán nội dung hồ sơ vụ án vào đây..."
              value={caseDesc}
              onChange={e => setCaseDesc(e.target.value)}
              rows={10}
              disabled={loading}
            />
          </div>
          <div className="card" style={{ padding: 24, marginTop: 16 }}>
            <h3 className={styles.panelTitle}>
              Phân tích của bạn theo vai trò <span className={`badge ${selectedMode.badgeClass}`}>{selectedMode.label}</span>
            </h3>
            <textarea
              className={styles.textarea}
              placeholder={`Viết phân tích pháp lý theo góc nhìn ${selectedMode.label}...`}
              value={userAnalysis}
              onChange={e => setUserAnalysis(e.target.value)}
              rows={10}
              disabled={loading}
            />
          </div>
          {error && <div className={styles.error}>{error}</div>}
          <div style={{ display: 'flex', gap: 10, marginTop: 12 }}>
            <button
              className="btn btn-primary"
              onClick={handleSubmit}
              disabled={loading}
              style={{ flex: 1, justifyContent: 'center', padding: 13, fontSize: 15 }}
            >
              {loading ? <><span className="loader" /> Đang đánh giá...</> : 'Đánh giá phân tích'}
            </button>
            {result && (
              <button
                className="btn btn-ghost"
                onClick={handleReset}
                style={{ padding: 13, fontSize: 14 }}
              >
                Làm lại
              </button>
            )}
          </div>
        </div>

        {/* Right: Result */}
        <div className={styles.panel}>
          {!result ? (
            <div className={styles.placeholder}>
              <p>Kết quả đánh giá sẽ hiển thị ở đây sau khi bạn gửi phân tích.</p>
              <p style={{ fontSize: 13, opacity: 0.7, marginTop: 8 }}>
                AI sẽ đánh giá từng điểm pháp lý và đưa ra nhận xét chi tiết theo vai trò <strong>{selectedMode.label}</strong>.
              </p>
            </div>
          ) : (
            <div className={`card ${styles.result} animate-fade-in`}>
              <div className={styles.scoreSection}>
                <div className={`${styles.score} ${getScoreClass(result.score)}`}>
                  {result.score}
                </div>
                <div>
                  <div className={styles.scoreLabel}>Điểm số của bạn</div>
                  <div className={styles.scoreDesc}>{getScoreEmoji(result.score)}</div>
                  <div className={styles.scoreRole}>
                    Đánh giá theo vai trò: <span className={`badge ${selectedMode.badgeClass}`}>{selectedMode.label}</span>
                  </div>
                </div>
              </div>

              {result.feedback.strengths?.length > 0 && (
                <section className={styles.section}>
                  <h4 className={styles.sectionTitle} style={{ color: 'var(--success)' }}>Điểm mạnh</h4>
                  <ul className={styles.list}>
                    {result.feedback.strengths.map((s, i) => (
                      <li key={i} className={styles.listItemGood}>{s}</li>
                    ))}
                  </ul>
                </section>
              )}

              {result.feedback.improvements?.length > 0 && (
                <section className={styles.section}>
                  <h4 className={styles.sectionTitle} style={{ color: 'var(--error)' }}>Cần cải thiện</h4>
                  <ul className={styles.list}>
                    {result.feedback.improvements.map((s, i) => (
                      <li key={i} className={styles.listItemBad}>{s}</li>
                    ))}
                  </ul>
                </section>
              )}

              {result.feedback.missed_articles?.length > 0 && (
                <section className={styles.section}>
                  <h4 className={styles.sectionTitle} style={{ color: 'var(--accent)' }}>Điều luật bỏ sót</h4>
                  <div className={styles.pills}>
                    {result.feedback.missed_articles.map((a, i) => (
                      <span key={i} className="law-citation">{a}</span>
                    ))}
                  </div>
                </section>
              )}

              {result.feedback.suggestion && (
                <section className={styles.section}>
                  <h4 className={styles.sectionTitle}>Gợi ý</h4>
                  <p className={styles.suggestion}>{result.feedback.suggestion}</p>
                </section>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
