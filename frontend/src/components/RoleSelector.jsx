import styles from './RoleSelector.module.css';

const ROLES = [
  {
    id: 'defense',
    label: 'Luật sư Bào chữa',
    desc: 'Bảo vệ quyền lợi bị cáo',
    color: '#2d5f7a',
  },
  {
    id: 'neutral',
    label: 'Thẩm phán',
    desc: 'Phán quyết khách quan dựa trên chứng cứ',
    color: '#3d5a42',
  },
  {
    id: 'victim',
    label: 'Luật sư Bị hại',
    desc: 'Bảo vệ quyền lợi nạn nhân',
    color: '#8b3a3a',
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
          <span className={styles.label}>{r.label}</span>
          <span className={styles.desc}>{r.desc}</span>
        </button>
      ))}
    </div>
  );
}
