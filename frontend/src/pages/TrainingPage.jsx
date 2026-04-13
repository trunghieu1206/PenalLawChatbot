import { useState } from 'react';
import { Link } from 'react-router-dom';
import { practiceApi } from '../services/api.js';
import styles from './TrainingPage.module.css';

const MODES = [
  {
    id: 'neutral',
    label: 'Judge',
    desc: 'Objective analysis based on evidence',
    badgeClass: 'badge-neutral',
    abbr: 'JDG',
  },
  {
    id: 'defense',
    label: 'Defense Counsel',
    desc: 'Protect defendant\'s rights, reduce penalty',
    badgeClass: 'badge-defense',
    abbr: 'DEF',
  },
  {
    id: 'victim',
    label: 'Victim\'s Counsel',
    desc: 'Protect victim\'s rights, seek strict penalties',
    badgeClass: 'badge-victim',
    abbr: 'VIC',
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
    if (score >= 80) return 'Excellent';
    if (score >= 60) return 'Good';
    return 'Needs Improvement';
  };

  const selectedMode = MODES.find(m => m.id === mode);

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <Link to="/chat" className="btn btn-ghost">← Back to Chat</Link>
        <div className={styles.headerTitle}>
          Practice Mode
        </div>
        <div />
      </header>

      {/* MODE SELECTOR */}
      <div className={styles.modeBar}>
        <span className={styles.modeBarLabel}>Select Practice Role:</span>
        <div className={styles.modeOptions}>
          {MODES.map(m => (
            <button
              key={m.id}
              className={`${styles.modeOption} ${mode === m.id ? styles.modeOptionActive : ''}`}
              onClick={() => { setMode(m.id); setResult(null); setError(''); }}
              disabled={loading}
              title={m.desc}
            >
              <span className={styles.modeAbbr}>{m.abbr}</span>
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
            <h3 className={styles.panelTitle}>Case Description</h3>
            <textarea
              className={styles.textarea}
              placeholder="Paste the case file content here..."
              value={caseDesc}
              onChange={e => setCaseDesc(e.target.value)}
              rows={10}
              disabled={loading}
            />
          </div>
          <div className="card" style={{ padding: 24, marginTop: 16 }}>
            <h3 className={styles.panelTitle}>
              Your Analysis as <span className={`badge ${selectedMode.badgeClass}`}>{selectedMode.label}</span>
            </h3>
            <textarea
              className={styles.textarea}
              placeholder={`Write your legal analysis from the perspective of ${selectedMode.label}...`}
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
              {loading ? <><span className="loader" /> Evaluating...</> : 'Evaluate Analysis'}
            </button>
            {result && (
              <button
                className="btn btn-ghost"
                onClick={handleReset}
                style={{ padding: 13, fontSize: 14 }}
              >
                Reset
              </button>
            )}
          </div>
        </div>

        {/* Right: Result */}
        <div className={styles.panel}>
          {!result ? (
            <div className={styles.placeholder}>
              <p>Evaluation results will appear here after you submit your analysis.</p>
              <p style={{ fontSize: 13, opacity: 0.7, marginTop: 8 }}>
                The AI will assess each legal point and provide detailed feedback from the <strong>{selectedMode.label}</strong> perspective.
              </p>
            </div>
          ) : (
            <div className={`card ${styles.result} animate-fade-in`}>
              <div className={styles.scoreSection}>
                <div className={`${styles.score} ${getScoreClass(result.score)}`}>
                  {result.score}
                </div>
                <div>
                  <div className={styles.scoreLabel}>Your Score</div>
                  <div className={styles.scoreDesc}>{getScoreEmoji(result.score)}</div>
                  <div className={styles.scoreRole}>
                    Evaluated as: <span className={`badge ${selectedMode.badgeClass}`}>{selectedMode.label}</span>
                  </div>
                </div>
              </div>

              {result.feedback.strengths?.length > 0 && (
                <section className={styles.section}>
                  <h4 className={styles.sectionTitle} style={{ color: 'var(--success)' }}>Strengths</h4>
                  <ul className={styles.list}>
                    {result.feedback.strengths.map((s, i) => (
                      <li key={i} className={styles.listItemGood}>{s}</li>
                    ))}
                  </ul>
                </section>
              )}

              {result.feedback.improvements?.length > 0 && (
                <section className={styles.section}>
                  <h4 className={styles.sectionTitle} style={{ color: 'var(--error)' }}>Areas for Improvement</h4>
                  <ul className={styles.list}>
                    {result.feedback.improvements.map((s, i) => (
                      <li key={i} className={styles.listItemBad}>{s}</li>
                    ))}
                  </ul>
                </section>
              )}

              {result.feedback.missed_articles?.length > 0 && (
                <section className={styles.section}>
                  <h4 className={styles.sectionTitle} style={{ color: 'var(--accent)' }}>📖 Điều luật bỏ sót</h4>
                  <div className={styles.pills}>
                    {result.feedback.missed_articles.map((a, i) => (
                      <span key={i} className="law-citation">{a}</span>
                    ))}
                  </div>
                </section>
              )}

              {result.feedback.suggestion && (
                <section className={styles.section}>
                  <h4 className={styles.sectionTitle}>💡 Gợi ý</h4>
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
