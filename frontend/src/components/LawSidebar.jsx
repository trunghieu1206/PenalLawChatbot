import { useEffect, useState } from 'react';
import styles from './LawSidebar.module.css';

/**
 * Right-side sliding panel that shows the full text of a law article.
 *
 * Props:
 *   - lawData: { article_number, crime_date, found_by, versions: LawResponse[] } | null
 *   - onClose: () => void
 *   - loading: boolean
 *   - error: string | null
 */
export default function LawSidebar({ lawData, onClose, loading, error }) {
  const [activeTab, setActiveTab] = useState(0);

  // Reset tab when law changes
  useEffect(() => {
    setActiveTab(0);
  }, [lawData?.article_number]);

  const open = loading || !!lawData || !!error;

  if (!open) return null;

  const versions = lawData?.versions ?? [];
  const activeVersion = versions[activeTab] ?? null;

  const formatDate = (dateStr) => {
    if (!dateStr) return 'Không xác định';
    try {
      const d = new Date(dateStr);
      return d.toLocaleDateString('vi-VN', { day: '2-digit', month: '2-digit', year: 'numeric' });
    } catch {
      return dateStr;
    }
  };

  return (
    <>
      {/* Backdrop */}
      <div className={styles.backdrop} onClick={onClose} />

      {/* Sidebar panel */}
      <aside className={styles.sidebar}>
        {/* Header */}
        <div className={styles.header}>
          <div className={styles.headerLeft}>
            <span className={styles.headerIcon}>⚖️</span>
            <div>
              <div className={styles.headerTitle}>
                {lawData ? `Điều ${lawData.article_number}` : 'Tra cứu điều luật'}
              </div>
              {lawData?.crime_date && (
                <div className={styles.headerSub}>
                  {lawData.found_by === 'crime_date'
                    ? `Áp dụng tại thời điểm phạm tội: ${formatDate(lawData.crime_date)}`
                    : 'Phiên bản hiện hành (không có ngày phạm tội)'}
                </div>
              )}
            </div>
          </div>
          <button className={styles.closeBtn} onClick={onClose} title="Đóng">✕</button>
        </div>

        {/* Content */}
        <div className={styles.body}>
          {loading && (
            <div className={styles.loadingState}>
              <span className={styles.spinner} />
              <span>Đang tải nội dung điều luật...</span>
            </div>
          )}

          {error && !loading && (
            <div className={styles.errorState}>
              <span className={styles.errorIcon}>⚠️</span>
              <div>
                <div className={styles.errorTitle}>Không tìm thấy điều luật</div>
                <div className={styles.errorMsg}>{error}</div>
              </div>
            </div>
          )}

          {!loading && !error && lawData && versions.length === 0 && (
            <div className={styles.errorState}>
              <span className={styles.errorIcon}>📭</span>
              <div>
                <div className={styles.errorTitle}>Không có dữ liệu</div>
                <div className={styles.errorMsg}>
                  Điều {lawData.article_number} chưa có trong cơ sở dữ liệu.
                </div>
              </div>
            </div>
          )}

          {!loading && !error && versions.length > 0 && (
            <>
              {/* Version tabs (show only if multiple versions) */}
              {versions.length > 1 && (
                <div className={styles.tabs}>
                  {versions.map((v, i) => (
                    <button
                      key={v.id}
                      className={`${styles.tab} ${i === activeTab ? styles.tabActive : ''}`}
                      onClick={() => setActiveTab(i)}
                    >
                      {v.source}
                    </button>
                  ))}
                </div>
              )}

              {/* Active version */}
              {activeVersion && (
                <div className={styles.versionCard}>
                  {/* Meta strip */}
                  <div className={styles.metaStrip}>
                    <span className={styles.sourceBadge}>{activeVersion.source}</span>
                    {activeVersion.chapter && (
                      <span className={styles.metaItem}>Chương {activeVersion.chapter}</span>
                    )}
                    {activeVersion.effective_date && (
                      <span className={styles.metaItem}>
                        Hiệu lực từ: {formatDate(activeVersion.effective_date)}
                      </span>
                    )}
                    {activeVersion.effective_end_date && (
                      <span className={`${styles.metaItem} ${styles.metaExpired}`}>
                        Hết hiệu lực: {formatDate(activeVersion.effective_end_date)}
                      </span>
                    )}
                    {activeVersion.is_active === false && (
                      <span className={styles.inactiveBadge}>Hết hiệu lực</span>
                    )}
                  </div>

                  {/* Article title */}
                  {activeVersion.title && (
                    <h3 className={styles.articleTitle}>
                      Điều {activeVersion.article_number}. {activeVersion.title}
                    </h3>
                  )}

                  {/* Full text */}
                  <div className={styles.articleContent}>
                    {activeVersion.content.split('\n').map((line, i) => (
                      <p key={i}>{line}</p>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </aside>
    </>
  );
}
