import { useState } from 'react';
import Topbar from '../components/Topbar.jsx';
import { Link, useNavigate } from 'react-router-dom';
import { practiceApi, lawsApi } from '../services/api.js';
import styles from './TrainingPage.module.css';
import Sidebar from '../components/Sidebar.jsx';

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
  const navigate = useNavigate();
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
    <div className="bg-background text-on-background font-body-md text-body-md h-full min-h-screen flex overflow-hidden">
      <Sidebar activeTab="training" />

      <main className="ml-64 flex-1 flex flex-col h-screen bg-surface overflow-y-auto pt-16">
        <Topbar />

        <div className={styles.pageHeader}>
          <div>
            
            <h1>Chế độ Luyện tập</h1>
            <p>Cải thiện lập luận pháp lý và phân tích vụ án bằng cách đóng các vai trò khác nhau trong các kịch bản tình huống giả định.</p>
          </div>
          <div className={styles.roleSelector}>
            <span>Vai trò:</span>
            {MODES.map(m => (
              <button
                key={m.id}
                className={mode === m.id ? styles.roleActive : styles.roleBtn}
                onClick={() => { setMode(m.id); setResult(null); setError(''); }}
                disabled={loading}
                type="button"
              >
                {m.label}
              </button>
            ))}
          </div>
        </div>

        <div className={styles.grid}>
          <div className={styles.leftColumn}>
            <section className={styles.card}>
              <div className={styles.cardHeader}>
                <h2>Nội dung Vụ án</h2>
                <div className={styles.cardActions}>
                  <button type="button" title="Nhập tài liệu">
                    <span className="material-symbols-outlined">upload_file</span>
                  </button>
                  <button type="button" title="Tải mẫu">
                    <span className="material-symbols-outlined">description</span>
                  </button>
                </div>
              </div>
              <div className={styles.cardBody}>
                <textarea
                  className="textarea"
                  placeholder="Dán hoặc nhập các tình tiết chi tiết của vụ án, dòng thời gian và các bên liên quan tại đây..."
                  value={caseDesc}
                  onChange={e => setCaseDesc(e.target.value)}
                  rows={8}
                  disabled={loading}
                />
                <div className={styles.charRow}>
                  <span>Nhập chi tiết đầy đủ để có kết quả đánh giá chính xác hơn.</span>
                  <span>{caseDesc.length} ký tự</span>
                </div>
              </div>
            </section>

            <section className={styles.card}>
              <div className={styles.cardHeader}>
                <h2>Phân tích của bạn</h2>
              </div>
              <div className={styles.cardBody}>
                <textarea
                  className="textarea"
                  placeholder={`Phác thảo chiến lược pháp lý theo góc nhìn ${selectedMode.label}...`}
                  value={userAnalysis}
                  onChange={e => setUserAnalysis(e.target.value)}
                  rows={10}
                  disabled={loading}
                />
                {error && <div className={styles.error}>{error}</div>}
                <div className={styles.cardFooter}>
                  
                  <button className="btn btn-primary" type="button" onClick={handleSubmit} disabled={loading}>
                    {loading ? <><span className="loader" /> Đang đánh giá...</> : 'Đánh giá'}
                  </button>
                </div>
              </div>
            </section>
          </div>

          <div className={styles.rightColumn}>
            {!result ? (
              <section className={styles.cardEmpty}>
                <div className={styles.cardHeader}>
                  <h2>Kết quả đánh giá</h2>
                </div>
                <div className={styles.emptyBody}>
                  <h3>Chưa có dữ liệu đánh giá</h3>
                  <p>Nhập "Nội dung Vụ án" và "Phân tích của bạn", sau đó nhấn "Bắt đầu đánh giá" để hệ thống AI phân tích lập luận pháp lý của bạn.</p>
                </div>
              </section>
            ) : (
              <section className={`${styles.card} ${styles.resultCard}`}>
                <div className={styles.cardHeader}>
                  <h2>Kết quả đánh giá</h2>
                </div>
                <div className={styles.resultBody}>
                  <div className={styles.scoreRow}>
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
                      <h4 className={styles.sectionTitle}>Điểm mạnh</h4>
                      <ul className={styles.list}>
                        {result.feedback.strengths.map((s, i) => (
                          <li key={i} className={styles.listItemGood}>{s}</li>
                        ))}
                      </ul>
                    </section>
                  )}

                  {result.feedback.improvements?.length > 0 && (
                    <section className={styles.section}>
                      <h4 className={styles.sectionTitle}>Cần cải thiện</h4>
                      <ul className={styles.list}>
                        {result.feedback.improvements.map((s, i) => (
                          <li key={i} className={styles.listItemBad}>{s}</li>
                        ))}
                      </ul>
                    </section>
                  )}

                  {result.feedback.missed_articles?.length > 0 && (
                    <section className={styles.section}>
                      <h4 className={styles.sectionTitle}>Điều luật bỏ sót</h4>
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
              </section>
            )}
          </div>
        </div>
      </main>

      {lawModal.open && (
        <div className={styles.lawModalOverlay} onClick={closeLawModal}>
          <div className={styles.lawModal} onClick={(e) => e.stopPropagation()}>
            <div className={styles.lawModalHeader}>
              <h3>Điều {lawModal.article} {lawModal.source && `(${lawModal.source})`}</h3>
              <button type="button" onClick={closeLawModal}>
                <span className="material-symbols-outlined">close</span>
              </button>
            </div>

            {lawModal.loading && (
              <div className={styles.lawModalLoading}>
                <span className="loader" /> Đang tải...
              </div>
            )}

            {lawModal.error && (
              <div className={styles.lawModalError}>
                {lawModal.error}
              </div>
            )}

            {lawModal.data && lawModal.data.versions?.length > 0 && (
              <div className={styles.lawModalBody}>
                <div className={styles.lawModalMeta}>
                  <strong>{lawModal.data.versions[0].title}</strong>
                  {lawModal.data.versions[0].chapter && ` (Chương ${lawModal.data.versions[0].chapter})`}
                </div>
                <div className={styles.lawModalContent}>
                  {lawModal.data.versions[0].content}
                </div>
                {lawModal.data.versions[0].source && (
                  <div className={styles.lawModalSource}>
                    Nguồn: {lawModal.data.versions[0].source}
                  </div>
                )}
              </div>
            )}

            {lawModal.data && lawModal.data.versions?.length === 0 && (
              <div className={styles.lawModalEmpty}>Không tìm thấy nội dung của điều luật này.</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
