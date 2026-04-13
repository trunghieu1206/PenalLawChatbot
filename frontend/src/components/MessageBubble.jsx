import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import styles from './MessageBubble.module.css';

// Regex (non-global) to match Vietnamese law article citations like "Điều 51", "Điều 255"
// BUG-06 FIX: The original code used a module-level regex with the /g flag inside .test(),
// which advances lastIndex and causes every-other citation to silently fail.
// Solution: use a non-global regex inline so there is no stale lastIndex state.
const LAW_SPLIT_REGEX = /(Điều\s+\d+[A-Z]?(?:\s+(?:BLHS|BLTTHS|BL[A-Z]+))?)/g;

/**
 * Renders inline law citations as clickable buttons when onLawClick is provided.
 * Each matched citation calls onLawClick({ article: "Điều X" }, crimeDate).
 */
function highlightLawCitations(text, onLawClick, crimeDate) {
  if (!text) return text;
  const parts = text.split(LAW_SPLIT_REGEX);
  return parts.map((part, i) => {
    if (!/^Điều\s+\d+/.test(part)) return part;

    if (onLawClick) {
      // Strip law-code suffixes ("BLHS", "BLHS 2015", "BLTTHS", etc.) before lookup
      const baseArticle = part.replace(/\s+(BLHS|BLTTHS|BL[A-Z]+)\b.*$/i, '').trim();
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
    return (
      <span key={i} className="law-citation" title={`Tra cứu ${part}`}>
        {part}
      </span>
    );
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
  victim: { label: 'Luật sư Bị hại', cls: 'victim' },
  neutral: { label: 'Thẩm phán', cls: 'neutral' },
};

/**
 * Parses a Vietnamese date string "dd/mm/yyyy" into ISO "YYYY-MM-DD".
 * Returns null if not parseable.
 */
function parseCrimeDate(dateStr) {
  if (!dateStr) return null;
  // Handle "dd/mm/yyyy" or "dd-mm-yyyy"
  const m = String(dateStr).match(/^(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})$/);
  if (m) return `${m[3]}-${m[2].padStart(2, '0')}-${m[1].padStart(2, '0')}`;
  // Already ISO "YYYY-MM-DD"?
  if (/^\d{4}-\d{2}-\d{2}$/.test(dateStr)) return dateStr;
  return null;
}

/**
 * MessageBubble renders a single chat message.
 *
 * Props:
 *   - message: { id, role, content, mappedLaws?, extractedFacts?, createdAt }
 *   - role: active session role ("defense" | "victim" | "neutral")
 *   - onLawClick: (law: MappedLaw, crimeDate: string|null) => void — called when a law pill is clicked
 */
function MessageBubble({ message, role, onLawClick }) {
  const isUser = message.role === 'user';

  // Format time in GMT+7 (Hanoi time)
  const formatTimeGMT7 = (date) => {
    const d = new Date(date);
    const offset = 7; // GMT+7
    const utc = d.getTime() + d.getTimezoneOffset() * 60000;
    const gmt7 = new Date(utc + 3600000 * offset);
    return gmt7.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' });
  };

  // Extract crime date from this message's extractedFacts
  const crimeDate = parseCrimeDate(
    message.extractedFacts?.ngay_pham_toi
  );

  const avatarLabel = isUser ? 'Bạn' : 'Trợ lý';

  const handleLawPillClick = (law) => {
    if (onLawClick) onLawClick(law, crimeDate);
  };

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
            : <MarkdownWithCitations
                content={message.content}
                onLawClick={onLawClick}
                crimeDate={crimeDate}
              />
          }
        </div>

        <time className={styles.time}>
          {message.createdAt
            ? formatTimeGMT7(message.createdAt)
            : 'Vừa xong'}
        </time>
      </div>
    </div>
  );
}

export default MessageBubble;
