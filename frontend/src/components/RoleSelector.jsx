import styles from './RoleSelector.module.css';

const ROLES = [
  {
    id: 'defense',
    icon: '🛡️',
    label: 'Luật sư Bào chữa',
    desc: 'Bảo vệ quyền lợi bị cáo',
    color: '#6366f1',
  },
  {
    id: 'neutral',
    icon: '⚖️',
    label: 'Thẩm phán / Trung lập',
    desc: 'Phán quyết khách quan',
    color: '#10b981',
  },
  {
    id: 'victim',
    icon: '🔴',
    label: 'Luật sư Bị hại',
    desc: 'Bảo vệ quyền lợi nạn nhân',
    color: '#ef4444',
  },
];

export default function RoleSelector({ selected, onChange }) {
  return (
    <div className={styles.wrapper}>
      {ROLES.map((r) => (
        <button
          key={r.id}
          className={`${styles.role} ${selected === r.id ? styles.active : ''}`}
          style={{ '--role-color': r.color }}
          onClick={() => onChange(r.id)}
          type="button"
          aria-pressed={selected === r.id}
        >
          <span className={styles.icon}>{r.icon}</span>
          <span className={styles.label}>{r.label}</span>
          <span className={styles.desc}>{r.desc}</span>
        </button>
      ))}
    </div>
  );
}
