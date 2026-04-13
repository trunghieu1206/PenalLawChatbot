import styles from './RoleSelector.module.css';

const ROLES = [
  {
    id: 'defense',
    label: 'Defense Counsel',
    desc: 'Protect defendant\'s rights',
    color: '#2d5f7a',
    abbr: 'DEF',
  },
  {
    id: 'neutral',
    label: 'Judge',
    desc: 'Objective legal determination',
    color: '#3d5a42',
    abbr: 'JDG',
  },
  {
    id: 'victim',
    label: 'Victim\'s Counsel',
    desc: 'Protect victim\'s rights',
    color: '#8b3a3a',
    abbr: 'VIC',
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
          <span className={styles.abbr}>{r.abbr}</span>
          <span className={styles.label}>{r.label}</span>
          <span className={styles.desc}>{r.desc}</span>
        </button>
      ))}
    </div>
  );
}
