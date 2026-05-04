import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import styles from './MessageBubble.module.css';
import { adminApi } from '../services/api.js';

// Regex to match Vietnamese law article citations like "Điều 51", "Điều 255"
const LAW_SPLIT_REGEX = /(Điều\s+\d+[A-Z]?(?:\s+(?:Bộ\s+luật\s+Hình\s+sự|BLHS|BLTTHS|BL[A-Z]+)(?:\s+\d{4})?(?:\s+\(sửa\s+đổi\s+\d{4}\))?)?)/g;

function highlightLawCitations(text, onLawClick, crimeDate) {
  if (!text) return text;
  const parts = text.split(LAW_SPLIT_REGEX);
  return parts.map((part, i) => {
    if (!/^Điều\s+\d+/.test(part)) return part;
    if (onLawClick) {
      const baseArticle = part.replace(/\s+(?:BLHS|BLTTHS|BL[A-Z]+|Bộ\s+luật\s+Hình\s+sự).*$/i, '').trim();
      return (
        <button
          key={i}
          className={`law-citation ${styles.inlineLawBtn}`}
          title={`Tra cứu ${part}`}
          type="button"
          onClick={() => onLawClick({ article: baseArticle }, crimeDate)}
        >
          {part}
        </button>
      );
    }
    return <span key={i} className="law-citation" title={`Tra cứu ${part}`}>{part}</span>;
  });
}

function MarkdownWithCitations({ content, onLawClick, crimeDate }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        p: ({ children }) => <p>{processChildren(children, onLawClick, crimeDate)}</p>,
        li: ({ children }) => <li>{processChildren(children, onLawClick, crimeDate)}</li>,
        strong: ({ children }) => <strong>{processChildren(children, onLawClick, crimeDate)}</strong>,
      }}
    >
      {content}
    </ReactMarkdown>
  );
}

function processChildren(children, onLawClick, crimeDate) {
  if (typeof children === 'string') return highlightLawCitations(children, onLawClick, crimeDate);
  if (Array.isArray(children)) return children.map((c, i) =>
    typeof c === 'string'
      ? <span key={i}>{highlightLawCitations(c, onLawClick, crimeDate)}</span>
      : c
  );
  return children;
}

const ROLE_LABELS = {
  defense: { label: 'Luật sư Bào chữa', cls: 'defense' },
  victim:  { label: 'Luật sư Bị hại',   cls: 'victim'  },
  neutral: { label: 'Thẩm phán',         cls: 'neutral' },
};

function parseCrimeDate(dateStr) {
  if (!dateStr) return null;
  const m = String(dateStr).match(/^(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})$/);
  if (m) return `${m[3]}-${m[2].padStart(2, '0')}-${m[1].padStart(2, '0')}`;
  if (/^\d{4}-\d{2}-\d{2}$/.test(dateStr)) return dateStr;
  return null;
}

/**
 * FeedbackBar — thumbs up/down + optional comment for an AI message.
 */
