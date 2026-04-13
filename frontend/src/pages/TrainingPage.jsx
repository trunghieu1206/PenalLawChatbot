import { useState } from 'react';
import { Link } from 'react-router-dom';
import { practiceApi, lawsApi } from '../services/api.js';
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

// Regex to extract article number and source from law citation text
// Properly matches: "Điều 249", "Điều 51 Bộ luật Hình sự 2025", "Điều 51 Bộ luật Hình sự 2015 (sửa đổi 2017)"
const LAW_CITATION_REGEX = /Điều\s+(\d+[A-Z]?)(?:\s+(Bộ\s+luật\s+Hình\s+sự(?:\s+\d{4})?(?:\s+\(sửa\s+đổi(?:\s+\d{4})?\))?|BLHS(?:\s+\d{4})?|BLTTHS))?/g;

export default function TrainingPage() {
  const [mode, setMode] = useState('neutral');
  const [caseDesc, setCaseDesc] = useState('');
  const [userAnalysis, setUserAnalysis] = useState('');
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  
  // Law modal state
  const [lawModal, setLawModal] = useState({
    open: false,
    loading: false,
    article: null,
    source: null,
    data: null,
    error: null,
  });

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

  const handleLawClick = async (article, source) => {
    setLawModal({
      open: true,
      loading: true,
      article,
      source,
      data: null,
      error: null,
    });

    try {
      const response = await lawsApi.getLaw(article, null, source);
      setLawModal(prev => ({
        ...prev,
        loading: false,
        data: response,
      }));
    } catch (err) {
      setLawModal(prev => ({
        ...prev,
        loading: false,
        error: err.message || 'Không thể tải luật này',
      }));
    }
  };

  const closeLawModal = () => {
    setLawModal({
      open: false,
      loading: false,
      article: null,
      source: null,
      data: null,
      error: null,
    });
  };

  // Parse law citations and make them clickable
  const renderLawCitations = (text) => {
    if (!text) return text;
    
    const parts = [];
    let lastIndex = 0;
    const matches = [...text.matchAll(LAW_CITATION_REGEX)];
    
    matches.forEach((match) => {
      const fullMatch = match[0];
      const articleNum = match[1];
      const sourceStr = match[2]?.trim() || '';
      
      // Add text before this match
      parts.push(text.substring(lastIndex, match.index));
      
      // Add clickable law citation
      parts.push(
        <button
          key={`law-${match.index}`}
          className="law-citation"
          style={{ cursor: 'pointer', textDecoration: 'underline', background: 'none', border: 'none', padding: 0 }}
          onClick={() => handleLawClick(articleNum, sourceStr)}
          title={`Xem Điều ${articleNum}`}
        >
          {fullMatch}
        </button>
      );
      
      lastIndex = match.index + fullMatch.length;
    });
    
    if (lastIndex < text.length) {
      parts.push(text.substring(lastIndex));
    }
    
    return parts.length > 0 ? parts : text;
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
                      <div key={i}>{renderLawCitations(a)}</div>
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

      {/* LAW REFERENCE MODAL */}
      {lawModal.open && (
        <div style={{
          position: 'fixed',
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          backgroundColor: 'rgba(0, 0, 0, 0.5)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          zIndex: 1000,
        }}>
          <div style={{
            backgroundColor: 'white',
            borderRadius: '8px',
            maxWidth: '600px',
            maxHeight: '80vh',
            overflowY: 'auto',
            padding: '20px',
            boxShadow: '0 4px 12px rgba(0, 0, 0, 0.15)',
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
              <h3 style={{ margin: 0 }}>
                Điều {lawModal.article} {lawModal.source && `(${lawModal.source})`}
              </h3>
              <button
                onClick={closeLawModal}
                style={{
                  background: 'none',
                  border: 'none',
                  fontSize: '24px',
                  cursor: 'pointer',
                }}
              >
                ×
              </button>
            </div>

            {lawModal.loading && (
              <div style={{ textAlign: 'center', padding: '20px' }}>
                <span className="loader" /> Đang tải...
              </div>
            )}

            {lawModal.error && (
              <div style={{ color: 'red', padding: '12px', backgroundColor: '#fee', borderRadius: '4px' }}>
                {lawModal.error}
              </div>
            )}

            {lawModal.data && lawModal.data.versions?.length > 0 && (
              <div>
                <div style={{ marginBottom: '12px', fontSize: '14px', color: '#666' }}>
                  <strong>{lawModal.data.versions[0].title}</strong>
                  {lawModal.data.versions[0].chapter && ` (Chương ${lawModal.data.versions[0].chapter})`}
                </div>
                <div style={{
                  backgroundColor: '#f5f5f5',
                  padding: '12px',
                  borderRadius: '4px',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  fontSize: '14px',
                  lineHeight: '1.6',
                }}>
                  {lawModal.data.versions[0].content}
                </div>
                {lawModal.data.versions[0].source && (
                  <div style={{ marginTop: '12px', fontSize: '12px', color: '#999', textAlign: 'right' }}>
                    Nguồn: {lawModal.data.versions[0].source}
                  </div>
                )}
              </div>
            )}

            {lawModal.data && lawModal.data.versions?.length === 0 && (
              <div style={{ padding: '12px', color: '#999' }}>
                Không tìm thấy nội dung của điều luật này.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
