import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { adminApi } from '../services/api.js';
import styles from './AdminPage.module.css';

const ROLE_LABEL = { neutral: 'Thẩm phán', defense: 'Luật sư Bào chữa', victim: 'Luật sư Bị hại' };

export default function AdminPage() {
  const navigate = useNavigate();
  const [feedback, setFeedback] = useState([]);
  const [loading, setLoading]   = useState(true);
  const [error, setError]       = useState(null);
  const [selectedId, setSelectedId] = useState(null);

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

  useEffect(() => {
    if (!selectedId && feedback.length > 0) {
      setSelectedId(feedback[0].id);
    }
  }, [feedback, selectedId]);

  const selected = feedback.find(item => item.id === selectedId) || null;
  const formatDate = (dateStr) => new Date(dateStr).toLocaleString('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh' });
  const shortCaseId = (value) => value ? `#${String(value).slice(0, 6)}` : '—';

  return (
    <div className={styles.page}>
      <aside className={styles.nav}>
        <div className={styles.navHeader}>
          <div className={styles.brandBadge}>V</div>
          <div>
            <div className={styles.brandTitle}>VNPLaw</div>
            <div className={styles.brandSub}>Legal Intelligence</div>
          </div>
        </div>
        <div className={styles.navLinks}>
          <button className={styles.navItem} type="button" onClick={() => navigate('/chat')}>
            <span className="material-symbols-outlined">chat</span>
            Trò chuyện
          </button>
          <button className={styles.navItem} type="button" onClick={() => navigate('/training')}>
            <span className="material-symbols-outlined">gavel</span>
            Chế độ Thực hành
          </button>
          <button className={`${styles.navItem} ${styles.navItemActive}`} type="button">
            <span className="material-symbols-outlined">dashboard</span>
            Bảng điều khiển Admin
          </button>
        </div>
        <div className={styles.navFooter}>
          <button className={styles.navItem} type="button">
            <span className="material-symbols-outlined">settings</span>
            Cài đặt
          </button>
          <button className={styles.navItem} type="button">
            <span className="material-symbols-outlined">help</span>
            Hỗ trợ
          </button>
        </div>
      </aside>

      <main className={styles.main}>
        <header className={styles.topbar}>
          <div className={styles.topbarLeft}>
            <div className={styles.topbarTitle}>VNPLaw Intelligence</div>
            <nav className={styles.topbarLinks}>
              <button type="button">Tài liệu</button>
              <button type="button">Lưu trữ</button>
            </nav>
          </div>
          <div className={styles.topbarRight}>
            <div className={styles.searchWrap}>
              <span className="material-symbols-outlined">search</span>
              <input className={styles.searchInput} placeholder="Tìm kiếm bản ghi..." />
            </div>
            <button className="btn btn-primary" type="button" onClick={() => navigate('/chat')}>
              Vụ án mới
            </button>
          </div>
        </header>

        <div className={styles.content}>
          <div className={styles.pageHeader}>
            <h1>Quản lý phản hồi</h1>
            <p>Xem xét và quản lý phản hồi của người dùng về tính chính xác của câu trả lời AI để cải thiện độ chính xác của hệ thống.</p>
          </div>

          {error && <div className={styles.errorBanner}>{error}</div>}

          <div className={styles.grid}>
            <section className={styles.listPanel}>
              <div className={styles.toolbar}>
                <div className={styles.sorter}>
                  <span>Sắp xếp theo:</span>
                  <select>
                    <option>Mới nhất</option>
                    <option>Cũ nhất</option>
                    <option>Trạng thái</option>
                  </select>
                </div>
                <div className={styles.toolbarActions}>
                  <button type="button"><span className="material-symbols-outlined">filter_list</span></button>
                  <button type="button"><span className="material-symbols-outlined">download</span></button>
                </div>
              </div>

              <div className={styles.listHeader}>
                <span>Mã vụ án</span>
                <span>Vai trò / Người dùng</span>
                <span className={styles.centered}>Phản hồi</span>
                <span>Đoạn bình luận</span>
              </div>

              {loading ? (
                <div className={styles.loadingState}><span className="loader" /> Đang tải phản hồi...</div>
              ) : (
                <div className={styles.listBody}>
                  {feedback.map(item => (
                    <button
                      key={item.id}
                      className={`${styles.listItem} ${selectedId === item.id ? styles.listItemActive : ''}`}
                      onClick={() => setSelectedId(item.id)}
                      type="button"
                    >
                      <span className={styles.caseId}>{shortCaseId(item.session_id || item.id)}</span>
                      <span className={styles.userCell}>
                        <strong>{ROLE_LABEL[item.session_mode] || item.session_mode || '—'}</strong>
                        <span>{item.user_name || item.user_email || 'Người dùng'}</span>
                      </span>
                      <span className={styles.centered}>
                        <span className={item.is_correct ? styles.voteGood : styles.voteBad}>
                          <span className="material-symbols-outlined">{item.is_correct ? 'thumb_up' : 'thumb_down'}</span>
                        </span>
                      </span>
                      <span className={styles.commentCell}>
                        {item.comment || 'Không có bình luận.'}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </section>

            <section className={styles.detailPanel}>
              {selected ? (
                <>
                  <div className={styles.detailHeader}>
                    <div>
                      <span className={styles.flagBadge}>Cần xem xét</span>
                      <h3>Chi tiết phản hồi</h3>
                      <p>{shortCaseId(selected.session_id || selected.id)} • {formatDate(selected.created_at)}</p>
                    </div>
                    <button type="button" className={styles.iconBtn}>
                      <span className="material-symbols-outlined">more_vert</span>
                    </button>
                  </div>

                  <div className={styles.detailBody}>
                    <div className={styles.detailUser}>
                      <div className={styles.userAvatar}>{(selected.user_name || 'TV')[0]}</div>
                      <div>
                        <p>{selected.user_name || 'Người dùng'} <span>({ROLE_LABEL[selected.session_mode] || selected.session_mode || '—'})</span></p>
                        <p>{selected.user_email || '—'}</p>
                      </div>
                    </div>

                    <div>
                      <h4>Bình luận</h4>
                      <div className={styles.commentBox}>
                        {selected.comment || 'Không có bình luận.'}
                      </div>
                    </div>

                    <div className={styles.detailMetaGrid}>
                      <div>
                        <span>Loại Prompt</span>
                        <strong>{selected.prompt_type || 'Phân tích vụ án'}</strong>
                      </div>
                      <div>
                        <span>Phiên bản mô hình</span>
                        <strong>{selected.model_version || 'v2.4.1-legal'}</strong>
                      </div>
                    </div>

                    <div className={styles.detailActions}>
                      <button className="btn btn-primary" type="button">Giải quyết</button>
                      <button className="btn btn-outline" type="button">Bỏ qua</button>
                    </div>
                  </div>
                </>
              ) : (
                <div className={styles.emptyPanel}>Chọn phản hồi để xem chi tiết.</div>
              )}
            </section>
          </div>
        </div>
      </main>
    </div>
  );
}