function FeedbackBar({ sessionId, messageId }) {
  const [voted, setVoted]       = useState(null);   // null | true | false
  const [showForm, setShowForm] = useState(false);
  const [comment, setComment]   = useState('');
  const [saving, setSaving]     = useState(false);
  const [done, setDone]         = useState(false);

  const submit = async (isCorrect) => {
    if (saving || done) return;
    setSaving(true);
    try {
      await adminApi.submitFeedback(sessionId, messageId, isCorrect, comment || null);
      setVoted(isCorrect);
      setDone(true);
      setShowForm(false);
    } catch (e) {
      console.error('Feedback error:', e);
    } finally {
      setSaving(false);
    }
  };

  if (done) {
    return (
      <div className={styles.feedbackBar}>
        <span className={styles.feedbackDone}>
          {voted ? '👍' : '👎'} Cảm ơn phản hồi của bạn!
        </span>
      </div>
    );
  }

  return (
    <div className={styles.feedbackBar}>
      <span className={styles.feedbackLabel}>Phản hồi hữu ích?</span>
      <button
        className={`${styles.feedbackBtn} ${voted === true ? styles.feedbackActive : ''}`}
        title="Hữu ích / Chính xác"
        type="button"
        onClick={() => submit(true)}
        disabled={saving}
      >👍</button>
      <button
        className={`${styles.feedbackBtn} ${voted === false ? styles.feedbackActive : ''}`}
        title="Không chính xác"
        type="button"
        onClick={() => { setVoted(false); setShowForm(true); }}
        disabled={saving}
      >👎</button>

      {showForm && (
        <div className={styles.feedbackForm}>
          <textarea
            className={styles.feedbackTextarea}
            placeholder="Mô tả lỗi (tuỳ chọn)..."
            value={comment}
            onChange={e => setComment(e.target.value)}
            rows={2}
          />
          <div className={styles.feedbackFormActions}>
            <button
              className={styles.feedbackSubmitBtn}
              type="button"
              onClick={() => submit(false)}
              disabled={saving}
            >
              {saving ? 'Đang gửi...' : 'Gửi phản hồi'}
            </button>
            <button
              className={styles.feedbackCancelBtn}
              type="button"
              onClick={() => { setShowForm(false); setVoted(null); }}
            >Huỷ</button>
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * MessageBubble renders a single chat message.
 *
 * Props:
 *   - message: { id, role, content, mappedLaws?, extractedFacts?, createdAt }
 *   - role: active session role ("defense" | "victim" | "neutral")
 *   - sessionId: UUID of the current chat session (for feedback)
 *   - onLawClick: (law, crimeDate, source?) => void
 */
function MessageBubble({ message, role, sessionId, onLawClick }) {
  const isUser = message.role === 'user';

  const formatTimeGMT7 = (date) =>
    new Date(date).toLocaleTimeString('vi-VN', {
      timeZone: 'Asia/Ho_Chi_Minh',
      hour: '2-digit',
      minute: '2-digit',
    });

  const crimeDate = parseCrimeDate(message.extractedFacts?.ngay_pham_toi);
  const avatarLabel = isUser ? 'Bạn' : 'Trợ lý';

  return (
    <div className={`${styles.wrapper} ${isUser ? styles.user : styles.assistant}`}>
      <div className={styles.avatar} title={avatarLabel}>
        {isUser ? 'U' : 'A'}
      </div>

      <div className={styles.bubble}>
        {!isUser && role && ROLE_LABELS[role] && (
          <span className={`badge badge-${ROLE_LABELS[role].cls} ${styles.roleBadge}`}>
            {ROLE_LABELS[role].label}
          </span>
        )}

        <div className={`${styles.content} ${isUser ? '' : 'prose'}`}>
          {isUser
            ? <p>{message.content}</p>
            : <MarkdownWithCitations content={message.content} onLawClick={onLawClick} crimeDate={crimeDate} />
          }
        </div>

        {/* Mapped-laws edition pills */}
        {!isUser && message.mappedLaws && message.mappedLaws.length > 0 && (
          <div className={styles.lawPills}>
            {message.mappedLaws
              .filter(l => !l._mapping_error && l.article && l.article !== 'N/A')
              .map((law, i) => (
                <button
                  key={i}
                  className={styles.lawPill}
                  title={`${law.offense_name || ''} — ${law.edition_reason || ''}`}
                  type="button"
                  onClick={() => onLawClick && onLawClick(
                    { article: law.article, edition_applied: law.edition_applied },
                    crimeDate,
                    law.edition_applied || null
                  )}
                >
                  <span className={styles.lawPillArticle}>{law.article} {law.clause}</span>
                  {law.edition_applied && (
                    <span className={styles.lawPillEdition}>{law.edition_applied}</span>
                  )}
                </button>
              ))
            }
          </div>
        )}

        {/* Feedback bar — only for AI messages with an id */}
        {!isUser && message.id && sessionId && (
          <FeedbackBar sessionId={sessionId} messageId={message.id} />
        )}

        <time className={styles.time}>
          {message.createdAt ? formatTimeGMT7(message.createdAt) : 'Vừa xong'}
        </time>
      </div>
    </div>
  );
}

export default MessageBubble;
