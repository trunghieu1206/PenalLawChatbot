import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth.jsx';
import { authApi } from '../services/api.js';
import styles from './Auth.module.css';

export default function LoginPage() {
  const [form, setForm] = useState({ email: '', password: '' });
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const { login } = useAuth();
  const navigate = useNavigate();

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const data = await authApi.login(form);
      login(data);
      navigate('/chat');
    } catch (err) {
      setError(err.response?.data?.message || 'Đăng nhập thất bại');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className={styles.page}>
      <div className={styles.bg} />
      <div className={`${styles.container} card animate-fade-in`}>
        <div className={styles.logo}>
          <span className={styles.logoIcon}>⚖️</span>
          <h1 className={styles.logoText}>LegalAI</h1>
          <p className={styles.logoSub}>Trợ lý Pháp luật Hình sự Việt Nam</p>
        </div>

        <form onSubmit={handleSubmit} className={styles.form}>
          <div className={styles.field}>
            <label className="label" htmlFor="email">Email</label>
            <input
              id="email"
              type="email"
              className="input"
              placeholder="example@email.com"
              value={form.email}
              onChange={e => setForm(f => ({ ...f, email: e.target.value }))}
              required
            />
          </div>
          <div className={styles.field}>
            <label className="label" htmlFor="password">Mật khẩu</label>
            <input
              id="password"
              type="password"
              className="input"
              placeholder="••••••••"
              value={form.password}
              onChange={e => setForm(f => ({ ...f, password: e.target.value }))}
              required
            />
          </div>

          {error && <div className={styles.error}>{error}</div>}

          <button type="submit" className={`btn btn-primary ${styles.submitBtn}`} disabled={loading}>
            {loading ? <span className="loader" /> : 'Đăng nhập'}
          </button>
        </form>

        <p className={styles.footer}>
          Chưa có tài khoản? <Link to="/register" className={styles.link}>Đăng ký ngay</Link>
        </p>
      </div>
    </div>
  );
}
