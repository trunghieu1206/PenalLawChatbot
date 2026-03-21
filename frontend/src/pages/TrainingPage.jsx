import { useState } from 'react';
import { Link } from 'react-router-dom';
import styles from './TrainingPage.module.css';

export default function TrainingPage() {
  const [caseDesc, setCaseDesc] = useState('');
  const [userAnalysis, setUserAnalysis] = useState('');
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async () => {
    if (!caseDesc.trim() || !userAnalysis.trim()) {
      setError('Vui lòng điền đầy đủ thông tin vụ án và phân tích của bạn.');
      return;
    }
    setLoading(true);
    setError('');
    setResult(null);
    try {
      // TODO: wire to POST /api/training/evaluate
      await new Promise(r => setTimeout(r, 2000));
      setResult({
        score: 72,
        feedback: {
          strengths: ['Xác định đúng tội danh chính', 'Phân tích tình tiết giảm nhẹ hợp lý'],
          improvements: ['Bỏ sót tình tiết tăng nặng "có tổ chức"', 'Chưa kiểm tra độ tuổi nạn nhân', 'Thiếu trích dẫn Điều 55 khi tổng hợp hình phạt'],
          missedArticles: ['Điều 52 BLHS (Tình tiết tăng nặng)', 'Điều 55 BLHS (Tổng hợp hình phạt)'],
          suggestion: 'Cần đọc kỹ lại các bước lượng hình trước khi đưa ra phán quyết cuối cùng.',
        }
      });
    } catch (err) {
      setError('Đánh giá thất bại. Vui lòng thử lại.');
    } finally {
      setLoading(false);
    }
  };

  const getScoreClass = (score) => {
    if (score >= 80) return styles.scoreHigh;
    if (score >= 60) return styles.scoreMid;
    return styles.scoreLow;
  };

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <Link to="/chat" className="btn btn-ghost">← Quay lại Chat</Link>
        <div className={styles.headerTitle}>
          <span>🎓</span> Chế độ Luyện tập
        </div>
        <div />
      </header>

      <div className={styles.body}>
        {/* Left: Input */}
        <div className={styles.panel}>
          <div className="card" style={{ padding: 24 }}>
            <h3 className={styles.panelTitle}>📋 Nội dung Vụ án</h3>
            <textarea
              className={styles.textarea}
              placeholder="Dán nội dung hồ sơ vụ án vào đây..."
              value={caseDesc}
              onChange={e => setCaseDesc(e.target.value)}
              rows={10}
            />
          </div>
          <div className="card" style={{ padding: 24, marginTop: 16 }}>
            <h3 className={styles.panelTitle}>✍️ Phân tích của bạn</h3>
            <textarea
              className={styles.textarea}
              placeholder="Viết phân tích pháp lý của bạn về vụ án trên..."
              value={userAnalysis}
              onChange={e => setUserAnalysis(e.target.value)}
              rows={10}
            />
          </div>
          {error && <div className={styles.error}>{error}</div>}
          <button
            className="btn btn-primary"
            onClick={handleSubmit}
            disabled={loading}
            style={{ width: '100%', justifyContent: 'center', marginTop: 12, padding: 13, fontSize: 15 }}
          >
            {loading ? <><span className="loader" /> Đang đánh giá...</> : '⚡ Đánh giá phân tích'}
          </button>
        </div>

        {/* Right: Result */}
        <div className={styles.panel}>
          {!result ? (
            <div className={styles.placeholder}>
              <span className={styles.placeholderIcon}>📊</span>
              <p>Kết quả đánh giá sẽ hiển thị ở đây sau khi bạn gửi phân tích.</p>
            </div>
          ) : (
            <div className={`card ${styles.result} animate-fade-in`}>
              <div className={styles.scoreSection}>
                <div className={`${styles.score} ${getScoreClass(result.score)}`}>
                  {result.score}
                </div>
                <div>
                  <div className={styles.scoreLabel}>Điểm số của bạn</div>
                  <div className={styles.scoreDesc}>
                    {result.score >= 80 ? '🏆 Xuất sắc!' : result.score >= 60 ? '📈 Khá tốt!' : '📚 Cần cải thiện'}
                  </div>
                </div>
              </div>

              <section className={styles.section}>
                <h4 className={styles.sectionTitle} style={{ color: 'var(--success)' }}>✅ Điểm mạnh</h4>
                <ul className={styles.list}>
                  {result.feedback.strengths.map((s, i) => <li key={i}>{s}</li>)}
                </ul>
              </section>

              <section className={styles.section}>
                <h4 className={styles.sectionTitle} style={{ color: 'var(--error)' }}>⚠️ Cần cải thiện</h4>
                <ul className={styles.list}>
                  {result.feedback.improvements.map((s, i) => <li key={i}>{s}</li>)}
                </ul>
              </section>

              {result.feedback.missedArticles?.length > 0 && (
                <section className={styles.section}>
                  <h4 className={styles.sectionTitle} style={{ color: 'var(--accent)' }}>📖 Điều luật bỏ sót</h4>
                  <div className={styles.pills}>
                    {result.feedback.missedArticles.map((a, i) => (
                      <span key={i} className="law-citation">{a}</span>
                    ))}
                  </div>
                </section>
              )}

              <section className={styles.section}>
                <h4 className={styles.sectionTitle}>💡 Gợi ý</h4>
                <p className={styles.suggestion}>{result.feedback.suggestion}</p>
              </section>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
