import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import styles from './MessageBubble.module.css';

// Regex (non-global) to match Vietnamese law article citations like "Điều 51", "Điều 255"
// BUG-06 FIX: The original code used a module-level regex with the /g flag inside .test(),
// which advances lastIndex and causes every-other citation to silently fail.
// Solution: use a non-global regex inline so there is no stale lastIndex state.
const LAW_SPLIT_REGEX = /(Điều\s+\d+[A-Z]?(?:\s+(?:BLHS|BLTTHS|BL[A-Z]+))?)/g;

function highlightLawCitations(text) {
  if (!text) return text;
  const parts = text.split(LAW_SPLIT_REGEX);
  return parts.map((part, i) =>
    /^Điều\s+\d+/.test(part)
      ? <span key={i} className="law-citation" title={`Tra cứu ${part}`}>{part}</span>
      : part
  );
}

function MarkdownWithCitations({ content }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        p: ({ children }) => <p>{processChildren(children)}</p>,
        li: ({ children }) => <li>{processChildren(children)}</li>,
        strong: ({ children }) => <strong>{processChildren(children)}</strong>,
      }}
    >
      {content}
    </ReactMarkdown>
  );
}

function processChildren(children) {
  if (typeof children === 'string') return highlightLawCitations(children);
  if (Array.isArray(children)) return children.map((c, i) =>
    typeof c === 'string' ? <span key={i}>{highlightLawCitations(c)}</span> : c
  );
  return children;
}

const ROLE_LABELS = {
  defense: { label: 'Luật sư Bào chữa', cls: 'defense' },
  victim: { label: 'Luật sư Bị hại', cls: 'victim' },
  neutral: { label: 'Thẩm phán', cls: 'neutral' },
};

export default function MessageBubble({ message, role }) {
  const isUser = message.role === 'user';

  return (
    <div className={`${styles.wrapper} ${isUser ? styles.user : styles.assistant}`}>
      <div className={styles.avatar}>
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
            : <MarkdownWithCitations content={message.content} />
          }
        </div>

        {message.mappedLaws && message.mappedLaws.length > 0 && (
          <div className={styles.lawsTag}>
            <span className={styles.lawsLabel}>Điều luật áp dụng:</span>
            {message.mappedLaws.map((l, i) => (
              <span key={i} className={`${styles.lawPill} law-citation`}>
                {l.article} {l.clause}
              </span>
            ))}
          </div>
        )}

        <time className={styles.time}>
          {message.createdAt
            ? new Date(message.createdAt).toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' })
            : 'Vừa xong'}
        </time>
      </div>
    </div>
  );
}
